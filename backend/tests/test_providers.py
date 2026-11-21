import json

import pytest

from app.composition import SlideContent
from app.config import settings
from app.providers import Intelligence, ProviderError


def openrouter(monkeypatch):
    monkeypatch.setattr(settings(), "openrouter_api_key", "fixture-key")
    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    return Intelligence("openrouter", "test-model")


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

    with pytest.raises(ProviderError, match="OpenRouter returned invalid content for the slide step") as failure:
        client.structured(SlideContent, "Write a slide.", {}, "slide")
    assert "table: use an array" in str(failure.value)
    assert "private model text" not in str(failure.value)
