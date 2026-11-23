import io
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader
from sqlalchemy import select, update

from app.db import Job, PresentationRecord, Session
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


def create(client, prompt="Generate three slides about quantum computing.", *, native=False, **settings):
    response = client.post("/api/v1/presentations", json={"prompt": prompt, **settings})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    if native:
        with Session.begin() as db:
            job = db.scalars(select(Job).where(Job.presentation_id == identifier)).one()
            job.context = {**job.context, "layout_engine": "native"}
    return f"/api/v1/presentations/{identifier}"


def test_create_rejects_model_overrides():
    client = TestClient(app)
    response = client.post("/api/v1/presentations", json={
        "prompt": "Generate three slides about quantum computing.", "provider": "override", "model": "override",
    })
    assert response.status_code == 422
    assert {issue["loc"][-1] for issue in response.json()["detail"]} == {"provider", "model"}


def test_guided_workflow_and_native_export():
    client = TestClient(app)
    path = create(client, native=True)
    with Session.begin() as db:
        item = db.get(PresentationRecord, path.rsplit("/", 1)[1])
        item.request = {**item.request, "provider": "unused-legacy-provider", "model": "unused-legacy-model"}
    work(2)
    assert client.get(path).json()["status"] == "WAITING_FOR_USER"
    waiting = client.get(f"{path}/questions").json()
    assert waiting["waiting"] and len(waiting["questions"]) == 2
    bad = client.post(f"{path}/answers", json={"answers": [{"question_id": "audience", "option_ids": ["technical"]}]})
    assert bad.status_code == 422
    answers = {"answers": [{"question_id": "audience", "option_ids": ["executive"]},
                           {"question_id": "visual_direction", "option_ids": ["editorial"]}]}
    assert client.post(f"{path}/answers", json=answers).status_code == 202
    work(3)
    planned = client.get(path).json()
    assert planned["status"] == "GENERATING" and planned["slide_count"] == 0
    outline = next(event["payload"]["outline"] for event in fetch_events(planned["id"], 0)
                   if event["type"] == "outline.completed")
    assert planned["planned_slide_titles"] == [slide["title"] for slide in outline["slides"]]
    work(1)
    reviewing = client.get(path).json()
    assert reviewing["status"] == "REVIEWING" and reviewing["slide_count"] == 0
    assert reviewing["review_draft"]["index"] == 0 and reviewing["review_draft"]["slide"]["elements"]
    stopped = client.post(f"{path}/cancel").json()
    assert stopped["planned_slide_titles"] == planned["planned_slide_titles"]
    assert stopped["review_draft"] == reviewing["review_draft"]
    assert client.post(f"{path}/generate").status_code == 202
    work(1)
    partial = client.get(path).json()
    assert partial["slide_count"] == 1
    assert partial["planned_slide_titles"] == planned["planned_slide_titles"]
    assert partial["status"] == "GENERATING"
    assert "review_draft" not in partial
    work()
    final = client.get(path).json()
    assert final["slide_count"] == 3 and final["status"] == "COMPLETED"
    assert "planned_slide_titles" not in final
    assert final["document"]["theme"]["font"] == "Manrope"
    assert final["document"]["theme"]["design"]["heading_font"] == "Source Serif 4"
    stream = fetch_events(final["id"], 0)
    assert [item["type"] for item in stream].count("slide.completed") == 3
    latest = fetch_events(final["id"], stream[-2]["id"])
    assert len(latest) == 1 and latest[0]["type"] == "generation.completed"
    assert client.post(f"{path}/answers", json=answers).status_code == 409

    document = final["document"]
    slide = document["slides"][0]
    comparison = document["slides"][1]
    icons = [element for element in comparison["elements"] if element["type"] == "image"]
    assert [icon["asset_id"].split("_")[0] for icon in icons] == ["lucide-cpu", "lucide-atom"]
    assert not any("Missing image" in warning for warning in comparison["warnings"])
    for icon in icons:
        response = client.get(f"/api/v1/assets/{icon['asset_id']}")
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(response.content)).getchannel("A").getextrema() == (0, 255)
    assert client.get("/api/v1/assets/lucide-unknown_000000").status_code == 404
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
    assert sum(hasattr(shape, "image") for shape in deck.slides[1].shapes) == len(icons)
    response = client.post(f"{path}/export?format=pdf")
    pdf = PdfReader(io.BytesIO(response.content))
    assert len(pdf.pages) == 3 and "Verified user edit" in pdf.pages[0].extract_text()
    assert len(pdf.pages[1].images) == len(icons)
    response = client.get(f"{path}/slides/{slide['id']}/preview")
    assert Image.open(io.BytesIO(response.content)).format == "PNG"
    versions = client.get(f"{path}/versions").json()
    assert [entry["label"] for entry in versions] == ["Manual edit", "Generated presentation"]


def test_explicit_consulting_style_changes_the_generated_exhibits():
    client = TestClient(app)
    path = create(client, prompt="generate bcg style about ecommerce in the middle east", native=True)
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
    assert all(slide["spec"]["identity_version"] == 2 for slide in doc["slides"])
    assert all(slide["background"] == "#FFFFFF" for slide in doc["slides"])
    exhibit = next(el for el in doc["slides"][1]["elements"] if el["type"] == "table")
    assert exhibit["rows"][0] == ["Observation", "Implication"]
    assert exhibit["header_fill"] == "#006841"
    pptx = Presentation(io.BytesIO(client.post(path + "/export?format=pptx").content))
    table = next(shape.table for shape in pptx.slides[1].shapes if shape.has_table)
    assert str(table.cell(0, 0).fill.fore_color.rgb) == "006841"
    assert table.cell(0, 0).text == "Observation"


def test_explicit_style_overrides_unlocked_preset_defaults():
    client = TestClient(app)
    path = create(client, "One slide about planning for executives in dark luxury style. Background #222222. "
                  "Accent #CAAE78. Headings font: Georgia. Body font: Verdana.", skill_id="strategy", research=False, native=True)
    work()
    generated = client.get(path).json()
    assert generated["status"] == "COMPLETED"
    theme = generated["document"]["theme"]
    assert (theme["background"], theme["accent"], theme["design"]["heading_font"], theme["font"]) == (
        "#222222", "#CAAE78", "Georgia", "Verdana")


def test_brand_kit_keeps_owned_logo_and_only_protected_theme_roles():
    client = TestClient(app)
    image = io.BytesIO()
    Image.new("RGB", (120, 60), "#006841").save(image, format="PNG")
    logo = client.post("/api/v1/assets", files={"file": ("identity.png", image.getvalue(), "image/png")}).json()["id"]
    response = client.post("/api/v1/skills", json={
        "name": "Brand kit", "description": "Green logo and accent.", "category": "Brand",
        "theme": {"accent": "#006841", "brand_kit": {"protected": ["accent"], "logo_asset_id": logo}},
    })
    assert response.status_code == 201
    path = create(client, "One slide about planning for executives in dark luxury style. Background #222222. Accent #CAAE78.",
                  skill_id=response.json()["id"], research=False, native=True)
    work()
    generated = client.get(path).json()
    assert generated["status"] == "COMPLETED"
    doc = generated["document"]
    assert doc["theme"]["background"] == "#222222" and doc["theme"]["accent"] == "#006841"
    assert any(element.get("asset_id") == logo for element in doc["slides"][0]["elements"])
    assert not any("Missing image" in warning for warning in doc["slides"][0]["warnings"])
    assert client.post(f"{path}/export?format=pptx").status_code == 200


def test_cancel_retry_and_expired_worker_fencing():
    client = TestClient(app)
    path = create(client, generate=False)
    assert client.get(path).json()["request"]["generate"] is False
    assert client.post(path + "/generate").status_code == 202
    assert client.get(path).json()["request"]["generate"] is True
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


def test_manual_retry_renews_only_the_attempt_token_allowance(monkeypatch):
    from app.config import settings

    client = TestClient(app)
    path = create(client, "Create one slide for engineers about PKCE in a technical style.", research=False)
    with Session.begin() as db:
        job = db.scalar(select(Job))
        job.state = "failed"
        job.context = {**job.context, "usage": {"input": 68016, "output": 13059, "estimated_cost": .24}}
    monkeypatch.setattr(settings(), "ai_job_token_budget", 16000)
    assert client.post(path + "/generate").status_code == 202
    work(1)
    assert client.post(path + "/generate").status_code == 202
    with Session() as db:
        job = db.scalar(select(Job))
        assert job.checkpoint == "clarify"
        usage = job.context["usage"]
        assert (usage["input"], usage["output"], usage["attempt_start_tokens"]) == (68026, 13069, 81075)
        assert usage["by_stage"]["analyze"]["calls"] == 1
    client.post(path + "/cancel")
    assert client.post(path + "/generate").status_code == 202
    with Session() as db:
        assert db.scalar(select(Job)).context["usage"] == usage


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
    monkeypatch.setattr(settings(), "app_env", "development")
    monkeypatch.setattr(settings(), "llm_api_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings(), "llm_api_key", "")
    client = TestClient(app)
    path = create(client)
    work()
    assert client.get(path).json()["status"] == "FAILED"
    with Session() as db:
        job = db.scalar(select(Job))
        assert job.checkpoint == "analyze" and "LLM_MODEL_ID" in job.error
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
    created = first.post("/api/v1/skills", json={"name": "Private skill", "description": "Custom recipe", "category": "Custom"})
    assert created.status_code == 201
    skill_id = created.json()["id"]
    library = {item["id"]: item for item in first.get("/api/v1/skills").json()}
    assert library[skill_id]["can_delete"] and not library["strategy"]["can_delete"]
    assert second.delete(f"/api/v1/skills/{skill_id}").status_code == 404
    assert first.delete("/api/v1/skills/strategy").status_code == 404
    assert first.get(f"/api/v1/skills/{skill_id}").status_code == 200
    assert first.get("/api/v1/skills/strategy").status_code == 200
