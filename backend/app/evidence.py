"""Verify headline claims and reconcile explicitly structured evidence before composition."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.domain import display_value

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


@dataclass
class Metric:
    label: str
    observations: dict[str, Decimal]
    currency: str = ""
    unit: str = ""

    def format(self, amount: Decimal) -> str:
        sign = "−" if amount < 0 else ""
        return sign + display_value(abs(amount), self.currency) + self.unit


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


def matching_metric(metrics: list[Metric], label: str) -> Metric | None:
    matches = sorted((m for m in metrics if contains(label, m.label)), key=lambda m: -len(m.label))
    if not matches:
        return None
    selected = matches[0]
    for other in matches[1:]:
        if key(other.label) != key(selected.label):
            continue
        if any(selected.observations[p] != other.observations[p] for p in selected.observations.keys() & other.observations.keys()):
            return None
    return selected


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


def comparison_headline(data, sources: list[str]) -> str:
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
        for series, row in zip(data.series, rows):
            series.values = list(map(float, row))
        data.value_prefix = first.currency
        low = 0 if first.total < second.total else 1
        smaller, larger = (first, second) if low == 0 else (second, first)
        percent = ((larger.total - smaller.total) / larger.total * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        if percent:
            return f"{data.categories[low]} running costs are {percent}% lower"
    return ""


def chart_headline(chart, metrics: list[Metric]) -> str:
    metric = matching_metric(metrics, chart.label)
    categories = [category.upper() for category in chart.categories]
    if not metric or len(categories) < 2 or any(category not in metric.observations for category in categories):
        return ""
    numbers = [metric.observations[category] for category in categories]
    chart.values = list(map(float, numbers))
    if chart.chart != "line" or not all(re.fullmatch(QUARTER, category) for category in categories):
        return ""
    label = metric.label[0].upper() + metric.label[1:]
    last, previous = numbers[-1], numbers[-2]
    if len(numbers) >= 3 and numbers[-3] > previous < last:
        return f"{label} rebounds to {metric.format(last)} in {categories[-1]}"
    if last != previous:
        verb = "rises" if last > previous else "falls"
        return f"{label} {verb} to {metric.format(last)} in {categories[-1]}"
    return f"{label} holds at {metric.format(last)} in {categories[-1]}"


def reconcile_table(rows, metrics: list[Metric]) -> None:
    if not rows or len(rows) < 2:
        return
    categories = [column.upper() for column in rows[0][1:]]
    if not categories or not all(re.fullmatch(QUARTER, category) for category in categories):
        return
    for row in rows[1:]:
        if len(row) == len(categories) + 1 and (metric := matching_metric(metrics, row[0])):
            for index, category in enumerate(categories, 1):
                if category in metric.observations:
                    row[index] = display_value(metric.observations[category])


def reconcile_readout(readout, metrics: list[Metric]) -> str:
    results = {}
    for point in readout.metrics:
        period = re.search(rf"\b{QUARTER}\b", point.title, re.I)
        metric = matching_metric(metrics, point.title)
        if not period or not metric or period[0].upper() not in metric.observations:
            continue
        quarter = period[0].upper()
        amount = metric.observations[quarter]
        point.value = metric.format(amount)
        # The evidence can describe quarter-to-quarter movement without relying on generated prose.
        previous = f"Q{int(quarter[1]) - 1}"
        if previous in metric.observations:
            change = amount - metric.observations[previous]
            if change:
                point.body = f"{'Up' if change > 0 else 'Down'} from {metric.format(metric.observations[previous])} in {previous}."
            else:
                point.body = f"Unchanged from {previous}."
        else:
            point.body = ""
        results.setdefault(metric.label, {})[quarter] = amount, point.value
    for label, observations in results.items():
        ordered = sorted(observations.items())
        if len(ordered) >= 2:
            first, last = ordered[0], ordered[-1]
            if first[1][0] < 0 < last[1][0]:
                metric_name = label[:1].upper() + label[1:]
                return f"{metric_name} rebounds from {first[1][1]} to {last[1][1]}"
    return ""


def resolve_headline(part, content, prompt: str, sources, *, strict_copy: bool = False):
    """Reconcile supported observations and choose a grounded display headline."""
    evidence = [prompt, *(source.excerpt for source in sources if source.id in content.source_ids and source.excerpt != prompt)]
    metrics = [metric for source in evidence for metric in periods(source)]
    derived = ""
    if content.stacked_comparison:
        derived = comparison_headline(content.stacked_comparison, evidence)
        if derived:
            content.subtitle = ""  # The chart, callout and caption carry the verified detail.
    if content.chart:
        derived = chart_headline(content.chart, metrics) or derived
        if derived:
            content.subtitle = "Illustrative figures" if contains(prompt, "illustrative") else ""
    if content.table:
        reconcile_table(content.table, metrics)
    if content.readout:
        derived = reconcile_readout(content.readout, metrics) or derived
        if derived and contains(prompt, "illustrative"):
            content.subtitle = "Illustrative figures"
    if strict_copy:
        # Brief-only decks cannot claim a feature applies universally without a source saying so.
        for point in content.points:
            if not any(contains(source, "universal compatibility") for source in evidence):
                point.body = re.sub(r"\s+(?:for|with) universal compatibility.*?(?=\.|$)", "", point.body, flags=re.I)
    explicit = part.layout in {"cover", "section", "quote"} or (part.lock_title and contains(prompt, part.title)) or any(
        key(part.title) == key(match[1]) for match in SCOPED_TITLE.finditer(prompt)
    )
    headline = part.title
    if not explicit:
        for proposal in (derived, part.key_message, part.title):
            if proposal and (proposal == derived or not NUMERIC_CLAIM.search(proposal) or
                             any(contains(source, proposal) for source in evidence)):
                headline = proposal
                break
        else:
            neutral = part.topic or re.sub(r"\s+\d+\s*$", "", part.title).strip()
            headline = neutral if not NUMERIC_CLAIM.search(neutral) else "Evidence and findings"
    content.title = headline
    return part.model_copy(update={"title": headline, "key_message": "" if explicit else headline})
