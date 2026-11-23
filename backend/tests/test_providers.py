import json
from urllib.parse import urlsplit

import httpx
import pytest

from app.composition import Intent, Outline, SlideContent
from app.config import Settings, settings
from app.providers import Intelligence, ModelReply, ProviderError


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
        assert body["max_completion_tokens" if "api.openai.com" in url else "max_tokens"] > 0
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
        if path == "/api/chat":
            assert body["options"]["num_predict"] > 0
        elif path == "/v1/messages":
            assert body["max_tokens"] > 0
        else:
            assert body["generationConfig"]["maxOutputTokens"] > 0
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

    def request(model, system, content, image, contract, **kwargs):
        prompts.append(system)
        return ModelReply(json.dumps(next(responses)), 90, 30)

    monkeypatch.setattr(client, "_request", request)
    slide = client.structured(SlideContent, "Write a slide.", {}, "slide")
    assert slide.points[0].title == "First finding"
    assert slide.table is None
    assert "points.0.title: use at most 65 characters" in prompts[1]
    assert "table: use an array" in prompts[2]
    assert "A" * 66 not in prompts[1]
    assert client.usage["input"] == 270


def test_metrics_slide_ignores_unused_evidence_and_closing_copy_in_json_fallback(monkeypatch):
    client = openrouter(monkeypatch)
    calls = []
    content = {"title": "Al Rajhi Bank", "points": [{"title": "Market cap", "value": "SAR 399bn"}],
               "source_ids": ["filing"], "exhibit_observations": [{"amount": "not available", "unit": "billion riyals"}],
               "next_step": "x" * 146}

    def post(url, body, headers):
        calls.append(body)
        if len(calls) == 1:
            contract = body["response_format"]["json_schema"]["schema"]
            assert set(contract["properties"]) == {"title", "subtitle", "points", "source_ids", "notes"}
            assert set(contract["$defs"]) == {"Point"}
            httpx.Response(404, request=httpx.Request("POST", url)).raise_for_status()
        return {"choices": [{"message": {"content": json.dumps(content)}}], "usage": {"prompt_tokens": 90, "completion_tokens": 30}}

    monkeypatch.setattr(client, "_post", post)
    slide = client.structured(SlideContent, "Write the profile.", {"item": {"layout": "metrics", "title": content["title"]}}, "slide")
    assert len(calls) == 2
    assert slide.points[0].value == "SAR 399bn" and slide.source_ids == ["filing"]
    assert slide.next_step == "" and not slide.exhibit_observations
    assert client.usage["by_stage"]["slide"] == {"calls": 1, "input": 90, "output": 30}


def test_closing_repairs_only_visible_copy_without_resending_sources(monkeypatch):
    client = openrouter(monkeypatch)
    calls = []

    def post(url, body, headers):
        calls.append(body)
        if len(calls) == 1:
            answer = {"title": "Next decision", "notes": "Preserve this text", "source_ids": ["filing"], "next_step": "x" * 146}
        else:
            assert set(body["response_format"]["json_schema"]["schema"]["properties"]) == {"next_step"}
            brief = json.loads(body["messages"][1]["content"])
            assert brief["fields"] == {"next_step": "x" * 146}
            assert "sources" not in brief
            answer = {"next_step": "Review the filing."}
        return {"choices": [{"message": {"content": json.dumps(answer)}}], "usage": {"prompt_tokens": 90, "completion_tokens": 30}}

    monkeypatch.setattr(client, "_post", post)
    slide = client.structured(SlideContent, "Write a closing.", {
        "item": {"layout": "closing", "title": "Next decision"}, "sources": [{"id": "filing", "excerpt": "original sources"}]}, "slide")
    assert len(calls) == 2
    assert slide.next_step == "Review the filing." and slide.notes == "Preserve this text" and slide.source_ids == ["filing"]
    assert client.usage["by_stage"]["slide"]["validation_failures"] == 1


def test_chart_observations_require_numeric_values_and_repair_keeps_the_chart(monkeypatch):
    client = openrouter(monkeypatch)
    chart = {"label": "Revenue", "categories": ["2025"], "values": [12]}
    source = {"id": "s1", "excerpt": "Revenue in 2025 was USD 12m."}
    row = {"metric": "Revenue", "period": "2025", "unit": "m", "currency": "USD", "source_id": "s1", "quote": source["excerpt"]}

    def post(url, body, headers):
        props = body["response_format"]["json_schema"]["schema"]["properties"]
        assert "exhibit_observations" in props
        if "chart" in props:
            answer = {"title": "Revenue", "chart": chart, "source_ids": ["s1"], "exhibit_observations": [{**row, "amount": "USD 12m"}]}
        else:
            assert set(props) == {"exhibit_observations"}
            request = json.loads(body["messages"][1]["content"])
            assert request["sources"] == [source] and request["exhibit"] == chart
            assert "use a plain numeric amount" in body["messages"][0]["content"]
            answer = {"exhibit_observations": [{**row, "amount": 12}]}
        return {"choices": [{"message": {"content": json.dumps(answer)}}]}

    monkeypatch.setattr(client, "_post", post)
    slide = client.structured(SlideContent, "Write the chart.", {
        "item": {"layout": "chart", "title": "Revenue"}, "sources": [source]}, "slide")
    assert slide.chart.values == [12] and slide.source_ids == ["s1"]
    assert slide.exhibit_observations[0].amount == 12


def test_detailed_design_brief_passes_intent_planning_on_first_response(monkeypatch):
    client = openrouter(monkeypatch)
    direction = (
        "Premium McKinsey/BCG-style consulting presentation. Off-white background (#F7F7F2), "
        "dark navy typography (#17283C), restrained emerald accent (#16856B). Strong answer-first headlines, "
        "annotated exhibits, thin dividers, precise alignment, compact source notes. No decorative stock imagery. "
        "Every slide should communicate one conclusion."
    )
    prompt = f"Create a 6-slide board presentation for Northstar Cloud. Design direction: {direction}"
    calls = []

    def post(url, body, headers):
        calls.append(body)
        response = {"subject": "The Growth Engine Is Leaking", "visual_direction": direction, "slide_count": 6}
        return {"choices": [{"message": {"content": json.dumps(response)}}]}

    monkeypatch.setattr(client, "_post", post)
    intent = client.structured(Intent, "Extract intent.", {"prompt": prompt})

    assert len(calls) == 1
    assert json.loads(calls[0]["messages"][1]["content"])["prompt"] == prompt
    assert intent.slide_count == 6
    assert intent.visual_direction.startswith("Premium McKinsey/BCG-style consulting presentation.")
    assert len(intent.visual_direction) <= 250


def test_openrouter_reports_fields_when_corrections_fail(monkeypatch):
    client = openrouter(monkeypatch)
    bad = {"title": "Exhibit", "table": "private model text"}
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: ModelReply(json.dumps(bad), 90, 30))

    with pytest.raises(ProviderError, match="The model API returned invalid content for the slide step") as failure:
        client.structured(SlideContent, "Write a slide.", {}, "slide")
    assert "table: use an array" in str(failure.value)
    assert "private model text" not in str(failure.value)


def test_planned_exhibit_is_required_and_repaired(monkeypatch):
    client = openrouter(monkeypatch)
    prompts = []
    decision = {"action": "Approve", "request": "the trial", "scope": "", "if_missed": "Pause expansion.",
                "gates": [{"label": "Readiness", "threshold": "≥95%", "detail": ""}]}

    def request(model, system, content, image, contract, **kwargs):
        assert "decision" in contract["required"]
        assert contract["properties"]["decision"]["$ref"] == "#/$defs/Decision"
        assert "cover_callout" not in contract["properties"]
        assert "response_schema" in kwargs
        prompts.append(system)
        return ModelReply(json.dumps({"title": "Decision", "decision": decision if len(prompts) > 1 else None}), 90, 30)

    monkeypatch.setattr(client, "_request", request)
    slide = client.structured(SlideContent, "Write the decision slide.", {"required_exhibit": "decision"}, "slide")
    assert slide.decision.model_dump() == decision
    assert "populate the decision object for this slide" in prompts[1]


def test_maturity_heatmap_schema_separates_scores_from_categories(monkeypatch):
    client = openrouter(monkeypatch)
    calls = []
    rows = [{"label": label, "cells": scores} for label, scores in [
        ("Temperature monitoring", [2, 5]), ("Route planning", [3, 4]), ("Warehouse automation", [2, 4]),
        ("Quality inspection", [4, 5]), ("Exception response", [1, 4]),
    ]]
    visual = {"type": "heatmap", "columns": ["Current", "Target"], "rows": rows}

    def post(url, body, headers):
        calls.append(body)
        response = dict(visual)
        if len(calls) == 1:
            response.update(weights=[0.2, 0.8], levels=["1", "2", "3", "4", "5"])
        return {"choices": [{"message": {"content": json.dumps({"title": "Maturity", "visual": response})}}]}

    monkeypatch.setattr(client, "_post", post)
    slide = client.structured(SlideContent, "Compare current and target maturity.",
                              {"required_exhibit": "visual", "visual_choice": {"type": "heatmap"}}, "slide")

    contract = calls[0]["response_format"]["json_schema"]["schema"]

    def resolve(ref):
        return contract["$defs"][ref["$ref"].split("/")[-1]]

    cell_types = set()
    for option in contract["properties"]["visual"]["anyOf"]:
        fields = resolve(option)["properties"]
        assert "weights" not in fields
        row = resolve(fields["rows"]["items"])
        cell_type = row["properties"]["cells"]["items"]["type"]
        assert ("levels" in fields) == (cell_type == "string")
        cell_types.add(cell_type)
    assert cell_types == {"number", "string"}
    assert len(calls) == 2
    assert "omit weights for a heatmap. Use numeric cells without levels" in calls[1]["messages"][0]["content"]
    assert [row.model_dump() for row in slide.visual.rows] == rows


def test_explicit_visible_point_count_is_repaired(monkeypatch):
    client = openrouter(monkeypatch)
    prompts = []
    points = [{"title": "Camp table"}, {"title": "Reading corner"}, {"title": "Extra use case"}]

    def request(model, system, content, image, contract, **kwargs):
        prompts.append(system)
        return ModelReply(json.dumps({"title": "Use cases", "points": points if len(prompts) == 1 else points[:2]}), 90, 30)

    monkeypatch.setattr(client, "_request", request)
    slide = client.structured(SlideContent, "Show the two use cases.", {"point_count": 2}, "slide")
    assert [point.title for point in slide.points] == ["Camp table", "Reading corner"]
    assert "return exactly 2 items in points and no extra items" in prompts[1]


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
    assert "JSON schema:" not in constrained["messages"][0]["content"]
    assert "JSON schema:" in fallback["messages"][0]["content"]
    assert "JSON schema:" in repair["messages"][0]["content"]
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


def test_budget_check_counts_native_schema_and_checkpoint_usage(monkeypatch):
    client = openrouter(monkeypatch)
    client.usage.update(input=73620, output=13259)
    monkeypatch.setattr(settings(), "ai_job_token_budget", 88000)
    monkeypatch.setattr(client, "_post", lambda *args: pytest.fail("Over-budget request reached the provider"))

    with pytest.raises(ProviderError) as error:
        client.structured(Outline, "Plan the deck.", {"slide_count": 6}, "outline")

    assert "86,879 of 88,000 tokens" in str(error.value)
    assert "next outline request" in str(error.value)
    assert "AI_JOB_TOKEN_BUDGET" in str(error.value)
    assert client.usage["input"] + client.usage["output"] == 86879


def test_output_limit_uses_remaining_checkpoint_budget_and_charges_failed_response(monkeypatch):
    client = openrouter(monkeypatch)
    client.usage.update(input=70000, output=18150)
    monkeypatch.setattr(settings(), "llm_model_id", "deepseek/deepseek-v4.1-flash")
    monkeypatch.setattr(settings(), "ai_job_token_budget", 90000)
    calls = []

    def post(url, body, headers):
        calls.append(body)
        return {"choices": [{"message": {"content": "{}"}}], "usage": {
            "prompt_tokens": 1000, "completion_tokens": 850, "completion_tokens_details": {"reasoning_tokens": 600}}}

    monkeypatch.setattr(client, "_post", post)
    with pytest.raises(ProviderError, match="90,000 of 90,000"):
        client.structured(Intent, "Extract intent.", {}, "analyze")

    call, = calls
    assert call["max_tokens"] == 850
    assert call["reasoning"] == {"enabled": False}
    assert client.usage["by_stage"]["analyze"] == {"calls": 1, "input": 1000, "output": 850,
                                                   "reasoning": 600, "validation_failures": 1}
    assert Intelligence(usage=client.usage).usage == client.usage


@pytest.mark.parametrize("count", [1, 6, 30])
def test_truncated_outline_gets_one_larger_attempt_with_room_for_requested_slides(monkeypatch, count):
    client = openrouter(monkeypatch)
    limits = []

    def post(url, body, headers):
        limits.append(body["max_tokens"])
        return {"choices": [{"finish_reason": "length", "message": {"content": None}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": body["max_tokens"]}}

    monkeypatch.setattr(client, "_post", post)
    with pytest.raises(ProviderError, match="output limit for the outline"):
        client.structured(Outline, "Plan the deck.", {"slide_count": count}, "outline")
    assert limits == [1024 + 512 * count, 2 * (1024 + 512 * count)]
    assert client.usage["output"] == sum(limits)
    assert client.usage["by_stage"]["outline"]["truncated"] == 2


@pytest.mark.parametrize(("layout", "expected"), [(" Line_Chart ", "chart"), ("data-table", "research"), (" Metrics ", "metrics")])
def test_outline_retries_invalid_layout_and_normalizes_known_layout_names(monkeypatch, layout, expected):
    client = openrouter(monkeypatch)
    prompts = []
    layouts = iter(["private model label", layout])

    def post(url, body, headers):
        prompts.append(body["messages"][0]["content"])
        outline = {"title": "TASI", "slides": [{"title": "TASI performance", "layout": next(layouts)}]}
        return {"choices": [{"message": {"content": json.dumps(outline)}}]}

    monkeypatch.setattr(client, "_post", post)
    outline = client.structured(Outline, "Plan one slide.", {
        "prompt": "generate impressive slide about TASI preformance in the last 5 years and top 5 companies",
        "slide_count": 1,
    }, "outline")

    assert [slide.layout for slide in outline.slides] == [expected]
    correction = prompts[1].split("Correct these issues: ", 1)[1]
    assert correction.startswith("slides.0.layout: use one of")
    assert "'chart'" in correction and "'research'" in correction
    assert "private model label" not in correction
