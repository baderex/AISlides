import io
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader
from sqlalchemy import select, update

from app.db import Job, Session
from app.main import app
from app.api import fetch_events
from app.queue import claim
from app.workflow import advance


def work(steps=100):
    for _ in range(steps):
        with Session() as db:
            ticket = claim(db)
        if not ticket:
            break
        advance(*ticket)


def create(client, prompt="Generate three slides about quantum computing.", **settings):
    response = client.post("/api/v1/presentations", json={"prompt": prompt, "provider": "demo", **settings})
    assert response.status_code == 201, response.text
    return f"/api/v1/presentations/{response.json()['id']}"


def test_guided_workflow_and_native_export():
    client = TestClient(app)
    path = create(client)
    work(2)
    assert client.get(path).json()["status"] == "WAITING_FOR_USER"
    waiting = client.get(f"{path}/questions").json()
    assert waiting["waiting"] and len(waiting["questions"]) == 2
    bad = client.post(f"{path}/answers", json={"answers": [{"question_id": "audience", "option_ids": ["technical"]}]})
    assert bad.status_code == 422
    answers = {"answers": [{"question_id": "audience", "option_ids": ["executive"]},
                           {"question_id": "visual_direction", "option_ids": ["editorial"]}]}
    assert client.post(f"{path}/answers", json=answers).status_code == 202
    work(4)
    partial = client.get(path).json()
    assert partial["slide_count"] == 1
    assert partial["status"] == "GENERATING"
    work()
    final = client.get(path).json()
    assert final["slide_count"] == 3 and final["status"] == "COMPLETED"
    assert final["document"]["theme"]["font"] == "Georgia"
    stream = fetch_events(final["id"], 0)
    assert [item["type"] for item in stream].count("slide.completed") == 3
    latest = fetch_events(final["id"], stream[-2]["id"])
    assert len(latest) == 1 and latest[0]["type"] == "generation.completed"
    assert client.post(f"{path}/answers", json=answers).status_code == 409

    document = final["document"]
    slide = document["slides"][0]
    next(element for element in slide["elements"] if element["type"] == "text")["text"] = "Verified user edit"
    assert client.patch(path, json={"document": document, "revision": final["revision"]}).status_code == 200
    assert client.patch(path, json={"document": document, "revision": final["revision"]}).status_code == 409
    response = client.post(f"{path}/export?format=pptx")
    assert response.status_code == 200, response.text[:200] if response.status_code != 200 else ""
    deck = Presentation(io.BytesIO(response.content))
    assert len(deck.slides) == 3
    assert abs(deck.slide_width / deck.slide_height - 16/9) < .0001
    assert any(shape.has_text_frame and shape.text == "Verified user edit" for shape in deck.slides[0].shapes)
    assert any(shape.auto_shape_type for shape in deck.slides[0].shapes if hasattr(shape, "auto_shape_type") and shape.shape_type == 1)
    response = client.post(f"{path}/export?format=pdf")
    pdf = PdfReader(io.BytesIO(response.content))
    assert len(pdf.pages) == 3 and "Verified user edit" in pdf.pages[0].extract_text()
    response = client.get(f"{path}/slides/{slide['id']}/preview")
    assert Image.open(io.BytesIO(response.content)).format == "PNG"
    versions = client.get(f"{path}/versions").json()
    assert [entry["label"] for entry in versions] == ["Manual edit", "Generated presentation"]


def test_explicit_consulting_style_changes_the_generated_exhibits():
    client = TestClient(app)
    path = create(client, prompt="generate bcg style about ecommerce in the middle east")
    work()
    waiting = client.get(path + "/questions").json()["questions"]
    assert [question["type"] for question in waiting] == ["audience"]
    response = client.post(path + "/answers", json={
        "answers": [{"question_id": waiting[0]["id"], "option_ids": ["executive"]}],
    })
    assert response.status_code == 202
    work()
    generated = client.get(path).json()
    assert generated["status"] == "COMPLETED"
    doc = generated["document"]
    assert doc["theme"]["profile"] == "consulting"
    assert all(slide["background"] == "#FFFFFF" for slide in doc["slides"])
    exhibit = next(el for el in doc["slides"][1]["elements"] if el["type"] == "table")
    assert exhibit["rows"][0] == ["Observation", "Implication"]
    assert exhibit["header_fill"] == "#006841"
    pptx = Presentation(io.BytesIO(client.post(path + "/export?format=pptx").content))
    table = next(shape.table for shape in pptx.slides[1].shapes if shape.has_table)
    assert str(table.cell(0, 0).fill.fore_color.rgb) == "006841"
    assert table.cell(0, 0).text == "Observation"


def test_cancel_retry_and_expired_worker_fencing():
    client = TestClient(app)
    path = create(client)
    with Session() as db:
        first = claim(db)
    assert first
    assert client.post(f"{path}/cancel").json()["status"] == "CANCELLED"
    advance(*first)
    assert client.get(path).json()["status"] == "CANCELLED"
    assert client.post(f"{path}/generate").status_code == 202
    with Session() as db:
        second = claim(db)
        assert second and second[1] != first[1]
        db.execute(update(Job).where(Job.id == second[0]).values(lease_until=1))
        db.commit()
    with Session() as db:
        third = claim(db)
    assert third and third[1] != second[1]
    advance(*second)
    with Session() as db:
        assert db.get(Job, third[0]).lease == third[1]
    advance(*third)
    work()
    assert client.get(path).json()["status"] == "WAITING_FOR_USER"


def test_atomic_claim():
    client = TestClient(app)
    create(client)
    def attempt(_):
        with Session() as db:
            return claim(db)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))
    assert sum(result is not None for result in results) == 1


def test_provider_failure_keeps_checkpoint(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "openai_api_key", "")
    client = TestClient(app)
    path = create(client, provider="openai")
    work()
    assert client.get(path).json()["status"] == "FAILED"
    with Session() as db:
        job = db.scalar(select(Job))
        assert job.checkpoint == "analyze" and "Configure" in job.error
    assert client.post(path + "/generate").status_code == 202


def test_owner_isolation_and_upload_validation():
    first, second = TestClient(app), TestClient(app)
    for client, email in [(first, "one@example.com"), (second, "two@example.com")]:
        assert client.post("/api/v1/auth/register", json={"email": email, "password": "test-password-123"}).status_code == 201
    path = create(first, generate=False)
    assert second.get(path).status_code == 404
    assert second.get("/api/v1/presentations").json() == []
    assert first.post("/api/v1/assets", files={"file": ("fake.png", b"untrusted bytes", "image/png")}).status_code == 415
    uploaded = first.post("/api/v1/assets", files={"file": ("notes.txt", b"Project context", "text/plain")}).json()
    assert second.get("/api/v1/assets/" + uploaded["id"]).status_code == 404
    assert second.post("/api/v1/presentations", json={"prompt": "A request", "asset_ids": [uploaded["id"]]}).status_code == 422
