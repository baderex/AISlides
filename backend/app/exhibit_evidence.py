"""Value-level provenance for semantic exhibits."""

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import re
from typing import Literal

from pydantic import Field, ValidationError

from app.domain import Chart, Contract, Identifier, Text
from app.evidence import CURRENCY_HINTS, NUMBER, NUMERIC_CLAIM, UNIT_HINTS, contains, key, value, verified_observations
from app.visuals import GraphVisual, MatrixVisual, QuantitativeVisual, ScatterVisual, ScheduleVisual, weighted_average


class ValueBinding(Contract):
    path: str
    label: str
    value: Decimal | str
    status: Literal["verified", "illustrative", "derived", "unverified"]
    source_observation_id: Identifier | None = None
    source_id: Identifier | None = None
    calculation: Literal["sum", "share", "weighted-average", "min", "max", "difference", "absolute-difference"] | None = None
    inputs: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ExhibitValue:
    path: str
    metric: str
    category: str
    value: float | str
    unit: str = ""


UNVERIFIED = "Exhibit data: unverified"
ILLUSTRATIVE = "Exhibit data: illustrative"
VERIFIED = "Exhibit data: source verified"
DERIVED = "Exhibit data: source verified and calculated"
STATUS_KEY = "exhibit:evidence"
EVIDENCE_LABELS = {UNVERIFIED, ILLUSTRATIVE, VERIFIED, DERIVED}


def values_for(v):
    if isinstance(v, QuantitativeVisual):
        if v.type == "slope":
            periods = v.series or [v.start_label, v.end_label]
            for i, row in enumerate(v.rows):
                for j, amount in enumerate(row.values):
                    yield ExhibitValue(f"rows/{i}/values/{j}", row.label, periods[j], amount, v.unit)
            return
        if v.baseline is not None and v.type in {"waterfall", "tornado"}:
            yield ExhibitValue("baseline", v.metric, v.start_label if v.type == "waterfall" else "Baseline",
                               v.baseline, v.unit)
        for i, row in enumerate(v.rows):
            for j, amount in enumerate(row.values):
                metric = (v.series[j] if v.series else ("Low", "High")[j]) if len(row.values) > 1 else v.metric
                unit = v.segment_unit if v.type == "mekko" else v.unit
                yield ExhibitValue(f"rows/{i}/values/{j}", metric, row.label, amount, unit)
            if v.type == "mekko":
                yield ExhibitValue(f"rows/{i}/width", "Category size", row.label, row.width, v.unit)
    elif isinstance(v, MatrixVisual):
        for i, row in enumerate(v.rows):
            for j, cell in enumerate(row.cells):
                if not isinstance(cell, str) or (not v.levels and NUMERIC_CLAIM.search(cell)):
                    unit = v.column_units[j] if v.column_units else v.unit
                    yield ExhibitValue(f"rows/{i}/cells/{j}", v.columns[j], row.label, cell, unit)
        for i, amount in enumerate(v.weights):
            yield ExhibitValue(f"weights/{i}", "Weight", v.columns[i], amount)
    elif isinstance(v, ScatterVisual) and not v.points[0].quadrant:
        for i, point in enumerate(v.points):
            for field, metric in (("horizontal", v.horizontal_label), ("vertical", v.vertical_label), ("size", v.size_label)):
                if field != "size" or v.type == "bubble-chart":
                    unit = getattr(v, f"{field}_unit")
                    yield ExhibitValue(f"points/{i}/{field}", metric, point.label, getattr(point, field),
                                       v.unit if unit is None else unit)
        for field, metric in (("horizontal_split", v.horizontal_label), ("vertical_split", v.vertical_label)):
            if (amount := getattr(v, field)) is not None:
                unit = getattr(v, field.replace("_split", "_unit"))
                yield ExhibitValue(field, metric, "Threshold", amount, v.unit if unit is None else unit)
    elif isinstance(v, GraphVisual) and v.type == "sankey":
        names = {node.id: node.label for node in v.nodes}
        for i, edge in enumerate(v.edges):
            yield ExhibitValue(f"edges/{i}/weight", edge.label or "Flow",
                               f"{names[edge.source]} to {names[edge.target]}", edge.weight, v.unit)
    elif isinstance(v, ScheduleVisual):
        for i, amount in enumerate(v.boundaries):
            yield ExhibitValue(f"boundaries/{i}", "Time boundary", v.periods[i] if i < len(v.periods) else "End", amount, v.unit)


def chart_values(chart):
    for i, series in enumerate(chart.series):
        for j, (category, amount) in enumerate(zip(chart.categories, series.values)):
            yield ExhibitValue(f"series/{i}/{j}", series.name, category, amount, chart.value_prefix + chart.value_suffix)


def normalized_unit(unit):
    unit = "".join(unit.casefold().split())
    for symbol, currency in CURRENCY_HINTS.items():
        if unit.startswith(currency.lower()):
            unit = symbol + unit[len(currency):]
    return unit


def quote_supports_amount(row):
    # Check the amount's own notation so another value in the source cannot
    # supply its currency or scale. Explicit unit headers may be expanded in
    # the extracted quote only when that exact text is present in the source.
    flat = " ".join(row.quote.split())
    metric = rf"(?<!\w){re.escape(row.metric)}(?!\w)"
    period = rf"(?<!\w){re.escape(row.period)}(?!\w)"
    joint = r"\s*(?:(?:in|for|at|during|as of|by|of)\s+|\s*[:;|,–—]\s*)?"
    comparison = r"\s*(?:(?:was|were|is|are|stood at|totaled|totalled)\s+|\s*[:=|,–—]\s*)?"
    amount_pattern = rf"(?P<amount>(?:\b(?:USD|EUR|GBP|JPY)\s+)?{NUMBER})(?!\d|[.,]\d|[eE][+-]?\d)"
    markers = [r"bn|[kmb]|millions?|billions?|thousands?|percent(?:age)?"]
    if row.unit:
        markers.insert(0, re.escape(row.unit))
    unit_pattern = rf"\s*(%|(?:{'|'.join(markers)})(?!\w))"
    anchors = (rf"(?:{metric}{joint}{period}|{period}{joint}{metric}){comparison}{amount_pattern}",
               rf"{metric}{comparison}{amount_pattern}(?:{unit_pattern})?\s*(?:in|for|at|during)\s+{period}")
    amounts = [match for pattern in anchors for match in re.finditer(pattern, flat, re.I)
               if value(match["amount"]) == row.amount]
    for match in amounts:
        symbol = next((char for char in CURRENCY_HINTS if char in match["amount"]), "")
        prefix = re.match(r"(USD|EUR|GBP|JPY)\b", match["amount"], re.I)
        if prefix and not symbol:
            symbol = normalized_unit(prefix[1])
        suffix = re.match(unit_pattern, flat[match.end("amount"):], re.I)
        unit = suffix[1].lower() if suffix else ""
        for short, pattern in UNIT_HINTS.items():
            if re.fullmatch(pattern, unit):
                unit = short
        if symbol + unit == normalized_unit(row.currency + row.unit):
            return True
    return False


def observation_id(row):
    return "obs-" + sha256(row.model_dump_json().encode()).hexdigest()[:28]


def exhibit_bindings(content, sources, source_ids, chart=None, planned_annotations=()):
    visual = content.visual
    values = list(chart_values(chart) if chart else values_for(visual))
    if not values:
        return []
    observations = [row for row in verified_observations(content.exhibit_observations, sources) if quote_supports_amount(row)]
    bindings = {}
    for entry in values:
        amount = entry.value if isinstance(entry.value, str) else Decimal(str(entry.value))
        matches = [row for row in observations if entry.metric and key(row.metric) == key(entry.metric)
                   and key(row.period) == key(entry.category)]
        families = {(row.amount, normalized_unit(row.currency + row.unit), key(row.scope)) for row in matches}
        match = next((row for row in matches if row.source_id in source_ids and (
            not row.scope or contains(f"{content.title} {content.subtitle} {entry.category}", row.scope)
        )), None) if len(families) == 1 else None
        if not (match and not isinstance(amount, str) and match.amount == amount
                and normalized_unit(match.currency + match.unit) == normalized_unit(entry.unit)):
            match = None
        status = "illustrative" if content.illustrative_exhibit else "verified" if match else "unverified"
        bindings[entry.path] = ValueBinding(path=entry.path, label=" / ".join(filter(None, (entry.metric, entry.category))),
                                            value=amount, status=status, source_id=match.source_id if match else None,
                                            source_observation_id=observation_id(match) if match else None)

    def derived(path, label, calculation, inputs):
        amounts = [bindings[k].value for k in inputs]
        match calculation:
            case "sum":
                result = sum(amounts)
            case "share":
                result = sum(amounts[:1]) / sum(amounts[1:])
            case "weighted-average":
                scores, weights = amounts[::2], amounts[1::2]
                result = weighted_average(scores, weights)
            case "min":
                result = min(amounts)
            case "max":
                result = max(amounts)
            case "difference":
                result = amounts[0] - amounts[1]
            case "absolute-difference":
                result = abs(amounts[0] - amounts[1])
        bindings[path] = ValueBinding(path=path, label=label, value=result, status="derived",
                                      calculation=calculation, inputs=inputs)

    if chart:
        if chart.chart == "stacked_column":
            totals = [f"totals/{j}" for j in range(len(chart.categories))]
            for j, (category, path) in enumerate(zip(chart.categories, totals)):
                derived(path, f"{category} total", "sum", [f"series/{i}/{j}" for i in range(len(chart.series))])
            if len(totals) == 2:
                derived("absolute-difference", "Absolute difference", "absolute-difference", totals)
                largest = max(totals, key=lambda path: bindings[path].value)
                if bindings[largest].value:
                    derived("relative-difference", "Difference as share of larger total", "share",
                            ["absolute-difference", largest])
        elif content.chart and chart.highlight is not None and content.chart.comparison_label(chart.highlight):
            derived("difference", "Change from comparison category", "difference",
                    [f"series/0/{chart.highlight}", f"series/0/{content.chart.compare_to}"])
    elif isinstance(visual, QuantitativeVisual):
        paths = [f"rows/{i}/values/0" for i in range(len(visual.rows))]
        if visual.type == "waterfall":
            for i, row in enumerate(visual.rows):
                derived(f"running-total/{i}", f"Total after {row.label}", "sum", ["baseline", *paths[:i + 1]])
            derived("ending-total", visual.end_label, "sum", ["baseline", *paths])
        elif visual.type in {"stacked-bar", "mekko"}:
            for i, row in enumerate(visual.rows):
                cells = [f"rows/{i}/values/{j}" for j in range(len(row.values))]
                if visual.type == "stacked-bar":
                    derived(f"totals/{i}", f"{row.label} total", "sum", cells)
                else:
                    for j, cell in enumerate(cells):
                        derived(f"shares/{i}/{j}", f"{row.label} / {visual.series[j]} share", "share", [cell, *cells])
        elif visual.type == "pareto":
            ordered = sorted(range(len(paths)), key=lambda i: visual.rows[i].values[0], reverse=True)
            for i, row_index in enumerate(ordered):
                total_path = f"cumulative/{row_index}"
                label = visual.rows[row_index].label
                derived(total_path, f"{label} cumulative value", "sum", [paths[j] for j in ordered[:i + 1]])
                derived(f"shares/{row_index}", f"{label} cumulative share", "share", [total_path, *paths])
    elif isinstance(visual, MatrixVisual):
        weights = [f"weights/{j}" for j in range(len(visual.weights))]
        for j, weight in enumerate(weights):
            derived(f"weight-shares/{j}", f"{visual.columns[j]} weight share", "share", [weight, *weights])
        if weights:
            for i, row in enumerate(visual.rows):
                inputs = [path for j, weight in enumerate(weights) for path in (f"rows/{i}/cells/{j}", weight)]
                derived(f"scores/{i}", f"{row.label} weighted score", "weighted-average", inputs)
        elif visual.type == "heatmap" and not visual.levels:
            cells = list(bindings)
            derived("range/min", "Minimum score", "min", cells)
            derived("range/max", "Maximum score", "max", cells)
    elif isinstance(visual, ScatterVisual):
        for axis in ("horizontal", "vertical"):
            inputs = [f"points/{i}/{axis}" for i in range(len(visual.points))]
            if f"{axis}_split" in bindings:
                inputs.append(f"{axis}_split")
            for bound in ("min", "max"):
                derived(f"{axis}/{bound}", f"{axis.title()} axis {bound}", bound, inputs)
    elif isinstance(visual, GraphVisual) and visual.type == "sankey":
        for node in visual.nodes:
            incoming = [f"edges/{i}/weight" for i, edge in enumerate(visual.edges) if edge.target == node.id]
            outgoing = [f"edges/{i}/weight" for i, edge in enumerate(visual.edges) if edge.source == node.id]
            for flow, links in (("in", incoming), ("out", outgoing)):
                if links:
                    derived(f"nodes/{node.id}/{flow}", f"{node.label} flow {flow}", "sum", links)
            if incoming and outgoing:
                derived(f"nodes/{node.id}/balance", f"{node.label} net flow", "difference",
                        [f"nodes/{node.id}/in", f"nodes/{node.id}/out"])
    # Free-form copy can make claims independently of the structured inputs.
    # Flag such figures when they lack a value binding of their own.
    if visual:
        prose = {}
        if content.decorations:
            prose.update({f"decorations/{field}": getattr(content.decorations, field) for field in
                          ("key_metric", "delta", "status_label", "callout", "footnote")})
        for i, note in enumerate(content.annotations or planned_annotations):
            prose[f"annotations/{i}"] = note if isinstance(note, str) else note.text
        for path, text in prose.items():
            if NUMERIC_CLAIM.search(text):
                bindings[path] = ValueBinding(path=path, label=path, value=text,
                                              status="illustrative" if content.illustrative_exhibit else "unverified")
    return list(bindings.values())


def evidence_label(bindings):
    statuses = {binding.status for binding in bindings}
    if "unverified" in statuses:
        return UNVERIFIED
    if "illustrative" in statuses:
        return ILLUSTRATIVE
    if "derived" in statuses:
        return DERIVED
    return VERIFIED if statuses else ""


def display_evidence(document, slide):
    """Recheck provenance and the rendered values when previewing or exporting saved slides."""
    spec = slide.spec
    chart = next((el for el in slide.elements if isinstance(el, Chart)), None)
    if (not chart or not (spec or slide.source_ids)) and (not spec or not spec.content.get("visual")):
        return slide
    from app.composition import SlideContent

    try:
        content = SlideContent.model_validate(spec.content) if spec else SlideContent(title=slide.title)
    except ValidationError:
        return with_badge(document, slide, UNVERIFIED)
    bindings = exhibit_bindings(content, document.sources, slide.source_ids, chart)
    if not bindings:
        return slide
    elements = [el for el in slide.elements if el.element_key != STATUS_KEY]
    if any(el.element_key == "exhibit:missing-data" for el in elements):
        return slide
    label = evidence_label(bindings)
    if label in {VERIFIED, DERIVED}:
        # Manual formatting can be preserved. Changes to visible values or
        # the geometry that encodes them require a fresh semantic render.
        by_id = {el.id: el for el in elements}
        changed = any(el.id not in spec.generated_elements and (isinstance(el, Chart) or
                      (isinstance(el, Text) and NUMERIC_CLAIM.search(el.text))) for el in elements)
        for identity, baseline in spec.generated_elements.items():
            if baseline.get("text") in EVIDENCE_LABELS:
                continue
            el = by_id.get(identity)
            if not el:
                changed = True
                break
            fields = {"text", "rows", "polygon"} & baseline.keys()
            if el.type == "chart":
                fields |= {"series", "categories", "value_prefix", "value_suffix"} & baseline.keys()
            if content.visual and el.type in {"shape", "line"} and (
                el.element_key or "").startswith(f"{content.visual.type}:"):
                fields |= {"x", "y", "w", "h", "rotation", "shape", "weight"} & baseline.keys()
            if el.model_dump(mode="json", include=fields) != {field: baseline[field] for field in fields}:
                changed = True
                break
        if changed or spec.value_bindings != [binding.model_dump(mode="json") for binding in bindings]:
            label = UNVERIFIED
    return with_badge(document, slide, label)


def with_badge(document, slide, label):
    from app.design import readable

    badges = [el for el in slide.elements if isinstance(el, Text) and el.element_key == STATUS_KEY]
    existing = next(iter(badges), None)
    if len(badges) == 1 and existing.text == label and not existing.edited:
        return slide
    if existing and slide.spec and (baseline := slide.spec.generated_elements.get(existing.id)):
        badge = Text.model_validate({**baseline, "id": existing.id, "element_key": STATUS_KEY,
                                     "text": label, "spans": []})
    else:
        badge = Text(x=.06, y=.966, w=.84, h=.031, text=label, size=11, bold=True, z=1000,
                     element_key=STATUS_KEY, color=readable(document.theme.ink, slide.background),
                     font=document.theme.font)
    elements = [el for el in slide.elements if el.element_key != STATUS_KEY]
    return slide.model_copy(update={"elements": [*elements, badge]})


def evidence_notes(document, slide):
    badge = next((el for el in slide.elements if isinstance(el, Text) and el.element_key == STATUS_KEY), None)
    if not badge:
        return ""
    from app.composition import SlideContent

    try:
        content = SlideContent.model_validate(slide.spec.content) if slide.spec else SlideContent(title=slide.title)
    except ValidationError:
        return badge.text
    chart = next((el for el in slide.elements if isinstance(el, Chart)), None)
    records = exhibit_bindings(content, document.sources, slide.source_ids, chart)
    lines = [badge.text, "Semantic exhibit inputs and calculations"]
    if badge.text == UNVERIFIED and evidence_label(records) in {VERIFIED, DERIVED}:
        lines.append("The displayed exhibit differs from its verified generated version. These records describe the underlying data.")
    for row in records:
        detail = f"{row.source_id} / {row.source_observation_id}" if row.source_id else (
            f"{row.calculation}({', '.join(row.inputs)})" if row.calculation else "")
        lines.append(f"{row.path} · {row.label} = {row.value} [{row.status}]" + (f" · {detail}" if detail else ""))
    used = {row.source_observation_id for row in records if row.source_observation_id}
    for observation in content.exhibit_observations:
        if (identifier := observation_id(observation)) in used:
            lines.append(f"{identifier} · {observation.quote}")
    return "\n".join(lines)
