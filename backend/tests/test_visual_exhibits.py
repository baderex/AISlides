import io

from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader
from pytest import mark, raises

from app.branding import inspect_template
from app.composition import Intent, Outline, OutlineItem, SlideContent
from app.domain import Document, Line, Shape, Source, Table, Text, Theme
from app.powerpoint import to_pptx
from app.main import app
from app.providers import Intelligence, exhibit_response_schema, strict_response_schema
from app.rendering import preview, to_pdf, validate_slide
from app.slide_design import build_slide, recompose_slide
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
    path = create(client, "One slide for engineers on how the browser compares fresh and stale cache hits.", research=False)
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
