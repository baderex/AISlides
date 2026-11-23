import base64
import io
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.oxml import parse_xml
from pptx.util import Inches
from reportlab.pdfgen.canvas import Canvas

from app.branding import PPTX, choose_layout, compose_branded, inspect_template
from app.composition import OutlineItem, Point, SlideContent
from app.domain import BrandLayout, BrandSlot, BrandTemplate, Document, Slide, Table, Text, Theme
from app.export_review import inspect_pdf, renderer
from app.main import app
from app.rendering import contrast, to_pdf, validate_slide
from test_workflow import create, work


@pytest.fixture(autouse=True)
def avoid_office_render_in_worker(monkeypatch):
    monkeypatch.setattr("app.workflow.renderer", lambda: None)


def template_bytes(*, with_panel=False):
    native = Presentation()
    layout = native.slide_layouts[4]
    layout.name = "Corporate content"
    sample = native.slides.add_slide(layout)
    sample.shapes.title.text = "Private sample content"
    artwork = sample.shapes.add_textbox(Inches(.2), Inches(7), Inches(2), Inches(.3))
    artwork.text = "Master brand artwork"
    layout.shapes._spTree.insert_element_before(deepcopy(artwork._element), "p:extLst")
    if with_panel:
        layout = native.slide_layouts[5]
        layout.name = "Split panel"
        title = layout.placeholders[0]
        title.left, title.top, title.width, title.height = (Inches(value) for value in (.5, .5, 6, .6))
        background = parse_xml(
            '<p:bg xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:bgPr><a:gradFill><a:gsLst><a:gs pos="0"><a:srgbClr val="03522D"/></a:gs>'
            '<a:gs pos="100000"><a:srgbClr val="29BA74"/></a:gs></a:gsLst>'
            '<a:lin ang="8100000" scaled="1"/></a:gradFill><a:effectLst/></p:bgPr></p:bg>'
        )
        layout._element.cSld.insert(0, background)
        panel = sample.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, 0, 0, Inches(7), Inches(7.5))
        panel.fill.solid()
        panel.fill.fore_color.rgb = RGBColor.from_string("F2F2F2")
        layout.shapes._spTree.insert_element_before(deepcopy(panel._element), "p:extLst")
    output = io.BytesIO()
    native.save(output)
    return output.getvalue()


def branded_deck(client):
    raw = template_bytes()
    uploaded = client.post("/api/v1/assets", files={"file": ("Company.pptx", raw, PPTX)})
    assert uploaded.status_code == 201, uploaded.text
    url = f"/api/v1/skills/from-asset/{uploaded.json()['id']}"
    extracted = client.post(url).json()
    assert "Private sample content" in extracted["instructions"] and extracted["brand"] is None
    branded = client.post(url, params={"branded": True})
    assert branded.status_code == 201, branded.text
    skill = branded.json()
    assert skill["formats"] == ["4:3"]
    assert any(layout["name"] == "Corporate content" for layout in skill["brand"]["layouts"])
    assert client.get(f"/api/v1/skills/{skill['id']}/export").content == raw
    assert client.post("/api/v1/skills", json={key: value for key, value in skill.items() if key != "id"}).status_code == 422

    path = create(client, prompt="Generate three slides about quantum computing for executives.", skill_id=skill["id"])
    work()
    deck = client.get(path).json()
    if deck["status"] == "WAITING_FOR_USER":
        questions = client.get(path + "/questions").json()["questions"]
        assert client.post(path + "/answers", json={"answers": [
            {"question_id": question["id"], "use_default": True} for question in questions
        ]}).status_code == 202
        work()
        deck = client.get(path).json()
    assert deck["status"] == "COMPLETED", deck
    return path, deck


def test_branded_generation_preserves_native_placeholders_and_artwork():
    client = TestClient(app)
    path, deck = branded_deck(client)
    skill_id = deck["request"]["skill_id"]
    skill_path = f"/api/v1/skills/{skill_id}"
    assert client.delete(skill_path).status_code == 204
    assert client.get(skill_path + "/export").status_code == 404
    assert client.post("/api/v1/presentations", json={"prompt": "A new branded deck", "skill_id": skill_id}).status_code == 422
    assert deck["aspect_ratio"] == "4:3"
    first = deck["document"]["slides"][0]
    first["elements"][0]["text"] = "Updated branded title"
    assert first["elements"][0]["placeholder_idx"] == 0
    response = client.patch(path, json={"document": deck["document"], "revision": deck["revision"]})
    assert response.status_code == 200, response.text
    export = client.post(path + "/export")
    assert export.status_code == 200
    actual = Presentation(io.BytesIO(export.content))
    assert len(actual.slides) == 3 and actual.slide_width == Inches(10)
    assert actual.slides[0].shapes.title.text == "Updated branded title"
    assert actual.slides[1].slide_layout.name == "Corporate content"
    assert any(shape.text == "Master brand artwork" for shape in actual.slides[1].slide_layout.shapes if shape.has_text_frame)
    assert not any("Private sample content" in shape.text for slide in actual.slides
                   for shape in slide.shapes if shape.has_text_frame)


def test_branded_content_uses_content_slots_instead_of_cover_metadata_or_section_header():
    def slot(idx, kind, y, h, color):
        return BrandSlot(idx=idx, kind=kind, x=.06, y=y, w=.84, h=h,
                         size=34 if kind == "title" else 20, color=color, font="Arial")

    cover = BrandLayout(index=0, name="Title Slide", background="#FFFFFF", slots=[
        slot(0, "title", .30, .45, "#FFFFFF"), slot(1, "body", .91, .05, "#575757"),
    ])
    section = BrandLayout(index=1, name="Section header", section=True, background="#FFFFFF",
                          slots=[slot(0, "title", .40, .46, "#FFFFFF")])
    body = BrandLayout(index=2, name="Title and Text", background="#F2F2F2", slots=[
        slot(0, "title", .09, .09, "#29BA74"), slot(1, "body", .30, .60, "#575757"),
    ])
    brand = BrandTemplate(asset_id="template", layouts=[cover, section, body])
    document = Document(title="Rig scheduling", theme=Theme(accent="#03522D"))
    content = SlideContent(title=document.title, points=[
        Point(title="Assess moves", body="Compare road distance and travel time between ready pads."),
        Point(title="Plan around constraints", body="Maintain rig compatibility and contractual dates."),
        Point(title="Measure impact", body="Track move kilometres and rig idle hours."),
    ])

    assert choose_layout(brand.layouts, "summary", legacy=True) == cover
    assert choose_layout(brand.layouts, "cover") == cover
    assert choose_layout(brand.layouts, "section") == section
    for role in ("section", "summary", "process"):
        slide = compose_branded(OutlineItem(title=document.title, layout=role), content, document, set(), brand)
        assert slide.brand_layout_index == body.index
        heading, detail = slide.elements
        assert heading.placeholder_idx == 0 and detail.placeholder_idx == 1
        assert contrast(heading.color, slide.background) >= 4.5
        assert "Track move kilometres" in detail.text
        assert validate_slide(document, slide, set()) == []


def test_branded_pdf_exports_the_native_deck_when_a_renderer_is_available(monkeypatch):
    rendered = []

    def render(executable, raw):
        assert executable == "office-renderer"
        native = Presentation(io.BytesIO(raw))
        assert native.slides[1].slide_layout.name == "Corporate content"
        assert any(shape.has_text_frame and shape.text == "Master brand artwork"
                   for shape in native.slides[1].slide_layout.shapes)
        rendered.append(raw)
        output = io.BytesIO()
        pdf = Canvas(output)
        pdf.drawString(36, 72, "Master brand artwork")
        pdf.save()
        return output.getvalue()

    monkeypatch.setattr("app.api.renderer", lambda: "office-renderer")
    monkeypatch.setattr("app.api.render_native_pdf", render)
    client = TestClient(app)
    path, _ = branded_deck(client)
    response = client.post(path + "/export", params={"format": "pdf"})
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert len(rendered) == 1


def test_template_selects_decorated_layout_only_when_content_fits():
    theme = inspect_template(template_bytes(with_panel=True), "template", "Corporate")
    document = Document(title="Rig scheduling", theme=theme.theme, aspect_ratio="4:3")
    part = OutlineItem(title=document.title, layout="summary")
    short = SlideContent(title=part.title, points=[Point(title="Sequence rig moves")])
    long = SlideContent(title=part.title, points=[Point(title="Well scheduling", body="History and constraints " * 10)] * 5)

    slide = compose_branded(part, short, document, set(), theme.brand)
    layout = theme.brand.layouts[slide.brand_layout_index]
    assert layout.name == "Split panel"
    heading, body = slide.elements
    assert heading.placeholder_idx is not None and body.placeholder_idx is None
    assert body.x + body.w < layout.artwork_panel.w
    assert validate_slide(document, slide, set()) == []
    fallback = compose_branded(part, long, document, set(), theme.brand)
    assert fallback.brand_layout_index != layout.index


def test_canvas_artwork_renders_the_layout_without_sample_or_generated_content(monkeypatch):
    renderer_calls = []

    def render(executable, raw):
        assert executable == "office-renderer"
        slide, = Presentation(io.BytesIO(raw)).slides
        assert slide.slide_layout.name == "Corporate content"
        assert len(slide.shapes) == 0
        assert any(shape.name and shape.has_text_frame and shape.text == "Master brand artwork"
                   for shape in slide.slide_layout.shapes)
        renderer_calls.append(raw)
        output = io.BytesIO()
        pdf = Canvas(output, pagesize=(720, 540))
        pdf.setFillColorRGB(0, .4, .2)
        pdf.rect(0, 0, 720, 540, stroke=0, fill=1)
        pdf.showPage()
        pdf.save()
        return output.getvalue()

    monkeypatch.setattr("app.api.renderer", lambda: "office-renderer")
    monkeypatch.setattr("app.master_artwork.render_native_pdf", render)
    client = TestClient(app)
    path, deck = branded_deck(client)
    slide = deck["document"]["slides"][1]
    response = client.get(path + "/master-artwork", params={
        "layout": slide["layout"], "index": slide["brand_layout_index"], "background": slide["background"],
    })
    assert response.status_code == 200, response.text
    with PILImage.open(io.BytesIO(response.content)) as backdrop:
        assert backdrop.getpixel((0, 0))[:3] == (0, 102, 51)
    preview = client.get(f"{path}/slides/{slide['id']}/preview")
    assert preview.status_code == 200
    with PILImage.open(io.BytesIO(preview.content)) as image:
        assert image.getpixel((0, 0)) == (0, 102, 51)
        assert image.getcolors(2) is None
    assert len(renderer_calls) == 1

def test_rendered_pdf_review_reports_text_fit_overlap_and_font_substitution():
    output = io.BytesIO()
    pdf = Canvas(output, pagesize=(720, 540))
    pdf.setFont("Helvetica", 22)
    pdf.drawString(72, 480, "Overlapping lines")
    pdf.drawString(72, 480, "Second line")
    pdf.drawString(72, 180, "Out of place")
    pdf.save()
    document = Document(title="Review", aspect_ratio="4:3", slides=[Slide(title="Review", elements=[
        Text(x=.09, y=.03, w=.5, h=.13, text="Overlapping lines", font="Georgia"),
        Text(x=.09, y=.2, w=.5, h=.13, text="Out of place"),
    ])])
    slide, = inspect_pdf(output.getvalue(), document)
    assert base64.b64decode(slide["image"]).startswith(b"\xff\xd8")
    assert {issue["code"] for issue in slide["issues"]} >= {"text-fit", "text-overlap", "font-substitution"}


def test_rendered_pdf_review_flags_changed_table_sign():
    table = Table(x=.1, y=.2, w=.8, h=.3, rows=[["Operating profit", "-1"], ["Cash", "20"]])
    document = Document(title="Results", slides=[Slide(title="Results", elements=[table])])
    raw = to_pdf(document, None)
    slide, = inspect_pdf(raw, document)
    assert slide["issues"] == []

    table.rows[0][1] = "1"
    slide, = inspect_pdf(raw, document)
    issue, = slide["issues"]
    assert issue["code"] == "table-content" and "cell 1, 2" in issue["message"]


def test_review_download_matches_the_rendered_powerpoint(monkeypatch):
    reviewed = []

    def review(raw, document):
        reviewed.append(raw)
        assert len(Presentation(io.BytesIO(raw)).slides) == len(document.slides) == 3
        return []

    monkeypatch.setattr("app.api.review_powerpoint", review)
    client = TestClient(app)
    path, deck = branded_deck(client)
    endpoint = path + "/export/review"
    assert client.post(endpoint, params={"expected": deck["revision"] - 1}).status_code == 409
    response = client.post(endpoint, params={"expected": deck["revision"]})
    assert response.status_code == 200, response.text
    assert client.get(path + "/exports/" + response.json()["id"]).content == reviewed[0]


@pytest.mark.skipif(not renderer(), reason="LibreOffice Impress is unavailable")
def test_real_impress_renders_the_branded_export():
    client = TestClient(app)
    path, deck = branded_deck(client)
    response = client.post(path + "/export/review", params={"expected": deck["revision"]})
    assert response.status_code == 200, response.text
    slides = response.json()["slides"]
    assert len(slides) == 3 and all(base64.b64decode(slide["image"]).startswith(b"\xff\xd8") for slide in slides)
    assert not any(issue["code"] == "text-fit" for slide in slides for issue in slide["issues"]), slides[0]["issues"]
