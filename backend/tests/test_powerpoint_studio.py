import base64
import io
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches
from reportlab.pdfgen.canvas import Canvas

from app.branding import PPTX
from app.domain import Document, Slide, Text
from app.export_review import inspect_pdf, renderer
from app.main import app
from test_workflow import create, work


def template_bytes():
    native = Presentation()
    layout = native.slide_layouts[4]
    layout.name = "Corporate content"
    sample = native.slides.add_slide(layout)
    sample.shapes.title.text = "Private sample content"
    artwork = sample.shapes.add_textbox(Inches(.2), Inches(7), Inches(2), Inches(.3))
    artwork.text = "Master brand artwork"
    layout.shapes._spTree.insert_element_before(deepcopy(artwork._element), "p:extLst")
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
