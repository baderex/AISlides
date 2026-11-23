import io
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image, ImageColor
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_VERTICAL_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pypdf import PdfReader
from pytest import approx

from app.composition import Intent, Outline, SlideContent
from app.design import ArtDirection
from app.domain import Chart
from app.main import app
from app.providers import Intelligence, ProviderError
from app.rendering import chart_marks, contrast
from app.render_geometry import available_family, fallback_family
from test_workflow import create, work


def test_dark_financial_deck_reuses_direction_and_exports_native_exhibits(monkeypatch):
    """A vague summary layout in the outline must not suppress a sourced exhibit."""
    structured = Intelligence.structured
    design_calls = []
    plan_attempts = []
    rows = [["USD millions", "FY23", "FY24", "FY25"], ["Revenue", "80", "92", "106"],
            ["Gross profit", "40", "48", "57"], ["Costs", "28", "32", "37"], ["Profit", "12", "16", "20"]]

    def respond(self, schema, task, data, stage="planning", image=None):
        if schema is ArtDirection:
            design_calls.append(data["prompt"])
        if schema is Outline:
            plan_attempts.append(data["design"])
            if len(plan_attempts) == 1:
                raise ProviderError("Temporary outline failure")
            return Outline(title="Management accounts", slides=[
                {"title": "Management accounts", "layout": "cover"},
                {"title": "Revenue", "layout": "summary"}, {"title": "Operating results", "layout": "summary"}])
        if schema is SlideContent:
            title = data["item"]["title"]
            source = data["sources"][0]["id"]
            if title == "Revenue":
                return SlideContent(title=title, source_ids=[source], chart={
                    "label": "Revenue", "chart": "line", "categories": rows[0][1:], "values": [80, 92, 106]})
            return SlideContent(title=title, table=rows if title == "Operating results" else None, source_ids=[source])
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", respond)
    client = TestClient(app)
    accounts = client.post("/api/v1/assets", files={"file": ("accounts.txt", str(rows).encode(), "text/plain")}).json()
    path = create(client, "Three slides for investors about finance in dark luxury style.", native=True, asset_ids=[accounts["id"]])
    work()
    saved = client.get(path).json()
    assert saved["status"] == "FAILED"
    theme = saved["document"]["theme"]
    expected_face = available_family("Georgia") or fallback_family("Georgia")
    assert theme["design"]["heading_font"] == expected_face
    assert client.post(path + "/generate").status_code == 202
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert len(design_calls) == 1 and plan_attempts == [theme, theme]
    cover, trend, results = result["document"]["slides"]
    assert [s["layout"] for s in (cover, trend, results)] == ["cover", "chart", "research"]
    assert cover["background"] == "#191817"
    assert next(el for el in cover["elements"] if el.get("text") == cover["title"])["font"] == expected_face
    chart = next(el for el in trend["elements"] if el["type"] == "chart")
    table = next(el for el in results["elements"] if el["type"] == "table")
    assert table["rows"] == rows and table["fill"] == "#F7F2E8"
    assert table["numeric_columns"] == [1, 2, 3]
    assert table["row_rule"] and table["header_rule"] == theme["accent"]
    assert contrast(table["color"], table["fill"]) >= 4.5
    assert contrast(table["header_color"], table["header_fill"]) >= 4.5
    assert contrast(chart["label_color"], trend["background"]) >= 4.5
    png = client.get(path + "/slides/" + results["id"] + "/preview")
    pixels = Image.open(io.BytesIO(png.content))
    assert pixels.getpixel((0, 0)) == ImageColor.getrgb(cover["background"])
    assert pixels.getpixel((int(.25*pixels.width), int(.85*pixels.height))) == ImageColor.getrgb(table["fill"])

    raw = client.post(path + "/export?format=pptx").content
    ppt = Presentation(io.BytesIO(raw))
    native = next(shape.table for shape in ppt.slides[2].shapes if shape.has_table)
    assert native.cell(4, 3).text == "20"
    assert str(native.cell(1, 0).fill.fore_color.rgb) == table["fill"][1:]
    assert str(native.cell(0, 0).fill.fore_color.rgb) == table["header_fill"][1:]
    number_cell = native.cell(4, 3)
    assert number_cell.vertical_anchor == MSO_VERTICAL_ANCHOR.MIDDLE
    assert number_cell.text_frame.paragraphs[0].alignment == PP_ALIGN.RIGHT
    assert native.cell(4, 0).text_frame.paragraphs[0].font.bold
    borders = native.cell(0, 0)._tc.get_or_add_tcPr()
    assert borders.find(qn("a:lnL")).find(qn("a:noFill")) is not None
    header_line_color = borders.find(qn("a:lnB")).find(qn("a:solidFill")).find(qn("a:srgbClr"))
    assert header_line_color.get("val") == table["header_rule"][1:]
    line = next(shape.chart for shape in ppt.slides[1].shapes if shape.has_chart)
    assert str(line.series[0].format.line.color.rgb) == chart["color"][1:]
    assert str(line.category_axis.tick_labels.font.color.rgb) == chart["label_color"][1:]
    assert line.plots[0].data_labels.show_value
    assert [*line.series[0].values] == [80, 92, 106]
    with ZipFile(io.BytesIO(raw)) as archive:
        assert any(name.startswith("ppt/embeddings/") for name in archive.namelist())
    pdf = PdfReader(io.BytesIO(client.post(path + "/export?format=pdf").content))
    assert len(pdf.pages) == 3
    assert all(cell in pdf.pages[2].extract_text() for row in rows for cell in row)


def test_supplied_costs_and_decision_use_editable_exhibits(monkeypatch):
    structured = Intelligence.structured
    parts = [
        ("Costs by option", "chart", "Compare monthly costs."),
        ("Budget", "summary", "Show the itemized allocation."),
        ("Delivery schedule", "process", "Show the deployment timeline and risks."),
        ("Release decision", "metrics", "Show performance gates and approval."),
    ]
    expected_exhibits = ["stacked_comparison", "allocation", "rollout", "decision"]
    exhibits = [
        {"stacked_comparison": {"label": "Monthly cost", "value_prefix": "$", "categories": ["Current", "Proposed"],
                                "series": [{"name": "Labor", "values": [70, 35]},
                                           {"name": "Tools", "values": [30, 25]}]}},
        {"allocation": {"value_prefix": "$", "lines": [
            {"label": "Equipment", "amount": 1000}, {"label": "Training", "amount": 800},
            {"label": "Support", "amount": 300}, {"label": "Contingency", "amount": 200}]}},
        {"rollout": {"phases": [{"period": "Week 1", "milestone": "Setup"},
                               {"period": "Week 2", "milestone": "Test"}],
                     "risks": [{"risk": "Late hardware", "mitigation": "Keep spare units available"}]}},
        {"decision": {"action": "Approve", "request": "the $2,300 trial", "if_missed": "Pause release and investigate.",
                      "gates": [{"label": "Success rate", "threshold": "≥95%"}]}},
    ]

    def respond(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Trial release", audience="Operations", visual_direction="Editorial",
                          slide_count=4, requires_quantitative_evidence=True, supplied_figures=True,
                          supplied_evidence_only=True)
        if schema is Outline:
            return Outline(title="Trial release", slides=[
                {"title": title, "layout": layout, "purpose": purpose, "exhibit": "diagram" if index == 2 else None}
                for index, (title, layout, purpose) in enumerate(parts)])
        if schema is SlideContent:
            assert data["required_exhibit"] == expected_exhibits[data["slide_index"]]
            return SlideContent(title=data["item"]["title"], source_ids=[data["brief_source_id"]],
                                **exhibits[data["slide_index"]])
        return structured(self, schema, task, data, stage, image)

    def unexpected_lookup(*args, **kwargs):
        raise AssertionError("The supplied-evidence-only brief must not trigger a web lookup")

    monkeypatch.setattr(Intelligence, "structured", respond)
    monkeypatch.setattr("app.workflow.collect", unexpected_lookup)
    client = TestClient(app)
    path = create(client, "Four slides for operations. Use only these supplied figures: monthly costs $100 ($70 labor, $30 tools) "
                         "and $60 ($35 labor, $25 tools). "
                         "Allocate $1,000, $800, $300 and $200.", research=True, native=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert [source["title"] for source in result["document"]["sources"]] == ["User-provided brief"]
    costs, budget, rollout, decision = result["document"]["slides"]
    assert [slide["layout"] for slide in (costs, budget, rollout, decision)] == ["chart", "metrics", "timeline", "closing"]
    chart = Chart.model_validate(next(el for el in costs["elements"] if el["type"] == "chart"))
    assert chart.totals() == [100, 60]
    current_base, current_top, proposed_base, proposed_top = [geometry for _, _, geometry in chart_marks(chart, 100, 100)]
    assert current_base[1] + current_base[3] == approx(100)
    assert proposed_base[1] + proposed_base[3] == approx(100)
    assert (100 - proposed_top[1]) / (100 - current_top[1]) == approx(.6)
    assert {"$2,300", "$1,000", "$800", "$300", "$200"} <= {el.get("text") for el in budget["elements"]}
    labels = {el.get("text"): el for el in rollout["elements"] if el["type"] == "text"}
    assert labels["Week 1"]["x"] < labels["Week 2"]["x"]
    assert labels["Late hardware"]["y"] == labels["Keep spare units available"]["y"]
    assert {"Approve the $2,300 trial.", "Pause release and investigate.", "≥95%"} <= {
        el.get("text") for el in decision["elements"]}

    ppt = Presentation(io.BytesIO(client.post(path + "/export?format=pptx").content))
    native = next(shape.chart for shape in ppt.slides[0].shapes if shape.has_chart)
    assert native.chart_type == XL_CHART_TYPE.COLUMN_STACKED
    assert native.value_axis.minimum_scale == 0
    assert native.plots[0].data_labels.show_value
    assert not native.has_legend
    assert [category.label for category in native.plots[0].categories] == ["Current  $100", "Proposed  $60"]
    pdf = PdfReader(io.BytesIO(client.post(path + "/export?format=pdf").content))
    text = pdf.pages[0].extract_text()
    assert all(label in text for label in chart.category_labels())
    assert {"$40", "$70", "$30", "$35", "$25"} <= set(text.split())
    strip = [el for el in budget["elements"] if el["type"] == "shape"][:4]
    assert [el["w"] for el in strip] == approx([.86 * amount / 2300 for amount in (1000, 800, 300, 200)])


def test_requested_cover_image_fits_and_is_used_once(monkeypatch):
    structured = Intelligence.structured
    client = TestClient(app)
    photo = io.BytesIO()
    Image.new("RGB", (400, 240), "gray").save(photo, format="PNG")
    uploaded = client.post("/api/v1/assets", files={"file": ("Product.png", photo.getvalue(), "image/png")})
    asset = uploaded.json()["id"]

    def respond(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Product launch", audience="Buyers", visual_direction="editorial", slide_count=2)
        if schema is Outline:
            return Outline(title="Product", slides=[
                {"title": "Product", "layout": "cover", "use_uploaded_image": True},
                {"title": "Features", "layout": "editorial"},
            ])
        if schema is SlideContent:
            # The provider omits the assigned hero and erroneously asks for it later.
            image_id = asset if data["slide_index"] == 1 else None
            return SlideContent(title=data["item"]["title"], image_asset_id=image_id)
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", respond)
    path = create(client, "Create two editorial slides for buyers with the photo on the cover only.", native=True, asset_ids=[asset])
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    cover, features = result["document"]["slides"]
    photo, = [element for element in cover["elements"] if element["type"] == "image"]
    assert photo["w"] * 960 / (photo["h"] * 540) == approx(400 / 240)
    assert photo["w"] * photo["h"] > .44
    assert all(element["type"] != "image" for element in features["elements"])
    exported = Presentation(io.BytesIO(client.post(path + "/export?format=pptx").content))
    assert sum(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for slide in exported.slides for shape in slide.shapes) == 1


def test_explicit_diagram_style_routes_comparison_to_native_branch(monkeypatch):
    structured = Intelligence.structured

    def respond(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Review workflow", audience="Operators", visual_direction="Light", slide_count=1)
        if schema is Outline:
            return Outline(title="Review workflow", slides=[{"title": "Review workflow", "layout": "comparison"}])
        if schema is SlideContent:
            assert data["required_exhibit"] == "diagram"
            return SlideContent(title="Review workflow", diagram={"lanes": [{
                "kind": "branch", "nodes": [{"label": "Review request", "role": "decision"},
                                            {"label": "Accepted", "detail": "Distribute the result"},
                                            {"label": "Rework", "detail": "Return to the author"}],
            }], "recap": "Route the request according to its review outcome."})
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", respond)
    client = TestClient(app)
    path = create(client, "Create one diagram-friendly slide about a review workflow for operators.", aspect_ratio="1:1", native=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    slide = result["document"]["slides"][0]
    assert slide["warnings"] == []
    lines = [element for element in slide["elements"] if element["type"] == "line"]
    assert [line["direction"] for line in lines] == ["down", "right", "down", "down"]
    native = Presentation(io.BytesIO(client.post(path + "/export?format=pptx").content))
    connectors = [shape for shape in native.slides[0].shapes if shape.shape_type == MSO_SHAPE_TYPE.LINE]
    assert len(connectors) == 4
    assert sum(connector._element.spPr.ln.find(qn("a:tailEnd")) is not None for connector in connectors) == 2
    assert {"Accepted", "Rework"} <= {shape.text for shape in native.slides[0].shapes if shape.has_text_frame}
    pdf = PdfReader(io.BytesIO(client.post(path + "/export?format=pdf").content))
    assert "Distribute the result" in pdf.pages[0].extract_text()


def test_swimlane_request_routes_to_editable_anchored_export(monkeypatch):
    structured = Intelligence.structured

    def respond(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Browser cache", audience="Developers", visual_direction="Light", slide_count=1)
        if schema is Outline:
            return Outline(title="Browser cache", slides=[{"title": "Cache hit vs. revalidation", "layout": "process"}])
        if schema is SlideContent:
            assert data["required_exhibit"] == "swimlane"
            return SlideContent(title="Cache hit vs. revalidation", swimlane={
                "lanes": ["Browser", "Server"],
                "nodes": [
                    {"id": "fresh", "label": "Cache fresh?", "lane": 0, "column": 0, "decision": True},
                    {"id": "reuse", "label": "Reuse response", "lane": 0, "column": 1},
                    {"id": "validate", "label": "Check validator", "lane": 1, "column": 0},
                ],
                "edges": [
                    {"source": "fresh", "target": "reuse", "label": "Cache hit"},
                    {"source": "fresh", "target": "validate", "label": "Revalidation"},
                    {"source": "validate", "target": "reuse", "label": "304 Not Modified"},
                ],
            })
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", respond)
    client = TestClient(app)
    path = create(client, "Create one slide with a swimlane diagram for developers.", aspect_ratio="1:1", native=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert result["document"]["slides"][0]["spec"]["recipe"] == "diagram_swimlane"
    native = Presentation(io.BytesIO(client.post(path + "/export?format=pptx").content))
    shapes = list(native.slides[0].shapes)
    assert any(shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE and shape.auto_shape_type == MSO_AUTO_SHAPE_TYPE.DIAMOND
               for shape in shapes)
    connectors = [shape for shape in shapes if shape.shape_type == MSO_SHAPE_TYPE.LINE]
    assert len(connectors) == 3
    assert any(shape._element.spPr.prstGeom.get("prst") == "bentConnector3" for shape in connectors)
    for connector in connectors:
        properties = connector._element.nvCxnSpPr.cNvCxnSpPr
        assert properties.find(qn("a:stCxn")) is not None and properties.find(qn("a:endCxn")) is not None
    labels = {"Browser", "Server", "Cache hit", "Revalidation", "304 Not Modified"}
    assert labels <= {shape.text for shape in shapes if shape.has_text_frame}
    pdf = PdfReader(io.BytesIO(client.post(path + "/export?format=pdf").content))
    pdf_text = " ".join(pdf.pages[0].extract_text().split())
    assert all(label in pdf_text for label in labels), pdf_text
