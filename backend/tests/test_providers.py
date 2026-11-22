import json
from urllib.parse import urlsplit

import httpx
import pytest

from app.composition import Outline, SlideContent
from app.config import Settings, settings
from app.providers import Intelligence, ProviderError


def openrouter(monkeypatch):
    configuration = settings()
    monkeypatch.setattr(configuration, "app_env", "development")
    monkeypatch.setattr(configuration, "llm_api_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(configuration, "llm_model_id", "test-model")
    monkeypatch.setattr(configuration, "llm_api_key", "fixture-key")
    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    return Intelligence()


def test_llm_settings_only_load_from_dotenv(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("LLM_API_URL=https://file.example.test/v1\nLLM_MODEL_ID=file-model\nLLM_API_KEY=file-key\nAPP_ENV=development\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_URL", "https://process.example.test/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "process-model")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    configuration = Settings(_env_file=dotenv)
    assert configuration.llm_api_url == "https://file.example.test/v1"
    assert configuration.llm_model_id == "file-model"
    assert configuration.app_env == "test"
    assert configuration.llm_api_key == "file-key"
    monkeypatch.setenv("LLM_API_KEY", "process-key")
    assert Settings(_env_file=dotenv).llm_api_key == "process-key"


@pytest.mark.parametrize(("base", "endpoint"), [
    ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/chat/completions"),
    ("https://gateway.example.test/v1/chat/completions", "https://gateway.example.test/v1/chat/completions"),
])
def test_chat_completions_use_the_configured_endpoint_model_and_key(monkeypatch, base, endpoint):
    monkeypatch.setattr(settings(), "app_env", "development")
    monkeypatch.setattr(settings(), "llm_api_url", base)
    monkeypatch.setattr(settings(), "llm_model_id", "test-model")
    monkeypatch.setattr(settings(), "llm_api_key", "fixture-key")
    client = Intelligence()

    def post(url, body, headers):
        assert url == endpoint
        assert body["model"] == "test-model"
        assert headers == {"Authorization": "Bearer fixture-key"}
        return {"choices": [{"message": {"content": '{"title": "Exhibit"}'}}]}

    monkeypatch.setattr(client, "_post", post)
    assert client.structured(SlideContent, "Write a slide.", {}).title == "Exhibit"


@pytest.mark.parametrize(("base", "path", "auth", "payload"), [
    ("https://api.anthropic.com/v1", "/v1/messages", "x-api-key", {"content": [{"type": "text", "text": '{"title": "Exhibit"}'}]}),
    ("https://generativelanguage.googleapis.com/v1beta", "/v1beta/models/test-model:generateContent", "x-goog-api-key",
     {"candidates": [{"content": {"parts": [{"text": '{"title": "Exhibit"}'}]}}]}),
    ("http://127.0.0.1:11434", "/api/chat", "Authorization", {"message": {"content": '{"title": "Exhibit"}'}}),
])
def test_native_providers_use_the_same_model_settings(monkeypatch, base, path, auth, payload):
    monkeypatch.setattr(settings(), "app_env", "development")
    monkeypatch.setattr(settings(), "llm_api_url", base)
    monkeypatch.setattr(settings(), "llm_model_id", "test-model")
    monkeypatch.setattr(settings(), "llm_api_key", "fixture-key")
    client = Intelligence()

    def post(url, body, headers):
        assert urlsplit(url).path == path
        assert headers[auth] == ("Bearer fixture-key" if auth == "Authorization" else "fixture-key")
        if auth != "x-goog-api-key":
            assert body["model"] == "test-model"
        return payload

    monkeypatch.setattr(client, "_post", post)
    assert client.structured(SlideContent, "Write a slide.", {}).title == "Exhibit"


def test_openrouter_retries_with_validation_feedback(monkeypatch):
    client = openrouter(monkeypatch)
    prompts = []
    responses = iter([
        {"title": "Exhibit", "points": [{"title": "A" * 66}], "table": None},
        {"title": "Exhibit", "points": [{"title": "First finding"}], "table": {"rows": [["Market"]]}},
        {"title": "Exhibit", "points": [{"title": "First finding"}], "table": None},
    ])

    def request(model, system, content, image, contract):
        prompts.append(system)
        return json.dumps(next(responses)), 90, 30

    monkeypatch.setattr(client, "_request", request)
    slide = client.structured(SlideContent, "Write a slide.", {}, "slide")
    assert slide.points[0].title == "First finding"
    assert slide.table is None
    assert "points.0.title: use at most 65 characters" in prompts[1]
    assert "table: use an array" in prompts[2]
    assert "A" * 66 not in prompts[1]
    assert client.usage["input"] == 270


def test_openrouter_reports_fields_when_corrections_fail(monkeypatch):
    client = openrouter(monkeypatch)
    bad = {"title": "Exhibit", "table": "private model text"}
    monkeypatch.setattr(client, "_request", lambda *args: (json.dumps(bad), 90, 30))

    with pytest.raises(ProviderError, match="The model API returned invalid content for the slide step") as failure:
        client.structured(SlideContent, "Write a slide.", {}, "slide")
    assert "table: use an array" in str(failure.value)
    assert "private model text" not in str(failure.value)


def test_outline_falls_back_and_repairs_the_count_without_rejecting_extra_fields(monkeypatch):
    client = openrouter(monkeypatch)
    calls = []
    first = {"title": "Introduction", "layout": "cover", "content": {"unused": "Slide copy"}}
    second = {"title": "Learn by doing", "layout": "process", "content": ["Extra slide copy"]}

    def post(url, body, headers):
        calls.append(body)
        if len(calls) == 1:
            request = httpx.Request("POST", url)
            response = httpx.Response(404, request=request)
            response.raise_for_status()
        slides = [first] if len(calls) == 2 else [first, second]
        return {"choices": [{"message": {"content": json.dumps({"title": "Workshop", "slides": slides})}}]}

    monkeypatch.setattr(client, "_post", post)
    outline = client.structured(Outline, "Plan two workshop slides.", {"slide_count": 2}, "outline")

    assert [slide.layout for slide in outline.slides] == ["cover", "process"]
    assert "content" not in outline.slides[0].model_dump()
    constrained, fallback, repair = calls
    assert constrained["provider"] == {"require_parameters": True}
    assert constrained["response_format"]["type"] == "json_schema"
    envelope = constrained["response_format"]["json_schema"]
    assert envelope["strict"]
    contract = envelope["schema"]
    slides_schema = contract["properties"]["slides"]
    assert slides_schema["minItems"] == slides_schema["maxItems"] == 2
    for obj in (contract, contract["$defs"]["OutlineItem"]):
        assert set(obj["required"]) == set(obj["properties"])
        assert obj["additionalProperties"] is False
        assert all("default" not in field for field in obj["properties"].values())
    assert fallback["response_format"] == repair["response_format"] == {"type": "json_object"}
    assert "slides: return exactly 2 slides (received 1)" in repair["messages"][0]["content"]
