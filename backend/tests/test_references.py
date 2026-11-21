import base64
import io

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches
from sqlalchemy import select

from app.composition import Outline, ResearchBrief
from app.config import settings
from app.db import Job, Session, SlidePreview, WorkerHeartbeat
from app.main import app
from app.providers import Intelligence
from app.questions import Clarification
from app.queue import claim
from app.workflow import advance
from test_workflow import create, work


def test_powerpoint_upload_extracts_reference_text():
    client = TestClient(app)
    incoming = Presentation()
    slide = incoming.slides.add_slide(incoming.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = "Confidential performance review"
    body = io.BytesIO()
    incoming.save(body)
    response = client.post("/api/v1/assets", files={"file": (
        "review.pptx", body.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")})
    assert response.status_code == 201, response.text
    path = create(client, "Generate one editorial slide about a performance review for the board.", asset_ids=[response.json()["id"]])
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert result["document"]["sources"][0]["excerpt"] == "Confidential performance review"
    with Session() as db:
        record = db.scalar(select(SlidePreview))
        assert record and record.presentation_id == result["id"]


def test_research_follow_up_persists_earlier_answers(monkeypatch):
    original = Intelligence.structured

    def research(self, schema, task, data, stage="planning", image=None):
        if schema is ResearchBrief:
            return ResearchBrief.model_validate({
                "summary": "The reference conflicts with the stated objective.",
                "uncertainties": ["The deployment year differs across references."],
                "follow_up": {"type": "free_text", "id": "source_conflict", "title": "Which year should the deck use?"},
            })
        return original(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", research)
    client = TestClient(app)
    reference = client.post("/api/v1/assets", files={"file": ("notes.txt", b"One source says 2025, another 2026.", "text/plain")}).json()
    path = create(client, mode="guided", asset_ids=[reference["id"]])
    work()
    first = client.get(path + "/questions").json()
    assert len(first["questions"]) == 2
    defaults = {"answers": [{"question_id": question["id"], "use_default": True} for question in first["questions"]]}
    assert client.post(path + "/answers", json=defaults).status_code == 202
    work()
    second = client.get(path + "/questions").json()
    assert second["waiting"] and second["questions"][0]["id"] == "source_conflict"
    assert len(second["answers"]) == 2 and len(second["history"]) == 1
    assert client.post(path + "/answers", json={"answers": [{"question_id": "source_conflict", "text": "2026"}]}).status_code == 202
    work()
    assert client.get(path).json()["status"] == "COMPLETED"
    assert len(client.get(path + "/questions").json()["answers"]) == 3


def test_worker_heartbeat_recovers_checkpoint_without_waiting_for_lease():
    client = TestClient(app)
    path = create(client)
    with Session.begin() as db:
        db.add(WorkerHeartbeat(id="departed", seen=1))
    with Session() as db:
        first = claim(db, "departed")
    with Session() as db:
        second = claim(db)
    assert first and second and first[0] == second[0] and first[1] != second[1]
    advance(*first)
    assert client.get(path).json()["status"] == "CREATED"
    advance(*second)
    with Session() as db:
        assert db.get(Job, second[0]).checkpoint == "clarify"


def test_generated_illustration_is_embedded(monkeypatch):
    configuration = settings()
    monkeypatch.setattr(configuration, "openai_api_key", "fixture")
    monkeypatch.setattr(configuration, "image_provider", "openai")
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(image, "PNG")
    encoded = base64.b64encode(image.getvalue()).decode()

    def request(url, **kwargs):
        assert url == "https://api.openai.com/v1/images/generations"
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]}, request=httpx.Request("POST", url))

    def structured(self, schema, task, data, stage="planning", image=None):
        fixtures = {
            "Intent": {"subject": "Example", "slide_count": 1},
            "Clarification": {"questions": []},
            "Outline": {"title": "Example", "slides": [{"title": "Example", "layout": "editorial"}]},
            "SlideContent": {"title": "Example", "image_prompt": "An abstract blue shape"},
        }
        return schema.model_validate(fixtures[schema.__name__])

    monkeypatch.setattr(httpx, "post", request)
    monkeypatch.setattr(Intelligence, "structured", structured)
    client = TestClient(app)
    path = create(client, provider="openai", generate_images=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert any(el["type"] == "image" for el in result["document"]["slides"][0]["elements"])
    response = client.post(path + "/export?format=pptx")
    assert response.status_code == 200
    deck = Presentation(io.BytesIO(response.content))
    assert any(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in deck.slides[0].shapes)


def test_web_research_embeds_and_credits_a_requested_portrait(monkeypatch):
    client = TestClient(app)
    configuration = settings()
    monkeypatch.setattr(configuration, "research_api_key", "test-search-token")
    monkeypatch.setattr(configuration, "research_api_url", "https://api.search.brave.com/res/v1/web/search")
    monkeypatch.setattr(configuration, "research_image_api_url", "https://api.search.brave.com/res/v1/images/search")
    monkeypatch.setattr(configuration, "vision_model", "")
    picture = io.BytesIO()
    Image.new("RGB", (300, 450), "blue").save(picture, "JPEG")
    source_url = "https://museum.gov/collection/portrait"
    thumbnail = "https://imgs.search.brave.com/portrait"
    downloads = []

    def respond(request):
        if request.url.host == "api.search.brave.com":
            assert request.headers["X-Subscription-Token"] == "test-search-token"
            assert request.url.path.endswith("/images/search")
            assert request.url.params["q"] == "Example official portrait"
            assert request.url.params["safesearch"] == "strict"
            return httpx.Response(200, json={"results": [
                {"title": "Untrusted thumbnail", "url": source_url, "thumbnail": {"src": "https://other.example/portrait"}},
                {"title": "Museum portrait", "url": source_url, "thumbnail": {"src": thumbnail}},
            ]})
        assert str(request.url) == thumbnail
        assert "X-Subscription-Token" not in request.headers
        downloads.append(thumbnail)
        return httpx.Response(200, content=picture.getvalue(), headers={"Content-Type": "image/jpeg"})

    http_client = httpx.Client
    monkeypatch.setattr("app.workflow.collect", lambda query: [])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: http_client(transport=httpx.MockTransport(respond), **kwargs))
    structured = Intelligence.structured

    def plan(self, schema, task, data, stage="planning", image=None):
        if schema is Clarification:
            return Clarification()
        if schema is Outline:
            assert "Web image search is enabled" in task
            return Outline.model_validate({"title": "Example", "slides": [
                {"title": "Example", "layout": "summary", "image_query": "Example official portrait"},
            ]})
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", plan)
    path = create(client, "Create one slide for students with an official portrait of Example in a professional style.",
                  mode="professional", research=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    source, = result["document"]["sources"]
    slide, = result["document"]["slides"]
    portrait, = [element for element in slide["elements"] if element["type"] == "image"]
    assert downloads == [thumbnail]
    assert source["url"] == source_url and source["asset_id"] == portrait["asset_id"]
    assert source["id"] in slide["source_ids"]
    assert abs(portrait["w"] * (16 / 9) / portrait["h"] - (300 / 450)) < .001
    assert client.get(f"/api/v1/assets/{portrait['asset_id']}").headers["content-type"] == "image/png"
    exported = client.post(f"{path}/export?format=pptx")
    assert exported.status_code == 200
    deck = Presentation(io.BytesIO(exported.content))
    assert any(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in deck.slides[0].shapes)
    assert source_url in deck.slides[0].notes_slide.notes_text_frame.text
