from types import SimpleNamespace
from decimal import Decimal

import pytest

from app.composition import OutlineItem, SlideContent
from app.domain import Chart, Slide, Source
from app.evidence import EvidenceIndex, EvidenceObservation, Metric, metric_base, verified_observations
from app.exhibit_evidence import exhibit_bindings
from app.source_sections import web_passage
from app.workflow import review_slide_content, slide_inputs


def observation(source, metric, quote, **changes):
    return EvidenceObservation(source_id=source.id, metric=metric, period="2025", amount=7,
                               currency=changes.pop("currency", "$"), unit=changes.pop("unit", "m"),
                               quote=quote, **changes)


@pytest.mark.parametrize("header,before,after,cell", [
    ("Revenue", "", "", "$7m"),
    ("Revenue (USD millions)", "", "", "7"),
    ("Revenue ($m)", "", "", "7"),
    ("Revenue", "All figures are in USD millions.", "", "7"),
    ("Revenue", "", "Notes: All figures are in USD millions.", "7"),
    ("Revenue", "# Annual results (USD millions)", "", "7"),
    ("Revenue (millions)", "Currency: USD", "", "7"),
    ("Revenue", "Currency: USD; Units: m", "", "7"),
])
def test_table_denominations_verify_the_same_chart_value(header, before, after, cell):
    quote = f"| 2025 | {cell} |"
    source = Source(id="report", title="Annual results", excerpt=(
        f"{before}\n| Year | {header} |\n| --- | --- |\n{quote}\n\n{after}"))
    row = observation(source, header, quote)
    assert list(verified_observations([row], [source])) == [row]
    content = SlideContent(title="Annual results", source_ids=[source.id], exhibit_observations=[row])
    chart = Chart(x=.1, y=.1, w=.8, h=.6, categories=["2025"], series=[{"name": header, "values": [7]}],
                  value_prefix="$", value_suffix="m")
    binding, = exhibit_bindings(content, [source], [source.id], chart)
    assert binding.status == "verified" and binding.source_id == source.id


@pytest.mark.parametrize("header,caption,cell", [
    ("Revenue (USD millions)", "All figures are in EUR millions.", "7"),
    ("Revenue (USD millions)", "All figures are in USD billions.", "7"),
    ("Revenue (USD millions)", "", "€7m"),
    ("Revenue (USD millions)", "", "$7bn"),
    ("Revenue (USD/EUR millions)", "", "7"),
    ("Revenue (USD millions or billions)", "", "7"),
    ("Revenue", "", "7"),
    ("Revenue", "A USD study discusses millions of customers.", "7"),
])
def test_conflicting_or_missing_denominations_are_not_verified(header, caption, cell):
    quote = f"| 2025 | {cell} |"
    source = Source(id="report", title="Annual results", excerpt=(
        f"{caption}\n| Year | {header} |\n| --- | --- |\n{quote}"))
    assert not list(verified_observations([observation(source, header, quote)], [source]))


def test_metric_headers_remain_exact_and_each_column_keeps_its_own_denomination():
    quote = "| 2025 | 7 | 7 |"
    source = Source(id="report", title="Annual results", excerpt=(
        "| Year | Revenue (USD millions) | Profit (EUR thousands) |\n| --- | --- | --- |\n" + quote))
    revenue = observation(source, "Revenue (USD millions)", quote)
    profit = observation(source, "Profit (EUR thousands)", quote, currency="€", unit="k")
    assert list(verified_observations([revenue, profit], [source])) == [revenue, profit]
    for wrong in (revenue.model_copy(update={"metric": "Revenue"}), revenue.model_copy(update={"unit": ""}),
                  revenue.model_copy(update={"currency": "€"}), revenue.model_copy(update={"amount": 7000000})):
        assert not list(verified_observations([wrong], [source]))


@pytest.mark.parametrize("header,unit", [("Series B", "bn"), ("Plan B cost", "bn"), ("Model T count", "tn")])
def test_letters_in_metric_names_do_not_supply_a_scale(header, unit):
    quote = "| 2025 | 7 |"
    source = Source(id="report", title="Results", excerpt=f"| Year | {header} |\n| --- | --- |\n{quote}")
    invented_scale = observation(source, header, quote, currency="", unit=unit)
    assert not list(verified_observations([invented_scale], [source]))
    unscaled = invented_scale.model_copy(update={"unit": ""})
    assert list(verified_observations([unscaled], [source])) == [unscaled]


def test_scopes_disambiguate_identical_rows_without_leaking_units_between_tables():
    quote = "| 2025 | 7 |"
    source = Source(id="report", title="Regional results", excerpt=(
        f"# North region\nAll figures are in USD millions.\n| Year | Revenue |\n| --- | --- |\n{quote}\n\n"
        f"# South region\nAll figures are in EUR thousands.\n| Year | Revenue |\n| --- | --- |\n{quote}"))
    north = observation(source, "Revenue", quote, scope="North region")
    south = observation(source, "Revenue", quote, currency="€", unit="k", scope="South region")
    assert list(verified_observations([north, south], [source])) == [north, south]
    assert not list(verified_observations([north.model_copy(update={"scope": ""})], [source]))


def test_a_previous_tables_footnote_does_not_denominate_the_next_table():
    source = Source(id="report", title="Results", excerpt=(
        "| Year | Revenue |\n| --- | --- |\n| 2024 | $7m |\n\nNotes: All figures are in USD millions.\n\n"
        "| Year | Revenue |\n| --- | --- |\n| 2025 | 7 |"))
    invented = observation(source, "Revenue", "| 2025 | 7 |")
    assert not list(verified_observations([invented], [source]))
    unscaled = invented.model_copy(update={"currency": "", "unit": ""})
    assert list(verified_observations([unscaled], [source])) == [unscaled]


def test_the_next_tables_caption_does_not_denominate_the_previous_table():
    source = Source(id="report", title="Results", excerpt=(
        "| Year | Revenue |\n| --- | --- |\n| 2025 | 7 |\n\nAll figures are in EUR millions.\n"
        "| Year | Profit |\n| --- | --- |\n| 2025 | 7 |"))
    revenue = observation(source, "Revenue", "| 2025 | 7 |", currency="€")
    profit = observation(source, "Profit", "| 2025 | 7 |", currency="€")
    assert not list(verified_observations([revenue], [source]))
    assert list(verified_observations([profit], [source])) == [profit]


def monthly_prices():
    heading = "# Monthly price history, January 2023–December 2025"
    caption = "Prices in USD. Volume in shares."
    header = "| Month | Open | High | Low | Close | Volume |"
    separator = "| --- | --- | --- | --- | --- | --- |"
    rows = [f"| {year}-{month:02} | $120.00 | $140.00 | $110.00 | $142.00 | 12,000,000 |"
            for year in (2023, 2024, 2025) for month in range(1, 13)]
    return "\n".join([heading, caption, header, separator, *rows]), rows


@pytest.mark.parametrize("budget", [2000, 1600, 600])
def test_late_requested_months_keep_complete_rows_headers_and_context(budget):
    text, rows = monthly_prices()
    query = "Plot closing prices for October, November and December 2025."
    passage = web_passage(text, query, budget)
    assert len(passage) <= budget
    assert all(row in passage for row in rows[-3:])
    assert "# Monthly price history, January 2023–December 2025" in passage
    assert "Prices in USD. Volume in shares." in passage
    assert "| Month | Open | High | Low | Close | Volume |\n| --- | --- | --- | --- | --- | --- |" in passage
    assert "other rows omitted" in passage
    assert all(line in text.splitlines() for line in passage.splitlines() if line.startswith("|"))


def test_shared_date_range_does_not_make_every_row_match_the_requested_month():
    rows = [f"| 2025-{month:02} | ${month}.00 |" for month in range(1, 13)]
    text = ("# Monthly prices for January–December 2025\nPrices in USD.\n| Month | Close |\n| --- | --- |\n" +
            "\n".join(rows))
    passage = web_passage(text, "Closing prices in December 2025", 180)
    assert len(passage) <= 180
    assert rows[-1] in passage
    assert "# Monthly prices for January–December 2025" in passage


def test_late_months_reach_both_slide_author_and_semantic_reviewer():
    text, rows = monthly_prices()
    query = "Plot closing prices for October, November and December 2025."
    source = Source(id="prices", title="Monthly prices", url="https://example.test/prices", excerpt=text)
    item = SimpleNamespace(request={"prompt": query})
    part = OutlineItem(title="Closing prices in Q4 2025", purpose=query, required_details=[query], layout="chart")
    context = {"sources": [source.model_dump(mode="json")]}
    author_source, = slide_inputs(item, context, part)["sources"]
    assert len(author_source["excerpt"]) <= 2000
    assert all(row in author_source["excerpt"] for row in rows[-3:])
    reviewed = []

    class Reviewer:
        def structured(self, schema, task, data, stage, image):
            reviewed.extend(data["sources"])
            return schema()

    review_slide_content(Reviewer(), Slide(title=part.title, source_ids=[source.id]), part, item, context)
    review_source, = reviewed
    assert len(review_source["excerpt"]) <= 1600
    assert all(row in review_source["excerpt"] for row in rows[-3:])


def test_entity_rows_are_selected_with_their_local_scope_and_units():
    text = ("# Legacy sites\nAll figures are in USD millions.\n| Site | Revenue |\n| --- | --- |\n" +
            "\n".join(f"| Legacy {i} | 5 |" for i in range(30)) +
            "\n\n# FY2025 new sites\nAll figures are in EUR thousands.\n| Site | Revenue |\n| --- | --- |\n" +
            "\n".join(f"| New site {i} | 5 |" for i in range(30)) +
            "\n| Docklands | 7 |\n| Northgate | 9 |")
    passage = web_passage(text, "Compare FY2025 revenue for Docklands and Northgate", 300)
    assert len(passage) <= 300
    assert "| Docklands | 7 |" in passage and "| Northgate | 9 |" in passage
    assert "All figures are in EUR thousands." in passage and "USD" not in passage
    source = Source(id="sites", title="Site revenues", excerpt=passage)
    row = EvidenceObservation(source_id=source.id, metric="Revenue", period="Docklands", scope="FY2025 new sites",
                              amount=7, currency="€", unit="k", quote="| Docklands | 7 |")
    assert list(verified_observations([row], [source])) == [row]


def test_unrelated_table_does_not_hide_requested_prose():
    text, _ = monthly_prices()
    requested = "The pilot requires board approval before hiring and regional approval before launch."
    passage = web_passage(text + "\n\n# Decision gates\n" + requested,
                          "Explain the board and regional approval gates before hiring and launch", 300)
    assert requested in passage and len(passage) <= 300


def test_a_row_that_cannot_fit_is_omitted_without_truncating_its_number():
    row = "| Extremely detailed category name that does not fit in the small passage budget | $123,456,789.12 |"
    text = "| Category | Cost |\n| --- | --- |\n" + row
    passage = web_passage(text, "Show category cost", 70)
    assert len(passage) <= 70
    assert "$123" not in passage and not any(line.startswith("|") for line in passage.splitlines())



def test_denomination_header_verifies_a_plain_series_name():
    quote = "| 2025 | 7 |"
    source = Source(id="revenue", title="Annual revenue",
                    excerpt="| Year | Revenue (USD millions) |\n| --- | --- |\n" + quote)
    row = observation(source, "Revenue (USD millions)", quote)
    assert list(verified_observations([row], [source])) == [row]
    chart = Chart(x=.1, y=.1, w=.8, h=.6, categories=["2025"],
                  series=[{"name": "Revenue", "values": [7]}], value_prefix="$", value_suffix="m")
    content = SlideContent(title="Revenue trend", source_ids=[source.id], exhibit_observations=[row])
    binding, = exhibit_bindings(content, [source], [source.id], chart)
    assert binding.status == "verified"
    index = EvidenceIndex([Metric(row.metric, {"2025": Decimal("7")}, "$", "m", "", (source.id,))])
    found = index.find("Revenue", ["2025"])
    assert found and found.currency == "$" and found.unit == "m"


def test_concise_metric_name_rejects_conflicting_denominations():
    metrics = [
        Metric("Revenue (USD millions)", {"2025": Decimal("7")}, "$", "m", "", ("usd",)),
        Metric("Revenue (EUR thousands)", {"2025": Decimal("7")}, "€", "k", "", ("eur",)),
    ]
    index = EvidenceIndex(metrics)
    assert index.find("Revenue", ["2025"]) is None
    assert "Revenue (USD millions)" in index.conflicts or "Revenue (EUR thousands)" in index.conflicts
    explicit = EvidenceIndex(metrics).find("Revenue (USD millions)", ["2025"])
    assert explicit and explicit.currency == "$"
    assert metric_base("Revenue (USD millions)") == "Revenue"
    assert metric_base("Revenue (North region)") == "Revenue (North region)"
    assert metric_base("Revenue (FY2025)") == "Revenue (FY2025)"
