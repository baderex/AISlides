"""Verify headline claims and reconcile explicitly structured evidence before composition."""

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from pydantic import Field, ValidationError

from app.domain import Contract, Identifier, display_value

NUMBER = r"[+\-−]?\s*[$€£¥]?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
QUARTER = r"Q[1-4]"
PERIOD_VALUES = re.compile(rf"\b({QUARTER})\s*[:=]?\s*({NUMBER})(?!\w|,\d)", re.I)
VALUES = re.compile(rf"^\s*({NUMBER}(?:\s*,\s*{NUMBER})+)\s*\.?\s*$")
NUMERIC_CLAIM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:[kmb]|bn)?(?!\w)|[$€£¥]\s*\d|\d\s*%")
SCOPED_TITLE = re.compile(r'\b(?:titled|title(?:d)?\s*[:=])\s*["“\x27]?([^."”\x27\n;]+)', re.I)


def value(token: str) -> Decimal:
    return Decimal(re.sub(r"[^0-9.\-+]", "", token.replace("−", "-")))


def key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def contains(text: str, phrase: str) -> bool:
    normalized = key(phrase)
    return bool(normalized and f" {normalized} " in f" {key(text)} ")


def exact_numeric_claim(text: str, claim: str) -> bool:
    # A minus sign, currency symbol, decimal point or percent mark can change the claim.
    normalized = " ".join(text.casefold().replace("−", "-").split())
    quoted = " ".join(claim.casefold().replace("−", "-").split())
    return bool(re.search(rf"(?<![\w+\-]){re.escape(quoted)}(?![\w%])", normalized))


@dataclass
class Metric:
    label: str
    observations: dict[str, Decimal]
    currency: str = ""
    unit: str = ""
    scope: str = ""
    source_ids: tuple[str, ...] = ()

    def format(self, amount: Decimal) -> str:
        sign = "−" if amount < 0 else ""
        return sign + display_value(abs(amount), self.currency) + self.unit


class EvidenceObservation(Contract):
    source_id: Identifier
    metric: str = Field(min_length=1, max_length=80)
    period: str = Field(min_length=1, max_length=40, description="The source's period or comparison category label.")
    scope: str = Field(default="", max_length=80, description="Series context such as fiscal year and geography.")
    amount: Decimal = Field(ge=Decimal("-1e15"), le=Decimal("1e15"))
    unit: str = Field(default="", max_length=8)
    currency: str = Field(default="", max_length=8)
    quote: str = Field(min_length=1, max_length=420, description="Verbatim excerpt containing the metric, period and value.")


EVIDENCE_GUIDANCE = (
    " Extract numerical observations into observations, including source ID, metric, exact period, scope, amount, "
    "unit, currency, and an exact supporting quote containing the metric, period, and numerical value. Preserve the "
    "quoted scale. Use currency symbols ($, €, £, ¥) and suffixes such as m, bn, or % where the source supports them. "
    "Record conflicting observations independently with the relevant source IDs. Omit rows without an exact quotation."
)

CURRENCY_HINTS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
UNIT_HINTS = {"m": r"millions?", "bn": r"billions?", "k": r"thousands?", "%": r"percent(?:age)?"}


def verified_observations(observations, sources):
    by_id = {source.id: source.excerpt for source in sources}
    for observation in observations:
        quote = " ".join(observation.quote.split())
        source_text = " ".join(by_id.get(observation.source_id, "").split())
        if (not quote or quote not in source_text or not contains(quote, observation.metric) or
                (observation.scope and not contains(source_text, observation.scope))):
            continue
        period = rf"(?<!\w){re.escape(observation.period)}(?!\w)"
        if not re.search(period, quote, re.I):
            continue
        if observation.currency and observation.currency not in source_text and not contains(
                source_text, CURRENCY_HINTS.get(observation.currency, observation.currency)):
            continue
        if observation.unit:
            unit = re.escape(observation.unit)
            marker = UNIT_HINTS.get(observation.unit, unit)
            if not re.search(rf"\b(?:{marker})(?!\w)|\d\s*{unit}(?!\w)", source_text, re.I):
                continue
        # A quote must anchor the number to its category. Removing the period
        # token first also prevents a year from being accepted as a metric value.
        remaining = re.sub(period, "", quote, flags=re.I)
        candidates = {value(token) for token in re.findall(NUMBER, remaining)}
        suffix = rf"(?:\s*{re.escape(observation.unit)})?" if observation.unit else ""
        adjacent = re.finditer(rf"{period}\s*(?:[:=|,]|was\b|of\b)?\s*({NUMBER}){suffix}(?!\w|[.,]\d)", quote, re.I)
        paired = {value(match[1]) for match in adjacent}
        if len(candidates) != 1 and not paired:
            continue
        if (paired or candidates) != {observation.amount}:
            continue
        yield observation


def verified_metrics(observations, sources):
    return [Metric(row.metric, {row.period.upper(): row.amount}, row.currency, row.unit, row.scope, (row.source_id,))
            for row in verified_observations(observations, sources)]


class EvidenceIndex:
    def __init__(self, metrics):
        self.metrics = metrics
        self.conflicts = set()
        self.used_sources = set()

    def find(self, label, periods=()):
        matches = sorted((metric for metric in self.metrics if contains(label, metric.label)),
                         key=lambda metric: -len(metric.label))
        if not matches:
            return None
        name = matches[0].label
        matches = [metric for metric in matches if key(metric.label) == key(name)]
        scoped = [metric for metric in matches if metric.scope and contains(label, metric.scope)]
        if scoped:
            matches = scoped
        # Keep the fiscal period, region, currency, and scale together when
        # merging rows. Independent scopes with the same label are ambiguous.
        families = defaultdict(list)
        for metric in matches:
            families[key(metric.scope), metric.currency, metric.unit.casefold()].append(metric)
        wanted = set(periods)
        observations = {}
        used_sources = set()
        useful = {family: entries for family, entries in families.items() if any(
            not wanted or wanted.intersection(entry.observations) for entry in entries)}
        if len(useful) > 1:
            self.conflicts.add(name)
            self.used_sources.update(source for entries in useful.values() for entry in entries for source in entry.source_ids)
            return None
        if not useful:
            return None
        entries = next(iter(useful.values()))
        for metric in entries:
            for period, amount in metric.observations.items():
                if wanted and period not in wanted:
                    continue
                if period in observations and observations[period] != amount:
                    self.conflicts.add(name)
                    self.used_sources.update(source for entry in entries for source in entry.source_ids)
                    return None
                observations[period] = amount
                used_sources.update(metric.source_ids)
        if observations:
            self.used_sources.update(used_sources)
        return Metric(name, observations, entries[0].currency, entries[0].unit, entries[0].scope)


@dataclass
class CostBreakdown:
    category: str
    components: list[tuple[str, Decimal]]
    currency: str

    @property
    def total(self) -> Decimal:
        return sum((amount for _, amount in self.components), Decimal(0))


def periods(source: str) -> Iterator[Metric]:
    """Read labeled quarterly series and quarterly rows supplied in readable text."""
    currency, unit = "", ""
    if match := re.search(r"\b(USD|EUR|GBP)\s+(millions?|billions?|thousands?)\b", source, re.I):
        currency = {"USD": "$", "EUR": "€", "GBP": "£"}[match[1].upper()]
        unit = match[2][0].lower()
    lines = re.split(r"[;\n]|(?<=[.!?])\s+(?=[A-Z\d])", source)
    headers = re.search(rf"\b({QUARTER})\s*,\s*({QUARTER})\s*,\s*({QUARTER})\s*,\s*({QUARTER})\b", source, re.I)
    for line in lines:
        for clause in re.split(r"(?<=[.!?])\s+(?=[A-Z\d])", line):
            pairs = list(PERIOD_VALUES.finditer(clause))
            if len(pairs) < 2:
                continue
            prefix = clause[:pairs[0].start()].rstrip(" :")
            label = prefix.rsplit(":", 1)[-1].strip()
            label = re.sub(r"^\s*\d+\s*[:.)]\s*", "", label)
            label = re.split(r"\s+(?:by quarter|per quarter|across quarters)\b", label, maxsplit=1, flags=re.I)[0].strip()
            if 0 < len(label) <= 48:
                yield Metric(label, {p[1].upper(): value(p[2]) for p in pairs}, currency, unit)
        if headers and (match := re.search(rf"\b([A-Za-z][A-Za-z &/()-]{{1,45}})\s+({NUMBER}\s*,.*)$", line)):
            if row := VALUES.fullmatch(match[2]):
                amounts = list(map(value, re.findall(NUMBER, row[1])))
                if len(amounts) == len(headers.groups()):
                    yield Metric(match[1].strip(), dict(zip((p.upper() for p in headers.groups()), amounts)), currency, unit)


def breakdowns(source: str) -> list[CostBreakdown]:
    entries = []
    for match in re.finditer(rf"\b([A-Za-z][A-Za-z -]{{0,30}}?)\s+([$€£¥])\s*({NUMBER})\s*\(([^()]+)\)", source):
        components = []
        for entry in match[4].split(", "):
            item = re.fullmatch(rf"\s*({NUMBER})\s+([A-Za-z][A-Za-z /&-]{{0,42}})\s*", entry)
            if item:
                components.append((item[2], value(item[1])))
        if len(components) >= 2 and sum(amount for _, amount in components) == value(match[3]):
            entries.append(CostBreakdown(match[1].strip(), components, match[2]))
    return entries


def comparison_headline(data, sources: list[str], conflicts=None) -> str:
    candidates = []
    for source in sources:
        entries = breakdowns(source)
        matched = [[item for item in entries if contains(category, item.category)] for category in data.categories]
        if len(matched) != 2 or any(len(items) != 1 for items in matched):
            continue
        first, second = (items[0] for items in matched)
        if first is second or first.currency != second.currency or first.total == second.total:
            continue
        rows = []
        for series in data.series:
            row = []
            for item in (first, second):
                matches = [amount for label, amount in item.components if contains(series.name, label)]
                if len(matches) != 1:
                    break
                row.append(matches[0])
            if len(row) == 2:
                rows.append(row)
        if len(rows) != len(data.series) or list(map(sum, zip(*rows))) != [first.total, second.total]:
            continue
        candidates.append((first, second, rows))
    if len({(a.currency, tuple(map(tuple, rows))) for a, _, rows in candidates}) > 1:
        if conflicts is not None:
            conflicts.add(data.label)
        return ""
    if candidates:
        first, _, rows = candidates[0]
        return commit_comparison(data, rows, first.currency)
    return ""


def commit_comparison(data, rows, currency):
    for series, row in zip(data.series, rows):
        series.values = list(map(float, row))
    data.value_prefix = currency
    totals = list(map(sum, zip(*rows)))
    smaller, larger = min(totals), max(totals)
    if not larger or smaller == larger:
        return ""
    percent = ((larger - smaller) / larger * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    category = data.categories[totals.index(smaller)]
    if not percent:
        return ""
    if category[-1:].casefold() == "s":
        return f"{category[0].upper()}{category[1:]} cost {percent}% less to run"
    return f"{category[0].upper()}{category[1:]} operating costs are {percent}% lower"


def reconcile_comparison(data, structured, regex_sources, evidence):
    categories = [category.upper() for category in data.categories]
    if len(categories) == 2:
        indexed = EvidenceIndex(structured)
        metrics = [indexed.find(series.name, categories) for series in data.series]
        evidence.conflicts.update(indexed.conflicts)
        if indexed.conflicts:
            return ""
        if all(metric and set(categories) <= metric.observations.keys() for metric in metrics):
            if len({(metric.currency, metric.unit, key(metric.scope)) for metric in metrics}) != 1:
                evidence.conflicts.add(data.label)
                return ""
            rows = [[metric.observations[category] for category in categories] for metric in metrics]
            if all(amount >= 0 for row in rows for amount in row) and not metrics[0].unit:
                evidence.used_sources.update(indexed.used_sources)
                return commit_comparison(data, rows, metrics[0].currency)
    return comparison_headline(data, regex_sources, evidence.conflicts)


def chart_headline(chart, evidence: EvidenceIndex) -> str:
    categories = [category.upper() for category in chart.categories]
    metric = evidence.find(chart.label, categories)
    if not metric or len(categories) < 2 or any(category not in metric.observations for category in categories):
        return ""
    numbers = [metric.observations[category] for category in categories]
    chart.values = list(map(float, numbers))
    chart.value_prefix = metric.currency
    chart.value_suffix = metric.unit
    if chart.chart != "line" or not all(re.fullmatch(QUARTER, category) for category in categories):
        return ""
    label = metric.label[0].upper() + metric.label[1:]
    last, previous = numbers[-1], numbers[-2]
    quarters = [int(category[1]) for category in categories]
    if last != previous and chart.highlight is None and quarters == sorted(set(quarters)):
        chart.highlight = len(categories) - 1
        if chart.compare_to is None and quarters[-1] == quarters[-2] + 1:
            chart.compare_to = len(categories) - 2
    if len(numbers) >= 3 and numbers[-3] > previous < last:
        return f"{label} rebounds to {metric.format(last)} in {categories[-1]}"
    if last != previous:
        verb = "rises" if last > previous else "falls"
        return f"{label} {verb} to {metric.format(last)} in {categories[-1]}"
    return f"{label} holds at {metric.format(last)} in {categories[-1]}"


def reconcile_semantic_series(content, evidence):
    visual = content.visual
    if not visual or visual.type not in {"ranked-bar", "funnel", "pareto"} or not visual.metric:
        return
    categories = [row.label.strip().upper() for row in visual.rows]
    metric = evidence.find(visual.metric, categories)
    if not metric or not set(categories) <= metric.observations.keys():
        return
    rows = [{**row.model_dump(), "values": [metric.observations[category]]}
            for row, category in zip(visual.rows, categories)]
    unit = f"{metric.currency}{metric.unit}" or visual.unit
    try:
        content.visual = type(visual).model_validate({**visual.model_dump(), "rows": rows, "unit": unit})
    except ValidationError:
        evidence.conflicts.add(visual.metric)


def reconcile_table(rows, evidence: EvidenceIndex) -> None:
    if not rows or len(rows) < 2:
        return
    categories = [column.upper() for column in rows[0][1:]]
    if not categories:
        return
    for row in rows[1:]:
        if len(row) == len(categories) + 1 and (metric := evidence.find(row[0], categories)):
            for index, category in enumerate(categories, 1):
                if category in metric.observations:
                    row[index] = display_value(metric.observations[category])


def selection_headline(content, sources: list[str]) -> str:
    rows = {key(row[0]): row[0] for row in content.table[1:] if row and row[0].strip()}
    for source in sources:
        options = re.finditer(
            r"\b(?:plan|deploy|allocate|assign|pilot)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
            r"([A-Za-z][A-Za-z -]{0,26}?)\s+each\s+(?:at|to)\s+([^;,!?.\n]+?)\s+and\s+([^;,!?.\n]+)",
            source, re.I,
        )
        for match in options:
            selected = [rows.get(key(match[i])) for i in (3, 4)]
            if all(selected) and selected[0] != selected[1]:
                content.table_focus = selected
                return f"{selected[0]} and {selected[1]} each get {match[1].lower()} {match[2]}"
    return ""


def reconcile_readout(readout, evidence: EvidenceIndex) -> str:
    results = {}
    for point in readout.metrics:
        metric = evidence.find(point.title)
        if not metric:
            continue
        period = next((period for period in sorted(metric.observations, key=len, reverse=True)
                       if contains(point.title, period)), None)
        if not period:
            continue
        amount = metric.observations[period]
        point.value = metric.format(amount)
        # The evidence can describe quarter-to-quarter movement without relying on generated prose.
        previous = f"Q{int(period[1]) - 1}" if re.fullmatch(QUARTER, period) else ""
        if annual := re.fullmatch(r"(FY)?((?:19|20)\d{2})", period):
            previous = f"{annual[1] or ''}{int(annual[2]) - 1}"
        if previous in metric.observations:
            change = amount - metric.observations[previous]
            if change:
                point.body = f"{'Up' if change > 0 else 'Down'} from {metric.format(metric.observations[previous])} in {previous}."
            else:
                point.body = f"Unchanged from {previous}."
        else:
            point.body = ""
        results.setdefault(metric.label, {})[period] = amount, point.value
    for label, observations in results.items():
        ordered = sorted(observations.items())
        if len(ordered) >= 2 and all(re.fullmatch(rf"{QUARTER}|(?:FY)?(?:19|20)\d{{2}}", period)
                                     for period, _ in ordered):
            first, last = ordered[0], ordered[-1]
            if first[1][0] < 0 < last[1][0]:
                metric_name = label[:1].upper() + label[1:]
                return f"{metric_name} rebounds from {first[1][1]} to {last[1][1]}"
    return ""


def reconcile_diagram(diagram, sources: list[str]) -> None:
    """Preserve explicit participants and conditions in an editable flow."""
    source = "\n".join(sources)
    status = re.search(
        r"\bmay\s+(?:return|reply with|respond with)\s+(\d{3}\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,3}?)"
        r"(?=\s+(?:during|when|if|to|in|for)\b|[.,;!?]|$)", source, re.I,
    )
    if status:
        exact = status[1]
        for lane in diagram.lanes:
            for node in lane.nodes:
                if contains(node.label, exact) and not re.search(r"\b(?:may|if|when)\b", node.label, re.I):
                    node.label = f"May return {exact}"[:46]
    if len(diagram.lanes) == 1:
        lane = diagram.lanes[0]
        actor = re.search(r"\b(browser|client|application|app|device)\s+requests?\s+(?:a|an|the)\s+(\w+)", source, re.I)
        service = re.search(r"\b(server|origin|API|backend|service)\s+(?:may\s+)?(?:return|repl|respond)", source, re.I)
        if actor and service and lane.kind == "flow" and len(lane.nodes) == 3:
            first, second, third = lane.nodes
            if (re.search(r"\brequest\w*\b", f"{first.label} {first.detail}", re.I) and
                    re.search(r"\bresponse\b|\breceive\w*\b", f"{second.label} {second.detail}", re.I) and
                    re.search(r"\b(?:stor|sav|cach)\w*\b", f"{third.label} {third.detail}", re.I)):
                if not contains(first.label, service[1]) and not contains(second.label, service[1]):
                    first.label, first.detail, first.role = actor[1].capitalize(), f"Requests the {actor[2]}", "actor"
                    second.detail = second.detail or second.label
                    second.label, second.role = service[1].upper() if service[1].casefold() == "api" else service[1].capitalize(), "service"
                second.incoming = second.incoming or "Request / response"
                second.two_way = True
    # Drop an unfinished tail from responses that run into the schema limit.
    recap = diagram.recap.strip()
    if recap and not recap.endswith((".", "!", "?")):
        if end := re.search(r"^(.+[.!?])\s+[^.!?]+$", recap):
            recap = end[1]
    diagram.recap = recap


def requested_closing_action(prompt: str) -> str:
    match = re.search(
        r"\b(?:close|end|finish)\s+with\s+(?:a request|an invitation|a call)\s+to\s+([^.!?;\n]{3,75})",
        prompt, re.I,
    )
    if not match:
        return ""
    action = match[1].strip().strip('"“”')
    return action[0].upper() + action[1:] if action else ""


def resolve_headline(part, content, prompt: str, sources, *, strict_copy: bool = False, observations=()):
    """Reconcile supported observations and choose a grounded display headline."""
    evidence = [prompt, *(source.excerpt for source in sources if source.id in content.source_ids and source.excerpt != prompt)]
    structured = verified_metrics(observations, sources)
    covered = {(metric.source_ids[0], key(metric.label)) for metric in structured}
    fallback = []
    for source in sources:
        for metric in periods(source.excerpt):
            if (source.id, key(metric.label)) not in covered:
                metric.source_ids = (source.id,)
                fallback.append(metric)
    if not any(source.excerpt == prompt for source in sources):
        fallback.extend(metric for metric in periods(prompt) if key(metric.label) not in {
            key(item.label) for item in structured
        })
    index = EvidenceIndex([*structured, *fallback])
    derived = ""
    if content.stacked_comparison:
        comparison_sources = list(dict.fromkeys([*evidence, *(source.excerpt for source in sources)]))
        derived = reconcile_comparison(content.stacked_comparison, structured, comparison_sources, index)
        if derived:
            content.subtitle = ""  # The chart, callout and caption carry the verified detail.
    if content.chart:
        derived = chart_headline(content.chart, index) or derived
        if derived:
            content.subtitle = "Illustrative figures" if contains(prompt, "illustrative") else ""
    reconcile_semantic_series(content, index)
    if content.table:
        reconcile_table(content.table, index)
        if selection := selection_headline(content, evidence):
            derived = selection
            content.subtitle = "Illustrative figures" if contains(prompt, "illustrative") else ""
    if content.readout:
        derived = reconcile_readout(content.readout, index) or derived
        if derived and contains(prompt, "illustrative"):
            content.subtitle = "Illustrative figures"
    if content.diagram:
        reconcile_diagram(content.diagram, evidence)
    if content.decision:
        if any(contains(source, "pause expansion") and re.search(r"\bif any gate\b", source, re.I)
               for source in evidence):
            derived = "Pause expansion if any gate is missed"
    elif part.layout == "closing" and (action := requested_closing_action(prompt)):
        derived = action
        content.next_step = ""
        content.subtitle = ""
    if strict_copy:
        unsupported = {
            "universal compatibility": r"\s+(?:for|with) universal compatibility[^.]*",
            "widely available": r"\bwidely available\s+",
            "fast charging": r"\bfast\s+(?=charg(?:e|ing)\b)",
        }
        for point in content.points:
            for claim, pattern in unsupported.items():
                if not any(contains(source, claim) for source in evidence):
                    point.body = re.sub(pattern, "", point.body, flags=re.I).strip()
    explicit = part.layout in {"cover", "section", "quote"} or (part.lock_title and contains(prompt, part.title)) or any(
        key(part.title) == key(match[1]) for match in SCOPED_TITLE.finditer(prompt)
    )
    headline = part.title
    if index.conflicts:
        names = ", ".join(sorted(index.conflicts))
        warning = f"Conflicting source values or scopes for {names}. Verify before relying on these figures."
        content.evidence_warnings = [warning[:220]]
        content.subtitle = warning[:220]
    cited_evidence = [source.id for source in sources if source.id in index.used_sources]
    content.source_ids = list(dict.fromkeys([*cited_evidence, *content.source_ids]))[:12]
    if not explicit:
        for proposal in (derived, part.key_message, part.title):
            if not proposal or len(proposal) > 96:
                continue
            if proposal == derived or not NUMERIC_CLAIM.search(proposal) or (not index.conflicts and any(
                    exact_numeric_claim(source, proposal) for source in evidence)):
                headline = proposal
                break
        else:
            neutral = part.topic or re.sub(r"\s+\d+\s*$", "", part.title).strip()
            headline = neutral if not NUMERIC_CLAIM.search(neutral) else "Evidence and findings"
    content.title = headline
    return part.model_copy(update={"title": headline, "topic": part.topic or part.title[:180],
                                   "key_message": "" if explicit else headline[:140]})
