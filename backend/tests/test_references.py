import base64
import io
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches
from sqlalchemy import select

from app.api import fetch_events
from app.composition import Intent, Outline, ResearchBrief, ResearchQueries, SlideContent
from app.config import Settings, settings
from app.db import Job, Session, SlidePreview, WorkerHeartbeat
from app.domain import Source
from app.main import app
from app.providers import Intelligence
from app.questions import Clarification
from app.queue import claim
from app.research import collect
from app.workflow import advance, align_query_period, requested_calendar_period
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
    path = create(client, asset_ids=[reference["id"]])
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


@pytest.mark.parametrize("web_setting", [None, "RESEARCH_API_URL", "BRAVE_WEB_SEARCH"])
@pytest.mark.parametrize("context_status", [None, 200, 403])
def test_detailed_web_research_uses_configured_endpoints(monkeypatch, tmp_path, web_setting, context_status):
    web_url = "https://search.example.test/web" if web_setting else "https://api.search.brave.com/res/v1/web/search"
    context_url = "https://search.example.test/context" if context_status is not None else ""
    for name in ("RESEARCH_API_URL", "BRAVE_WEB_SEARCH", "RESEARCH_CONTEXT_API_URL", "RESEARCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".env"
    url_setting = f"{web_setting}={web_url}\n" if web_setting else ""
    dotenv.write_text(f"{url_setting}RESEARCH_API_KEY=test-search-token\n", encoding="utf-8")
    if context_url:
        monkeypatch.setenv("RESEARCH_CONTEXT_API_URL", context_url)
    configuration = Settings(_env_file=dotenv)
    monkeypatch.setattr("app.research.settings", lambda: configuration)
    table = "| Year | Index close |\n| 2024 | 10 |\n| 2025 | 12 |"
    calls = []

    def get(url, *, params, headers, **kwargs):
        calls.append(url)
        assert headers["X-Subscription-Token"] == "test-search-token"
        request = httpx.Request("GET", url)
        result = {"title": "Exchange statistics", "url": "https://example.test/annual"}
        if url == context_url:
            if context_status != 200:
                return httpx.Response(context_status, request=request)
            return httpx.Response(200, request=request, json={"grounding": {"generic": [
                {**result, "snippets": ["About the exchange", table]}]}})
        assert params["extra_snippets"] is True
        return httpx.Response(200, request=request, json={"web": {"results": [{**result, "extra_snippets": [table]}]}})

    monkeypatch.setattr("app.research.httpx.get", get)
    assert collect("Index close 2024 2025", detailed=True)[0].excerpt.startswith(table)
    expected = [context_url] if context_url else []
    if context_status != 200:
        expected.append(web_url)
    assert calls == expected


def test_relative_annual_searches_correct_stale_model_dates():
    period = requested_calendar_period("Index returns in the last five years", date(2026, 11, 22))
    assert period == (2021, 2025)
    assert align_query_period("Index annual returns 2019 to 2023", period) == "Index annual returns 2021-2025"
    assert align_query_period("Top 5 by market cap as of 2023-12-31", period) == "Top 5 by market cap as of 2025-12-31"
    assert align_query_period("Largest companies by market cap at year end 2025", period).endswith("December 31, 2025 historical snapshot")
    assert align_query_period("Index return in 2022", period) == "Index return in 2022"
    assert requested_calendar_period("Last 5 years before 2021", date(2026, 11, 22)) is None


@pytest.mark.parametrize(("research", "style_question"), [(None, True), (False, True), (True, True), (None, False)])
def test_quantitative_deck_retrieves_and_cites_focused_chart_evidence(monkeypatch, research, style_question):
    monkeypatch.setattr(settings(), "research_api_key", "test-search-token")
    overview = Source(id="overview", title="Exchange overview", url="https://example.test/index", excerpt="Index statistics")
    ranking = Source(id="ranking", title="Exchange ranking", url="https://example.test/ranking", excerpt="A ranks first.")
    series = Source(id="series", title="Index annual series", url="https://example.test/series", excerpt="2024: 10, 2025: 12")
    filing = Source(id="filing", title="Company A filing", url="https://example.test/filing", excerpt="2025 net profit: $10M")
    evidence = {"Index annual returns": overview, "Constituent ranking": ranking,
                "Index annual closes": series, "Company A results": filing}
    queries = []

    def search(query, *, count, detailed):
        assert detailed
        queries.append((query, count))
        return [evidence[query]]

    structured = Intelligence.structured

    def plan(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Index", slide_count=2, requires_quantitative_evidence=not style_question)
        if schema is Clarification:
            if not style_question:
                return Clarification()
            return Clarification.model_validate({"questions": [{"id": "design", "type": "visual_style", "title": "Design",
                "options": [{"id": "data", "label": "Data Driven"}, {"id": "visual", "label": "Visual"}]}]})
        if schema is ResearchQueries:
            if style_question:
                assert data["answers"][0]["value"] == "Data Driven"
            return ResearchQueries(queries=["Index annual returns", "Constituent ranking"])
        if schema is Outline:
            assert [source["id"] for source in data["sources"]] == [overview.id, ranking.id]
            return Outline.model_validate({"title": "Index and Company A", "slides": [
                {"title": "Index performance", "layout": "summary", "research_query": "Index annual closes"},
                {"title": "Company A", "layout": "metrics", "research_query": "Company A results"},
            ]})
        if schema is SlideContent:
            if data["item"]["title"] == "Index performance":
                assert data["sources"][0]["id"] == series.id
                return SlideContent(title="Index performance", source_ids=[series.id], chart={
                    "chart": "line", "label": "Index points", "categories": ["2024", "2025"], "values": [10, 12]})
            assert data["sources"][0]["id"] == filing.id
            return SlideContent(title="Company A", source_ids=[filing.id], points=[{"title": "2025 net profit", "value": "$10M"}])
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr("app.workflow.collect", search)
    monkeypatch.setattr(Intelligence, "structured", plan)
    client = TestClient(app)
    path = create(client, "Two slides on the index and its top company", **({} if research is None else {"research": research}))
    work()
    if style_question:
        answers = {"answers": [{"question_id": "design", "option_ids": ["data"]}]}
        assert client.post(path + "/answers", json=answers).status_code == 202
        work()
    if research is False:
        assert queries == []
        question, = client.get(path + "/questions").json()["questions"]
        assert question["title"] == "Use web research for your charts and statistics?"
        answers = {"answers": [{"question_id": question["id"], "option_ids": ["use_web"]}]}
        assert client.post(path + "/answers", json=answers).status_code == 202
        work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert queries == [("Index annual returns", 4), ("Constituent ranking", 4), ("Index annual closes", 3), ("Company A results", 3)]
    index, company = result["document"]["slides"]
    assert {series.id, filing.id} <= {source["id"] for source in result["document"]["sources"]}
    assert index["source_ids"] == [series.id]
    assert next(el for el in index["elements"] if el["type"] == "chart")["series"][0]["values"] == [10, 12]
    assert next(el for el in index["elements"] if el["type"] == "table")["rows"][1:] == [["2024", "10"], ["2025", "12"]]
    assert company["source_ids"] == [filing.id]
    assert any(el.get("text") == "$10M" for el in company["elements"])


def test_data_driven_can_continue_without_web_research(monkeypatch):
    monkeypatch.setattr(settings(), "research_api_key", "test-search-token")
    structured = Intelligence.structured

    def plan(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Electricity generation", slide_count=1, visual_direction="Data Driven")
        return structured(self, schema, task, data, stage, image)

    def unexpected_search(*args, **kwargs):
        pytest.fail("Web research was declined")

    monkeypatch.setattr(Intelligence, "structured", plan)
    monkeypatch.setattr("app.workflow.collect", unexpected_search)
    client = TestClient(app)
    path = create(client, "One slide about electricity generation", research=False)
    work()
    question, = client.get(path + "/questions").json()["questions"]
    answers = {"answers": [{"question_id": question["id"], "option_ids": ["continue"]}]}
    assert client.post(path + "/answers", json=answers).status_code == 202
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert result["document"]["sources"] == []
    notice, = [event for event in fetch_events(result["id"], 0) if event["type"] == "research.completed"]
    assert notice["payload"]["uncertainties"] == ["Web research is off."]


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


@pytest.mark.parametrize(
    "provider, image_settings, expected_url, expected_model, expected_key, image_format",
    [
        pytest.param("openai", {}, "https://api.openai.com/v1/images/generations",
                     "gpt-image-1.5", "llm-fixture", "PNG", id="openai"),
        pytest.param("openai", {"image_openai_api_key": "legacy-fixture"},
                     "https://api.openai.com/v1/images/generations", "gpt-image-1.5", "legacy-fixture", "PNG",
                     id="legacy-openai-key"),
        pytest.param("openrouter", {"image_openai_api_key": "legacy-fixture"}, "https://openrouter.ai/api/v1/images",
                     "bytedance-seed/seedream-4.5", "llm-fixture", "JPEG", id="openrouter"),
        pytest.param("openrouter", {"image_gen_url": "https://images.example.test/generate",
                                    "image_gen_api_key": "images-fixture", "image_model": "example/image-model"},
                     "https://images.example.test/generate", "example/image-model", "images-fixture", "WEBP",
                     id="image-settings-overrides"),
    ],
)
def test_generated_illustration_is_embedded(monkeypatch, provider, image_settings, expected_url, expected_model,
                                            expected_key, image_format):
    configuration = settings()
    monkeypatch.setattr(configuration, "app_env", "development")
    llm_url = {"openai": "https://api.openai.com/v1", "openrouter": "https://openrouter.ai/api/v1"}[provider]
    monkeypatch.setattr(configuration, "llm_api_url", llm_url)
    monkeypatch.setattr(configuration, "llm_model_id", "test-model")
    monkeypatch.setattr(configuration, "llm_api_key", "llm-fixture")
    monkeypatch.setattr(configuration, "image_provider", provider)
    for name in ("image_gen_url", "image_gen_api_key", "image_model", "image_openai_api_key"):
        monkeypatch.setattr(configuration, name, image_settings.get(name, ""))
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(image, image_format)
    encoded = base64.b64encode(image.getvalue()).decode()

    def request(url, **kwargs):
        assert url == expected_url
        assert kwargs["headers"] == {"Authorization": f"Bearer {expected_key}"}
        payload = {
            "model": expected_model,
            "prompt": "Create a presentation illustration without captions, text or logos. An abstract blue shape"
                      " Deck imagery direction: Restrained editorial imagery coordinated with the deck palette.",
        }
        if provider == "openai":
            payload.update(size="1024x1024", quality="medium", n=1)
        assert kwargs["json"] == payload
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]}, request=httpx.Request("POST", url))

    def structured(self, schema, task, data, stage="planning", image=None):
        fixtures = {
            "Intent": {"subject": "Example", "slide_count": 1},
            "Clarification": {"questions": []},
            "Outline": {"title": "Example", "slides": [{"title": "Example", "layout": "editorial"}]},
            "SlideContent": {"title": "Example", "image_prompt": "An abstract blue shape"},
        }
        if schema.__name__ == "ArtDirection":
            from app.demo import demo_response
            return schema.model_validate(demo_response("ArtDirection", data))
        return schema.model_validate(fixtures[schema.__name__])

    monkeypatch.setattr(httpx, "post", request)
    monkeypatch.setattr(Intelligence, "structured", structured)
    client = TestClient(app)
    assert client.get("/api/v1/capabilities").json()["images"] is True
    path = create(client, generate_images=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    assert any(el["type"] == "image" for el in result["document"]["slides"][0]["elements"])
    response = client.post(path + "/export?format=pptx")
    assert response.status_code == 200
    deck = Presentation(io.BytesIO(response.content))
    assert any(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in deck.slides[0].shapes)


@pytest.mark.parametrize(("direction", "requested"), [
    ("Professional", True), ("Strategy consulting", True), ("Professional", False),
])
def test_web_research_only_embeds_and_credits_a_requested_portrait(monkeypatch, direction, requested):
    client = TestClient(app)
    configuration = settings()
    monkeypatch.setattr(configuration, "research_api_key", "test-search-token")
    monkeypatch.setattr(configuration, "research_api_url", "https://api.search.brave.com/res/v1/web/search")
    monkeypatch.setattr(configuration, "research_image_api_url", "https://api.search.brave.com/res/v1/images/search")
    monkeypatch.setattr(configuration, "llm_vision_enabled", False)
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
        if schema is Intent:
            return Intent(subject="Example", slide_count=1, visual_direction=direction, web_images_requested=requested)
        if schema is Clarification:
            return Clarification()
        if schema is Outline:
            assert ("Web image search is enabled" in task) == requested
            return Outline.model_validate({"title": "Example", "slides": [
                {"title": "Example", "layout": "editorial", "image_query": "Example official portrait"},
            ]})
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", plan)
    prompt = "Create one slide for students about Example."
    if requested:
        prompt += " Include an official portrait."
    path = create(client, prompt, research=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    document = result["document"]
    slide, = document["slides"]
    assert slide["layout"] == "editorial"
    portraits = [element for element in slide["elements"] if element["type"] == "image"]
    if not requested:
        assert not downloads and not portraits and not document["sources"]
        return
    source, = document["sources"]
    portrait, = portraits
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


@pytest.mark.parametrize("direction", ["Professional", "Strategy consulting"])
def test_image_queries_cannot_replace_planned_layouts_or_exhibits(monkeypatch, direction):
    configuration = settings()
    monkeypatch.setattr(configuration, "research_api_key", "test-search-token")
    source = Source(id="results", title="Quarterly results", excerpt="A: 1, B: 2")
    monkeypatch.setattr("app.workflow.collect", lambda query: [source])

    def unexpected_image_search(*args):
        pytest.fail("The planned slide has no slot for a web image")

    monkeypatch.setattr("app.workflow.source_web_image", unexpected_image_search)
    structured = Intelligence.structured

    def plan(self, schema, task, data, stage="planning", image=None):
        if schema is Intent:
            return Intent(subject="Example", slide_count=5, visual_direction=direction, web_images_requested=True)
        if schema is Clarification:
            return Clarification()
        if schema is Outline:
            return Outline.model_validate({"title": "Example", "slides": [
                {"title": "Intro", "layout": "cover", "image_query": "Photo A"},
                {"title": "Overview", "layout": "summary", "image_query": "Photo B"},
                {"title": "Results", "layout": "chart", "image_query": "Photo C"},
                {"title": "Breakdown", "layout": "research", "image_query": "Photo D"},
                {"title": "Action", "layout": "closing", "image_query": "Photo E"},
            ]})
        if schema is SlideContent:
            item = data["item"]
            if item["layout"] == "chart":
                return SlideContent(title=item["title"], source_ids=[source.id],
                                    chart={"label": "Results", "categories": ["A", "B"], "values": [1, 2]})
            table = [["Group", "Result"], ["A", "1"]] if item["layout"] == "research" else None
            return SlideContent(title=item["title"], source_ids=[source.id], table=table)
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", plan)
    client = TestClient(app)
    path = create(client, "Build a quarterly results deck with web research and photos.", research=True)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    slides = result["document"]["slides"]
    assert [slide["layout"] for slide in slides] == ["cover", "summary", "chart", "research", "closing"]
    assert any(element["type"] == "chart" for element in slides[2]["elements"])
    assert any(element["type"] == "table" for element in slides[3]["elements"])
    assert all(element["type"] != "image" for slide in slides for element in slide["elements"])
    assert [item["id"] for item in result["document"]["sources"]] == [source.id]


def test_resuming_an_older_image_draft_restores_the_planned_layout():
    client = TestClient(app)
    path = create(client, "Generate one slide in editorial style about accessibility for new hires.")
    work(6)
    identifier = path.rsplit("/", 1)[1]
    source = Source(title="Old web photo", asset_id="old_asset")
    with Session.begin() as db:
        job = db.scalar(select(Job).where(Job.presentation_id == identifier))
        assert job.checkpoint == "review"
        job.context = {
            **job.context,
            "draft": {**job.context["draft"], "layout": "editorial", "source_ids": [source.id]},
            "slide_content": {**job.context["slide_content"], "image_asset_id": source.asset_id,
                              "source_ids": [source.id]},
            "web_image": {"source": source.model_dump(mode="json"), "ratio": 1., "layout": "editorial"},
        }

    work()
    final = client.get(path).json()
    assert final["status"] == "COMPLETED"
    slide, = final["document"]["slides"]
    assert slide["layout"] == "cover"
    assert not slide["source_ids"] and not final["document"]["sources"]
