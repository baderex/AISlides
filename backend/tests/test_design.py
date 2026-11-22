import io
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image, ImageColor
from pptx import Presentation
from pypdf import PdfReader

from app.composition import Outline, SlideContent
from app.design import ArtDirection
from app.main import app
from app.providers import Intelligence, ProviderError
from app.rendering import contrast
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
    path = create(client, "Three slides for investors about finance in dark luxury style.", asset_ids=[accounts["id"]])
    work()
    saved = client.get(path).json()
    assert saved["status"] == "FAILED"
    theme = saved["document"]["theme"]
    assert theme["design"]["heading_font"] == "Georgia"
    assert client.post(path + "/generate").status_code == 202
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert len(design_calls) == 1 and plan_attempts == [theme, theme]
    cover, trend, results = result["document"]["slides"]
    assert [s["layout"] for s in (cover, trend, results)] == ["cover", "chart", "research"]
    assert cover["background"] == "#191817"
    assert next(el for el in cover["elements"] if el.get("text") == cover["title"])["font"] == "Georgia"
    chart = next(el for el in trend["elements"] if el["type"] == "chart")
    table = next(el for el in results["elements"] if el["type"] == "table")
    assert table["rows"] == rows and table["fill"] == "#F7F2E8"
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
    line = next(shape.chart for shape in ppt.slides[1].shapes if shape.has_chart)
    assert str(line.series[0].format.line.color.rgb) == chart["color"][1:]
    assert str(line.category_axis.tick_labels.font.color.rgb) == chart["label_color"][1:]
    with ZipFile(io.BytesIO(raw)) as archive:
        assert any(name.startswith("ppt/embeddings/") for name in archive.namelist())
    pdf = PdfReader(io.BytesIO(client.post(path + "/export?format=pdf").content))
    assert len(pdf.pages) == 3
    assert all(cell in pdf.pages[2].extract_text() for row in rows for cell in row)
