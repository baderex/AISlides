import io

from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader, PdfWriter
from pytest import mark, raises

from app.branding import inspect_template
from app.composition import Intent, Outline, OutlineItem, SlideContent
from app.domain import Document, Line, Shape, Source, Table, Text, Theme
from app.export_review import add_pdf_evidence
from app.powerpoint import to_pptx
from app.main import app
from app.providers import Intelligence, exhibit_response_schema, strict_response_schema
from app.rendering import preview, to_pdf, validate_slide
from app.exhibit_evidence import DERIVED, ILLUSTRATIVE, STATUS_KEY, UNVERIFIED
from app.slide_design import LayoutReview, build_slide, recipe_options, recompose_slide, review_layouts
from app.storage import Storage
from app.visuals import VISUAL_LAYOUTS
from test_workflow import create, work


def rows(data):
    return [{"label": label, "values": values} for label, values in data]


bars = rows([("North", [3]), ("South", [8]), ("East", [5])])
composition = {"rows": rows([("North", [3, 6]), ("South", [8, 4])]), "series": ["Product", "Services"]}
matrix = {"columns": ["Impact", "Cost"], "rows": [{"label": "A", "cells": [2, 3]}, {"label": "B", "cells": [5, 1]}]}
graph = {"nodes": [{"id": "a", "label": "Client", "group": "Public", "icon": "cloud"},
                   {"id": "b", "label": "API", "role": "decision", "group": "Private"},
                   {"id": "c", "label": "Store", "group": "Private", "role": "store"}],
         "edges": [{"source": "a", "target": "b", "label": "Request"}, {"source": "b", "target": "c", "label": "Read"}]}
schedule = {"periods": ["May", "Jun", "Jul"], "items": [
    {"id": "research", "label": "Research", "start": 0, "end": 1, "kind": "phase"},
    {"id": "approval", "label": "Approval", "start": 2, "end": 2, "kind": "gate", "predecessors": ["research"]}]}
flow = {"variant": "two-scenario-comparison", "diagram": {"lanes": [
    {"title": "Fresh", "nodes": [{"label": "Check freshness"}, {"label": "Reuse local response"}]},
    {"title": "Stale", "nodes": [{"label": "Validate with server"}, {"label": "Server may return 304"}]}]}}

CASES = {
    "ranked-bar": {"rows": bars, "highlight": "East"},
    "waterfall": {"rows": rows([("Sales", [5]), ("Costs", [-2])]), "baseline": 10, "end_label": "Closing"},
    "stacked-bar": composition,
    "mekko": {**composition, "rows": [dict(row, width=10 + 10 * i) for i, row in enumerate(composition["rows"])]},
    "funnel": {"rows": list(reversed(sorted(bars, key=lambda row: row["values"])))},
    "slope": {"rows": rows([("North", [3, 2]), ("South", [1, 4])]), "series": ["FY24", "FY25"]},
    "tornado": {"rows": rows([("Inputs", [-1, 4]), ("Sales", [0, 3])]), "baseline": 2},
    "pareto": {"rows": bars},
    "heatmap": {**matrix, "scale_low": "Low", "scale_high": "High"},
    "decision-matrix": {**matrix, "recommended": "B"},
    "scoring-matrix": {**matrix, "weights": [2, 1], "recommended": "B"},
    "raci": {**matrix, "rows": [{"label": "Review", "cells": ["R", "A"]}]},
    "capability-map": {**matrix, "rows": [{"label": "Analytics", "cells": ["Defined", "Pilot"]}]},
    "two-by-two": {"horizontal_label": "Effort", "vertical_label": "Impact", "horizontal_split": 2, "vertical_split": 2,
                    "points": [{"label": "A", "horizontal": 1, "vertical": 3}, {"label": "B", "horizontal": 3, "vertical": 1}]},
    "bubble-chart": {"horizontal_label": "Share", "vertical_label": "Growth", "size_label": "Sales",
                     "points": [{"label": "A", "horizontal": 1, "vertical": 2, "size": 4},
                                {"label": "B", "horizontal": 3, "vertical": 1, "size": 16}]},
    **dict.fromkeys(["architecture", "data-flow", "flowchart", "state-machine", "pipeline", "network-topology",
                     "issue-tree", "driver-tree", "organization-chart", "value-chain", "layered-stack"], graph),
    "er-diagram": {"nodes": [{"id": "a", "label": "Order", "fields": ["id PK"]},
                             {"id": "b", "label": "Item", "fields": ["order_id FK"]}],
                   "edges": [{"source": "a", "target": "b", "label": "1 to many"}]},
    "sankey": {"nodes": graph["nodes"], "edges": [dict(edge, weight=i + 1) for i, edge in enumerate(graph["edges"])]},
    **dict.fromkeys(["roadmap", "gantt", "timing-diagram"], schedule),
    "sequence-diagram": {"participants": ["Client", "API"], "messages": [
        {"source": 0, "target": 1, "label": "GET"}, {"source": 1, "target": 0, "label": "304", "kind": "return"}]},
    "venn": {"left": "Local", "right": "Remote", "left_only": "Disk", "right_only": "Server", "intersection": "Shared"},
    "annotated-code": {"language": "Python", "lines": [{"code": "if fresh:"}, {"code": "    return cached", "highlight": True}]},
    "flow-diagram": flow,
    "swimlane": {"swimlane": {"lanes": ["Client", "Server"], "nodes": [
        {"id": "a", "label": "Request", "lane": 0, "column": 0}, {"id": "b", "label": "Respond", "lane": 1, "column": 1}],
        "edges": [{"source": "a", "target": "b", "label": "GET"}]}},
}


@mark.parametrize("kind", VISUAL_LAYOUTS)
def test_each_visual_lays_out_and_exports_editable_objects(kind):
    data = CASES[kind]
    choice = {"type": kind, "variant": data.get("variant")}
    part = OutlineItem.model_validate({"mainMessage": "The exhibit explains the finding", "visual": choice})
    content = SlideContent.model_validate({"title": part.title, "visual": {"type": kind, **data}, "source_ids": ["brief"]},
                                          context={"required_exhibit": "visual", "visual_choice": choice})
    for aspect in ["16:9", "4:3", "1:1"]:
        deck = Document(title="Review", aspect_ratio=aspect, sources=[Source(id="brief", title="Exhibit inputs")])
        slide = build_slide(part, content, deck, 0, set())
        deck.slides = [slide]
        assert slide.layout == VISUAL_LAYOUTS[kind]
        assert len(slide.elements) >= 4
        assert not validate_slide(deck, slide, set())
        Document.model_validate(deck.model_dump())
        rendered = Image.open(io.BytesIO(preview(deck, slide, lambda _: None, pixel_width=480)))
        assert rendered.width == 480
    powerpoint = Presentation(io.BytesIO(to_pptx(deck, Storage().get)))
    native = powerpoint.slides[0].shapes
    assert any(shape.has_text_frame and shape.text == part.title for shape in native)
    assert len(native) == len(slide.elements)
    pdf_text = PdfReader(io.BytesIO(to_pdf(deck, Storage().get))).pages[0].extract_text()
    assert part.title in " ".join(pdf_text.split())


@mark.parametrize("use_series", [False, True])
def test_single_slope_binds_and_renders_both_periods(use_series):
    periods = ["2023", "2025"]
    amounts = [11967.39, 10490.69]
    quotes = [f"TASI {year}: {amount} points" for year, amount in zip(periods, amounts)]
    source = Source(id="exchange", title="Year-end closes", excerpt=". ".join(quotes))
    labels = {"series": periods} if use_series else {"start_label": periods[0], "end_label": periods[1]}
    content = SlideContent(title="TASI closing levels", source_ids=[source.id], visual={
        "type": "slope", "unit": "points", "rows": rows([("TASI", amounts)]), **labels,
    }, exhibit_observations=[
        {"source_id": source.id, "metric": "TASI", "period": period, "amount": amount, "unit": "points", "quote": quote}
        for period, amount, quote in zip(periods, amounts, quotes)
    ])
    deck = Document(title=content.title, sources=[source])
    slide = build_slide(OutlineItem(title=content.title, layout="chart"), content, deck, 0, set())
    assert [b["status"] for b in slide.spec.value_bindings] == ["verified", "verified"]
    assert sum(el.element_key == "slope:TASI:connector" for el in slide.elements) == 1
    assert set(periods) <= {el.text for el in slide.elements if isinstance(el, Text)}


def test_two_scenario_plan_preserves_annotations_and_editable_flow():
    part = OutlineItem.model_validate({
        "slideIntent": "explain-process",
        "mainMessage": "The browser reuses fresh responses without contacting the server",
        "visual": {"type": "flow-diagram", "variant": "two-scenario-comparison"},
        "annotations": ["No network request for a fresh hit", "Revalidation may return 304 Not Modified"],
    })
    content = SlideContent(title=part.title, visual={"type": "flow-diagram", **flow},
                           annotations=[{"text": part.annotations[0], "target": "Reuse local response"},
                                        part.annotations[1]],
                           decorations={"kicker": "HTTP caching", "emphasis": ["fresh responses"], "status": 3,
                                        "footnote": "304 applies when the response is unchanged."})
    document = Document(title="Browser cache", theme=Theme(profile="consulting"))
    slide = build_slide(part, content, document, 0, set())
    labels = {e.text: e for e in slide.elements if isinstance(e, Text)}
    assert labels[part.title].spans
    assert set(part.annotations) <= labels.keys()
    assert sum(isinstance(e, Line) and e.arrows == "end" for e in slide.elements) == 3


def test_heatmap_retains_cell_colors_in_native_powerpoint_table_and_recomposes():
    part = OutlineItem(title="Where performance varies", layout="research")
    content = SlideContent(title=part.title, visual={"type": "heatmap", **CASES["heatmap"]}, source_ids=["brief"])
    deck = Document(title="Review", sources=[Source(id="brief", title="Inputs")])
    slide = build_slide(part, content, deck, 0, set())
    deck.slides = [slide]
    rebuilt = recompose_slide(slide, deck, 0, keep_recipe=True)
    assert rebuilt is not None
    grid = next(e for e in rebuilt.elements if isinstance(e, Table))
    assert grid.cell_style(1, 1) != grid.cell_style(2, 1)
    native = Presentation(io.BytesIO(to_pptx(deck, None)))
    table = next(shape.table for shape in native.slides[0].shapes if shape.has_table)
    assert str(table.cell(1, 1).fill.fore_color.rgb) == grid.cell_fills[1][1][1:]


def test_quantitative_exhibit_needs_cited_data_and_preserves_order():
    plan = OutlineItem(title="Ranked results", layout="chart")
    content = SlideContent(title=plan.title, visual={"type": "ranked-bar", **CASES["ranked-bar"]})
    deck = Document(title="Review", sources=[Source(id="brief", title="Inputs")])
    assert build_slide(plan, content, deck, 0, set()).warnings
    content.source_ids = ["brief"]
    slide = build_slide(plan, content, deck, 0, set())
    labels = {e.text: e.y for e in slide.elements if isinstance(e, Text)}
    assert labels["South"] < labels["East"] < labels["North"]
    assert sum(isinstance(e, Shape) and e.fill == deck.theme.accent for e in slide.elements) == 1


def test_provider_contract_is_narrow_and_rejects_the_wrong_visual_type():
    choice = {"type": "waterfall", "variant": None}
    schema = strict_response_schema(exhibit_response_schema(SlideContent, "visual", choice))
    assert "QuantitativeVisual" in schema["$defs"]
    assert "GraphVisual" not in schema["$defs"]
    assert "annotations" in schema["required"]
    assert schema["$defs"]["QuantitativeVisual"]["properties"]["type"]["const"] == "waterfall"
    with raises(ValueError, match="use the planned visual type"):
        SlideContent.model_validate({"title": "Comparison", "visual": {"type": "ranked-bar", **CASES["ranked-bar"]}},
                                    context={"required_exhibit": "visual", "visual_choice": choice})


def test_worker_generates_and_exports_a_semantic_visual(monkeypatch):
    headline = "Fresh responses stay in the browser"
    choice = {"type": "flow-diagram", "variant": "two-scenario-comparison"}
    original = Intelligence.structured

    def respond(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Browser caches", slide_count=1, audience="Engineers", visual_direction="Technical")
        if schema is Outline:
            assert "explain causes=driver-tree" in task
            return Outline.model_validate({"title": "Browser caching", "slides": [
                {"slideIntent": "explain-process", "mainMessage": headline, "visual": choice,
                 "annotations": ["Fresh hits stay local"]}]})
        if schema is SlideContent:
            assert data["required_exhibit"] == "visual"
            assert data["visual_choice"] == choice
            return SlideContent(title=headline, visual={"type": "flow-diagram", **flow})
        return original(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", respond)
    client = TestClient(app)
    path = create(client, "One slide for engineers on how the browser compares fresh and stale cache hits.", research=False, native=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert result["document"]["slides"][0]["title"] == headline
    exported = Presentation(io.BytesIO(client.post(f"{path}/export?format=pptx").content))
    assert any(shape.has_text_frame and shape.text == "Fresh hits stay local" for shape in exported.slides[0].shapes)


def test_qualitative_heatmap_uses_ordered_categories_without_inventing_scores():
    part = OutlineItem(title="Risk by region", layout="research")
    content = SlideContent(title=part.title, visual={
        "type": "heatmap", "columns": ["Risk"], "levels": ["Low", "Medium", "High"],
        "rows": [{"label": "A", "cells": ["High"]}, {"label": "B", "cells": ["Low"]}],
    })
    slide = build_slide(part, content, Document(title="Risk"), 0, set())
    grid = next(e for e in slide.elements if isinstance(e, Table))
    assert not slide.warnings
    assert grid.rows[1:] == [["A", "High"], ["B", "Low"]]
    assert grid.cell_fills[1][1] != grid.cell_fills[2][1]


def test_branded_visual_retains_master_and_native_exhibit():
    template = Presentation()
    raw = io.BytesIO()
    template.save(raw)
    skill = inspect_template(raw.getvalue(), "template", "Test brand")
    part = OutlineItem(title="Handoff to the service", layout="process")
    content = SlideContent(title=part.title, visual={"type": "sequence-diagram", **CASES["sequence-diagram"]})
    deck = Document(title=part.title, theme=skill.theme)
    slide = build_slide(part, content, deck, 0, set(), skill.brand)
    deck.slides = [slide]
    exported = Presentation(io.BytesIO(to_pptx(deck, lambda _: raw.getvalue(), skill.brand)))
    native = exported.slides[0]
    assert native.slide_layout.name == skill.brand.layouts[slide.brand_layout_index].name
    assert native.shapes.title.text == part.title
    assert any(el.dashed for el in slide.elements if isinstance(el, Line))


def test_qualitative_two_by_two_uses_quadrants_without_source_scores():
    part = OutlineItem(title="Prioritize work by impact and effort", layout="chart")
    content = SlideContent(title=part.title, visual={
        "type": "two-by-two", "horizontal_label": "Effort", "vertical_label": "Impact",
        "points": [{"label": "Quick win", "quadrant": "upper-left"}, {"label": "Defer", "quadrant": "lower-right"}],
    })
    slide = build_slide(part, content, Document(title=part.title), 0, set())
    labels = {e.text: e for e in slide.elements if isinstance(e, Text)}
    assert not slide.warnings
    assert labels["Quick win"].x < labels["Defer"].x
    assert labels["Quick win"].y < labels["Defer"].y


def sourced_waterfall():
    part = OutlineItem(title="Operating profit bridge", layout="chart")
    source = Source(id="ledger", title="Profit ledger", excerpt=(
        "Operating profit Opening: $120m. Operating profit Pricing: −$15m. Operating profit Volume: $8m."
    ))
    deck = Document(title=part.title, sources=[source])
    content = SlideContent(title=part.title, source_ids=[source.id], visual={
        "type": "waterfall", "metric": "Operating profit", "unit": "$m", "baseline": 120,
        "start_label": "Opening", "end_label": "Closing", "rows": rows([("Pricing", [-15]), ("Volume", [8])]),
    }, exhibit_observations=[
        {"source_id": source.id, "metric": "Operating profit", "period": label,
         "amount": amount, "currency": "$", "unit": "m", "quote": quote}
        for label, amount, quote in zip(("Opening", "Pricing", "Volume"), (120, -15, 8), source.excerpt.split(". "))
    ])
    return part, content, deck


def evidence_status(slide):
    return next(el.text for el in slide.elements if el.element_key == STATUS_KEY)


def test_value_bindings_are_exported_and_a_citation_does_not_verify_an_invented_adjustment():
    part, content, deck = sourced_waterfall()
    slide = build_slide(part, content, deck, 0, set())
    deck.slides = [slide]
    assert evidence_status(slide) == DERIVED
    bindings = {row["path"]: row for row in slide.spec.value_bindings}
    assert bindings["baseline"]["source_observation_id"].startswith("obs-")
    assert bindings["ending-total"]["status"] == "derived"
    assert bindings["ending-total"]["inputs"] == ["baseline", "rows/0/values/0", "rows/1/values/0"]
    assert bindings["ending-total"]["value"] == "113.0"

    powerpoint = Presentation(io.BytesIO(to_pptx(deck, None)))
    notes = powerpoint.slides[0].notes_slide.notes_text_frame.text
    assert "Operating profit Pricing:" in notes
    assert "ending-total" in notes
    pdf = PdfReader(io.BytesIO(to_pdf(deck, None)))
    page = pdf.pages[0]
    assert DERIVED in page.extract_text()
    assert "ending-total" in page["/Annots"][0].get_object()["/Contents"]

    content.visual.rows[0].values[0] = 12
    unsupported = build_slide(part, content, deck, 0, set())
    assert evidence_status(unsupported) == UNVERIFIED
    assert unsupported.spec.value_bindings[1]["status"] == "unverified"
    content.illustrative_exhibit = True
    assert evidence_status(build_slide(part, content, deck, 0, set())) == ILLUSTRATIVE

    value_label = next(el for el in slide.elements if el.element_key == "waterfall:driver:Pricing:value-label")
    value_label.text = "$12m"
    downgraded = Presentation(io.BytesIO(to_pptx(deck, None)))
    labels = [shape.text for shape in downgraded.slides[0].shapes if shape.has_text_frame]
    assert UNVERIFIED in labels and DERIVED not in labels


def test_research_quote_must_match_the_figures_currency_scale_and_meaning():
    part, content, deck = sourced_waterfall()
    for text, currency, unit in (
        ("Operating profit Pricing fell from −$15m.", "$", "m"),
        ("Operating profit Pricing: −15m. Other values in $m.", "$", "m"),
        ("Operating profit Pricing: −$15bn. Other values in m.", "$", "m"),
    ):
        observation = content.exhibit_observations[1]
        observation.quote = text
        observation.currency = currency
        observation.unit = unit
        deck.sources[0].excerpt = text
        assert build_slide(part, content, deck, 0, set()).spec.value_bindings[1]["status"] == "unverified"


def test_table_evidence_binds_only_the_quoted_rows_exact_column_category_and_unit():
    quote = "| 1 | Bitcoin | BTC | $1,747.54bn | $87,508.83 |"
    source = Source(id="snapshot", title="December snapshot", excerpt=(
        "| Rank | Name | Symbol | Market cap | Price |\n| --- | --- | --- | --- | --- |\n" + quote))
    deck = Document(title="Market cap", sources=[source])
    part = OutlineItem(title=deck.title, layout="chart")
    row = {"source_id": source.id, "metric": "Market cap", "period": "BTC", "amount": "1747.54",
           "currency": "$", "unit": "bn", "quote": quote}
    for changes, expected in [({}, "verified"), ({"metric": "Price"}, "unverified"),
                              ({"period": "ETH"}, "unverified"), ({"amount": 87508.83}, "unverified"),
                              ({"unit": ""}, "unverified"), ({"currency": "EUR"}, "unverified")]:
        fact = {**row, **changes}
        content = SlideContent(title=part.title, source_ids=[source.id], chart={
            "label": fact["metric"], "categories": [fact["period"]], "values": [fact["amount"]],
            "value_prefix": fact["currency"], "value_suffix": fact["unit"]}, exhibit_observations=[fact])
        assert build_slide(part, content, deck, 0, set()).spec.value_bindings[0]["status"] == expected


def test_free_form_numeric_callout_does_not_inherit_chart_verification():
    part, content, deck = sourced_waterfall()
    part.annotations = ["Pricing headwind will cost $12m next year"]
    slide = build_slide(part, content, deck, 0, set())
    assert evidence_status(slide) == UNVERIFIED
    assert any(row["path"] == "annotations/0" and row["status"] == "unverified" for row in slide.spec.value_bindings)


def test_bubble_geometry_can_bind_metrics_with_different_units():
    part = OutlineItem(title="North performance", layout="chart")
    entries = [("Share", 10, "", "%", "Share North: 10%."),
               ("Growth", 3, "", "%", "Growth North: 3%."),
               ("Sales", 12, "$", "m", "Sales North: $12m.")]
    deck = Document(title=part.title, sources=[Source(
        id="ledger", title="Performance ledger", excerpt=" ".join(row[4] for row in entries))])
    content = SlideContent(title=part.title, source_ids=["ledger"], visual={
        "type": "bubble-chart", "horizontal_label": "Share", "vertical_label": "Growth", "size_label": "Sales",
        "horizontal_unit": "%", "vertical_unit": "%", "size_unit": "$m",
        "points": [{"label": "North", "horizontal": 10, "vertical": 3, "size": 12}],
    }, exhibit_observations=[
        {"source_id": "ledger", "metric": metric, "period": "North", "amount": amount,
         "currency": currency, "unit": unit, "quote": quote}
        for metric, amount, currency, unit, quote in entries
    ])
    slide = build_slide(part, content, deck, 0, set())
    assert evidence_status(slide) == DERIVED
    assert {row["path"] for row in slide.spec.value_bindings if row["status"] == "verified"} == {
        "points/0/horizontal", "points/0/vertical", "points/0/size",
    }


def test_native_pdf_conversion_can_preserve_the_exhibit_evidence_annotation():
    part, content, deck = sourced_waterfall()
    deck.slides = [build_slide(part, content, deck, 0, set())]
    native_pdf = PdfWriter()
    native_pdf.add_blank_page(960, 540)
    raw = io.BytesIO()
    native_pdf.write(raw)
    reader = PdfReader(io.BytesIO(add_pdf_evidence(raw.getvalue(), deck)))
    assert "ending-total" in reader.pages[0]["/Annots"][0].get_object()["/Contents"]


def test_sequence_local_actions_return_to_the_same_lifeline():
    part = OutlineItem(title="Prepare the verifier", layout="process")
    content = SlideContent(title=part.title, visual={"type":"sequence-diagram", "participants":["SPA","Server"],
        "messages":[{"source":0, "target":0, "label":"Generate verifier"}, {"source":0, "target":1, "label":"Authorize"}]})
    deck = Document(title=part.title, aspect_ratio="1:1")
    slide = build_slide(part, content, deck, 0, set())
    lines = {el.element_key:el for el in slide.elements if isinstance(el, Line)}
    outgoing, returning = (lines["sequence:message:0:" + suffix] for suffix in ("out","connector"))
    assert (outgoing.direction, returning.direction, returning.arrows) == ("right", "left", "end")
    assert outgoing.x == returning.x and outgoing.y < returning.y
    assert lines["sequence:message:1:connector"].direction == "right"


@mark.parametrize("kind", ["ranked-bar", "waterfall", "decision-matrix", "architecture", "roadmap", "sankey"])
def test_semantic_recipe_candidates_change_layout_and_share_element_ids(kind):
    part = OutlineItem(title="Compare the exhibit layouts", layout=VISUAL_LAYOUTS[kind])
    content = SlideContent(title=part.title, visual={"type": kind, **CASES[kind]}, source_ids=["brief"])
    deck = Document(title=part.title, sources=[Source(id="brief", title="Inputs")])
    standard, alternate = recipe_options(part, content, deck)
    original = build_slide(part, content, deck, 0, set(), recipe=standard)
    second = build_slide(part, content, deck, 0, set(), recipe=alternate, slide_id=original.id)
    assert not validate_slide(deck, second, set())
    original_keys = {el.id: el for el in original.elements if (el.element_key or "").startswith(kind + ":")}
    moved = [el for el in second.elements if el.id in original_keys and
             (el.x, el.y, el.w, el.h) != (original_keys[el.id].x, original_keys[el.id].y,
                                          original_keys[el.id].w, original_keys[el.id].h)]
    assert moved


def test_visual_review_receives_both_ranked_bar_candidates():
    part = OutlineItem(title="Rank the regions", layout="chart")
    content = SlideContent(title=part.title, visual={"type": "ranked-bar", **CASES["ranked-bar"]},
                           source_ids=["brief"])
    deck = Document(title=part.title, sources=[Source(id="brief", title="Inputs")])
    slide = build_slide(part, content, deck, 0, set())

    def evaluate(_part, recipes, _png):
        assert recipes == ["standard", "exhibit_callouts"]
        return LayoutReview(ratings=[{"candidate": i, **dict.fromkeys(
            ("hierarchy", "emphasis", "balance", "imagery", "style_match"), i)
        } for i in (1, 2)])

    winner, _ = review_layouts(deck, slide, 0, set(), lambda _: None, evaluate=evaluate)
    assert winner.spec.recipe == "exhibit_callouts"


def test_sankey_uses_proportional_editable_ribbons_and_flags_a_nonconserving_node():
    part = OutlineItem(title="Track the flow", layout="process")
    content = SlideContent(title=part.title, visual={"type": "sankey", **CASES["sankey"]},
                           source_ids=["brief"])
    deck = Document(title=part.title, sources=[Source(id="brief", title="Inputs")])
    slide = build_slide(part, content, deck, 0, set())
    deck.slides = [slide]
    ribbons = [element for element in slide.elements if isinstance(element, Shape) and element.polygon]
    assert len(ribbons) == 2
    thicknesses = [el.h * (el.polygon[-1][1] - el.polygon[0][1]) for el in ribbons]
    assert abs(thicknesses[1] - 2 * thicknesses[0]) < 1e-12
    assert any("does not balance at API" in warning for warning in slide.warnings)
    assert any(row["path"] == "nodes/b/balance" and row["value"] == "-1.0" for row in slide.spec.value_bindings)
    powerpoint = Presentation(io.BytesIO(to_pptx(deck, None)))
    assert sum(shape._element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}custGeom") is not None
               for shape in powerpoint.slides[0].shapes) == 2


def test_dense_architecture_uses_hierarchical_geometry_and_keeps_its_semantic_edges():
    part = OutlineItem(title="Service architecture", layout="process")
    nodes = [{"id": f"service-{i}", "label": f"Service {i}"} for i in range(8)]
    edges = [{"source": nodes[a]["id"], "target": nodes[b]["id"]}
             for a, b in [(0, 3), (0, 4), (1, 2), (1, 4), (2, 5), (3, 6), (4, 6), (2, 7), (3, 7), (7, 1)]]
    content = SlideContent(title=part.title, visual={"type": "architecture", "nodes": nodes, "edges": edges})
    deck = Document(title=part.title)
    slide = build_slide(part, content, deck, 0, set())
    connectors = [el for el in slide.elements if isinstance(el, Line)]
    generated_nodes = {el.id for el in slide.elements if (el.element_key or "").endswith(":node")}
    assert len(generated_nodes) == len(nodes)
    assert len([el for el in connectors if el.start_anchor and el.end_anchor]) == len(edges)
    assert all(el.start_anchor.shape_id in generated_nodes for el in connectors if el.start_anchor)
    assert slide.spec.content["visual"]["edges"] == [e.model_dump(mode="json") for e in content.visual.edges]
    assert not validate_slide(deck, slide, set())


def test_exhibit_editor_rebuilds_and_preserves_visual_edits_through_recipe_switch():
    part, content, deck = sourced_waterfall()
    slide = build_slide(part, content, deck, 0, set())
    deck.slides = [slide]
    client = TestClient(app)
    path = create(client, generate=False)
    version = client.get(path).json()
    saved = client.patch(path, json={"revision": version["revision"], "document": deck.model_dump(mode="json")})
    assert saved.status_code == 200, saved.text
    current = saved.json()
    draft = current["document"]["slides"][0]
    bar = next(el for el in draft["elements"] if el["element_key"] == "waterfall:driver:Pricing:bar")
    bar.update(fill="#395366", edited=True)
    visual = content.visual.model_dump(mode="json")
    visual["rows"][0]["values"][0] = -18
    result = client.post(f'{path}/slides/{slide.id}/exhibit', json={
        "revision": current["revision"], "slide": draft, "visual": visual, "recipe": "exhibit_callouts",
    })
    assert result.status_code == 200, result.text
    edited = result.json()
    assert edited["spec"]["recipe"] == "exhibit_callouts"
    assert edited["spec"]["content"]["visual"]["rows"][0]["values"] == [-18]
    assert any(el["id"] == bar["id"] and el["fill"] == "#395366" for el in edited["elements"])
    assert any(el.get("text") == UNVERIFIED for el in edited["elements"])
    assert any(el.get("text") == "110" for el in edited["elements"])
    assert client.get(path).json()["revision"] == current["revision"]
