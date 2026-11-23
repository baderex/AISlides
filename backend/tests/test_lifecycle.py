import base64
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation

from app import artifacts, worker
from app.api import fetch_events
from app.asset_lifecycle import collect_orphans, reconcile_storage
from app.auth import LOCAL_USER
from app.config import Settings, settings
from app.db import Asset, Job, PresentationRecord, Session, StudioEvent, uid
from app.domain import Document, Slide, Source
from app.imagery import render_image
from app.main import app
from app.previews import fingerprint
from app.queue import claim, heartbeat
from app.storage import OBJECT_PREFIX, Storage
from test_workflow import create

client = TestClient(app)


def draft(title="Draft"):
    path = create(client, title, generate=False)
    deck = client.get(path).json()
    deck["document"]["slides"] = [{"id": "one", "title": "Original"}]
    response = client.patch(path, json={"revision": 0, "document": deck["document"]})
    assert response.status_code == 200, response.text
    return path, response.json()


def test_active_worker_outlives_initial_lease(monkeypatch):
    create(client)
    monkeypatch.setattr(settings(), "lease_seconds", 2)
    started, release = threading.Event(), threading.Event()
    tickets = []

    def slow_checkpoint(key, token):
        tickets.append((key, token))
        started.set()
        assert release.wait(12)
        with Session.begin() as db:
            job = db.get(Job, key)
            assert job.lease == token
            job.state = "complete"

    monkeypatch.setattr(worker, "advance", slow_checkpoint)
    with ThreadPoolExecutor() as workers:
        first = workers.submit(worker.work, True)
        try:
            assert started.wait(5)
            with Session() as db:
                initial = db.get(Job, tickets[0][0]).lease_until
            while time.time() < initial + 1.1:
                time.sleep(.1)
            second = workers.submit(worker.work, True)
            second.result(timeout=4)
            assert len(tickets) == 1
            with Session() as db:
                job = db.get(Job, tickets[0][0])
                assert job.lease_until > initial
                expires = job.lease_until
                heartbeat(db, "other", (job.id, "stale-token"))
                assert db.get(Job, job.id).lease_until == expires
        finally:
            release.set()
        first.result(timeout=5)


def test_shared_and_version_only_assets_survive_collection():
    upload = client.post("/api/v1/assets", files={"file": ("source.txt", b"retained evidence", "text/plain")}).json()["id"]
    first = create(client, generate=False, asset_ids=[upload])
    second, saved = draft()
    document = saved["document"]
    document["sources"] = [{"id": "source", "title": "Evidence", "asset_id": upload}]
    assert client.patch(second, json={"revision": 1, "document": document}).status_code == 200
    document["sources"] = []
    assert client.patch(second, json={"revision": 2, "document": document}).status_code == 200
    assert client.delete(first).status_code == 204
    assert Storage().get(upload) == b"retained evidence"
    assert client.post(second + "/versions/2/restore?expected=3").status_code == 200
    assert client.delete(second).status_code == 204
    with Session() as db:
        assert db.get(Asset, upload) is None
    with pytest.raises(FileNotFoundError):
        Storage().get(upload)


def test_abandoned_uploads_grace_storage_quota_and_uncommitted_objects(monkeypatch):
    monkeypatch.setattr(settings(), "account_asset_limit_bytes", 5)
    upload = lambda: client.post("/api/v1/assets", files={"file": ("note.txt", b"hello", "text/plain")})
    key = upload().json()["id"]
    assert upload().status_code == 429
    collect_orphans()
    assert Storage().get(key) == b"hello"
    with Session.begin() as db:
        db.get(Asset, key).created = "2000-01-01"
    collect_orphans()
    assert client.get("/api/v1/assets/" + key).status_code == 404
    storage, lost = Storage(), uid()
    storage.put(lost, b"crashed write", "text/plain")
    os.utime(storage.root / OBJECT_PREFIX / lost, (1, 1))
    reconcile_storage()
    with pytest.raises(FileNotFoundError):
        storage.get(lost)
    assert upload().status_code == 201


def test_ai_admission_is_shared_atomic_and_survives_deck_deletion(monkeypatch):
    monkeypatch.setattr(settings(), "account_ai_concurrency", 1)
    monkeypatch.setattr(settings(), "account_ai_requests_daily", 1)
    url = "/api/v1/presentations"
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda i: client.post(url, json={"prompt": "Account test " + str(i)}), range(2)))
    assert sorted(r.status_code for r in replies) == [201, 429]
    path = url + "/" + next(r.json()["id"] for r in replies if r.status_code == 201)
    assert client.post(path + "/generate").status_code == 202
    assert client.delete(path).status_code == 204
    other, _ = draft("Another presentation")
    assert client.post(other + "/chat", json={"instruction": "Update the title"}).status_code == 429
    assert client.post(other + "/generate").status_code == 429


def test_production_signup_default_and_provider_reservation(monkeypatch):
    monkeypatch.delenv("ALLOW_SIGNUP")
    assert not Settings(app_env="production", _env_file=None).allow_signup
    assert Settings(app_env="production", allow_signup=True, _env_file=None).allow_signup
    monkeypatch.setattr(settings(), "provider_daily_budget_usd", .0001)
    path, _ = draft()
    response = client.post(path + "/chat", json={"instruction": "Update the title"})
    assert response.status_code == 429 and "provider spend" in response.json()["detail"]


def test_search_and_cursor_with_tied_timestamps():
    with Session.begin() as db:
        for i in range(105):
            title = "Match 100%" if i == 0 else f"Deck {i}"
            db.add(PresentationRecord(id=f"deck-{i:03}", owner_id=LOCAL_USER, title=title,
                                      updated="2026-01-01", request={}, document={"slides": [], "aspect_ratio": "16:9"}))
    url = "/api/v1/presentations"
    first = client.get(url)
    second = client.get(url, params={"cursor": first.headers["X-Next-Cursor"]})
    assert len({row["id"] for row in first.json() + second.json()}) == 105
    assert "X-Next-Cursor" not in second.headers
    assert [row["id"] for row in client.get(url, params={"q": "100%"}).json()] == ["deck-000"]
    assert client.get(url, params={"cursor": "malformed"}).status_code == 422


def test_bounded_event_history_and_preview_dependencies(monkeypatch):
    path, _ = draft()
    key = path.rsplit("/", 1)[1]
    monkeypatch.setattr(settings(), "event_replay_limit", 2)
    with Session.begin() as db:
        db.add_all(StudioEvent(presentation_id=key, type="example", payload={"n": i}) for i in range(5))
    batch = fetch_events(key, 0)
    assert [event["payload"]["n"] for event in batch] == [3, 4]
    assert fetch_events(key, batch[-1]["id"]) == []
    slide = Slide(title="Evidence", source_ids=["source"])
    document = Document(title="Draft", slides=[slide], sources=[Source(id="source", title="Data", excerpt="One")])
    first = fingerprint(document, slide)
    document.sources[0].excerpt = "Two"
    second = fingerprint(document, slide)
    document.theme.font = "Georgia"
    third = fingerprint(document, slide)
    slide.source_ids = []
    assert len({first, second, third, fingerprint(document, slide)}) == 4


def test_queued_export_capacity_snapshot_unicode_and_ownership(monkeypatch):
    monkeypatch.setattr(artifacts, "notify", lambda _: None)
    monkeypatch.setattr(settings(), "artifact_queue_capacity", 1)
    monkeypatch.setattr(settings(), "export_queue_slide_threshold", 1)
    title = "عرض تقديمي"
    path, deck = draft(title)
    response = client.post(path + "/export")
    assert response.status_code == 202
    location = response.headers["Location"]
    job = client.get(location).json()
    assert job["state"] == "queued" and artifacts.status(job["id"], "another-owner") is None
    assert client.post(path + "/export").status_code == 429
    document = deck["document"]
    document["title"] = "Changed"
    document["slides"] = []
    assert client.patch(path, json={"revision": 1, "document": document}).status_code == 200
    with Session() as db:
        ticket = artifacts.claim_artifact(db, "file-worker")
    artifacts.execute(*ticket)
    status = client.get(location).json()
    assert status["state"] == "complete"
    result = client.get(status["result"]["download"])
    assert len(Presentation(io.BytesIO(result.content)).slides) == 1
    header = result.headers["Content-Disposition"]
    assert 'filename="presentation.pptx"' in header
    assert unquote(header.partition("UTF-8''")[2]) == title + ".pptx"


def test_image_regeneration_caches_only_the_same_prompt(monkeypatch):
    path = create(client)
    with Session() as db:
        job_id, token = claim(db)
    monkeypatch.setattr(settings(), "image_provider", "openai")
    monkeypatch.setattr(settings(), "image_gen_api_key", "test")
    output = io.BytesIO()
    Image.new("RGB", (10, 10)).save(output, "PNG")
    encoded = base64.b64encode(output.getvalue()).decode()
    calls = []

    def request(url, **kwargs):
        calls.append(kwargs["json"]["prompt"])
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", request)
    keys = [render_image(job_id, token, 0, LOCAL_USER, prompt) for prompt in ("morning", "morning", "evening")]
    assert len(calls) == 2 and keys[0] == keys[1] and keys[1] != keys[2]
    assert client.delete(path).status_code == 204
    for key in set(keys):
        with pytest.raises(FileNotFoundError):
            Storage().get(key)


def test_per_account_spend_and_calls_share_provider_reservations(monkeypatch):
    from app.limits import LimitExceeded, billing_account, provider_call, settle_spend

    monkeypatch.setattr(settings(), "account_daily_budget_usd", .001)
    monkeypatch.setattr(settings(), "account_ai_concurrency", 1)
    with billing_account(LOCAL_USER):
        with provider_call(900) as reserved:
            with pytest.raises(LimitExceeded, match="concurrent provider"):
                with provider_call(10):
                    pass
            settle_spend(reserved, 500)
        with pytest.raises(LimitExceeded, match="account.*spend limit"):
            with provider_call(600):
                pass
        with provider_call(500):
            pass


def test_template_survives_soft_delete_until_last_deck_releases_it():
    from app.branding import PPTX
    from test_powerpoint_studio import template_bytes

    uploaded = client.post("/api/v1/assets", files={"file": ("Brand.pptx", template_bytes(), PPTX)})
    key = uploaded.json()["id"]
    template = client.post("/api/v1/skills/from-asset/" + key + "?branded=true").json()["id"]
    first = create(client, generate=False, skill_id=template)
    second = create(client, generate=False, skill_id=template)
    assert client.delete(first).status_code == 204
    assert client.delete("/api/v1/skills/" + template).status_code == 204
    assert client.get("/api/v1/assets/" + key).status_code == 200
    assert client.delete(second).status_code == 204
    assert client.get("/api/v1/assets/" + key).status_code == 404


def test_missing_worker_preview_is_regenerated():
    from app.db import ArtifactJob
    from sqlalchemy import select

    path, _ = draft()
    url = path + "/slides/one/preview"
    first = client.get(url)
    assert first.status_code == 200
    with Session() as db:
        cached = db.scalar(select(ArtifactJob).where(ArtifactJob.kind == "preview"))
        Storage().delete(cached.id)
    assert client.get(url).content == first.content


def test_failed_storage_deletion_is_retried_after_commit(monkeypatch):
    from app.asset_lifecycle import drain_deletes
    from app.db import PendingDelete

    key = client.post("/api/v1/assets", files={"file": ("source.txt", b"temporary", "text/plain")}).json()["id"]
    path = create(client, generate=False, asset_ids=[key])
    def unavailable(self, key):
        raise OSError("storage unavailable")

    with monkeypatch.context() as failure:
        failure.setattr(Storage, "delete", unavailable)
        assert client.delete(path).status_code == 204
    with Session() as db:
        assert db.get(Asset, key) is None and db.get(PendingDelete, key) is not None
    drain_deletes()
    with pytest.raises(FileNotFoundError):
        Storage().get(key)
