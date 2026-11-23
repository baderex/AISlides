"""Deterministic model replies shared by worker and browser tests."""

import json
import re
from contextlib import ExitStack, contextmanager
from unittest.mock import patch
from xml.sax.saxutils import escape

from app.config import settings
from app.providers import Intelligence, ModelReply


@contextmanager
def model_fixture():
    config = settings()
    url = "http://model.invalid/v1"
    original = Intelligence._request

    def request(self, model, system, content, image, schema, **options):
        if config.llm_api_url != url:
            return original(self, model, system, content, image, schema, **options)
        response = model_response(schema["title"], json.loads(content))
        return ModelReply(json.dumps(response), input_tokens=10, output_tokens=10)

    with ExitStack() as stack:
        stack.enter_context(patch.multiple(config, llm_api_url=url, llm_api_key="", llm_model_id="fixture",
                                           llm_vision_enabled=False, research_api_key="", image_provider="none",
                                           image_gen_api_key="", image_openai_api_key=""))
        stack.enter_context(patch.object(Intelligence, "_request", request))
        yield


def model_response(schema: str, data: dict) -> dict:
    if schema == "Intent":
        prompt = data["prompt"]
        match = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]?slides?\b", prompt, re.I)
        words = "zero one two three four five six seven eight nine ten".split()
        count = None
        if match:
            value = match[1].lower()
            count = max(1, min(30, int(value) if value.isdecimal() else words.index(value)))
        subject = re.split(r"\babout\b", prompt, 1, flags=re.I)[-1].strip(" .")[:130]
        audience = re.search(r"\b(?:for|audience:)\s+([^.,;]+)", prompt, re.I)
        style = re.search(r"(dark[ -]luxury|editorial|academic|minimal corporate|bold creative|modern technology)", prompt, re.I)
        return {"subject": subject or "Untitled presentation", "slide_count": count,
                "audience": audience[1][:250] if audience else "", "visual_direction": style[0] if style else "",
                "missing": [key for key, value in {"audience": audience, "visual_direction": style}.items() if not value]}
    if schema == "ArtDirection":
        theme = dict(data["baseline"])
        direction = data["direction"].lower()
        if "dark" in direction and "luxury" in direction and not data["locked_template"]:
            theme.update(background="#191817", ink="#F7F2E8", muted="#D1C8B6", surface="#302E2A", accent="#D3B478")
        theme.pop("profile")
        theme["design"] = theme.get("design") or {
            "audience": data["intent"].get("audience") or "General audience",
            "purpose": data["intent"].get("objective") or "Brief the audience",
            "industry": "Finance" if "financ" in data["prompt"].lower() else "General",
            "mood": direction or "Professional", "brand_constraints": "", "visual_references": "",
            "imagery": "Restrained editorial imagery coordinated with the deck palette.",
            "heading_font": "Georgia" if "luxury" in direction else theme["font"], "spacing": "spacious",
            "card_shape": "rect", "cover_style": "rules" if "luxury" in direction else "panel",
            "cover_background": theme["background"], "dense_surface": "#F7F2E8", "dense_ink": "#242321",
            "chart_colors": [theme["accent"], theme["muted"], "#779891"],
        }
        return theme
    if schema == "Clarification":
        intent = data["intent"]
        agents = [
            ("audience", "audience", "Who is this presentation for?", "The audience determines how much context and technical detail to include.",
             [("executive", "Executive / Board", "Strategic and decision-oriented", None),
              ("technical", "Technical audience", "Detailed concepts and analysis", None),
              ("students", "Students", "Foundational and explanatory", None),
              ("general", "General audience", "Accessible to a broad audience", None)]),
            ("visual_direction", "visual_style", "Which visual direction should I use?", "Choose how the deck should look and feel.",
             [("corporate", "Minimal corporate", "Clean, confident and focused", "#D5DDF7"),
              ("editorial", "Editorial report", "Thoughtful typography and a restrained palette", "#EADDD1"),
              ("technology", "Modern technology", "Crisp accents and structured grids", "#D8E9F4"),
              ("creative", "Bold creative", "Expressive color and composition", "#EAD6F4")]),
        ]
        questions = []
        for key, kind, title, description, options in agents:
            if key in intent["missing"]:
                questions.append({"id": key, "type": kind, "title": title, "description": description,
                                  "options": [{"id": i, "label": label, "description": detail, "swatch": swatch}
                                              for i, label, detail, swatch in options],
                                  "recommended_option_id": options[0][0]})
        return {"questions": questions}
    if schema in {"HtmlPlan", "Outline"}:
        topic = data["intent"]["subject"]
        title = "Quantum Computing" if "quantum computing" in topic.lower() else topic[:100].strip().capitalize()
        count = data.get("slide_count") or 6
        steps = [("What makes it different", "comparison"), ("Where it could create value", "closing"),
                 ("From concept to implementation", "process"), ("What to watch next", "timeline")]
        slides = [{"title": title, "layout": "cover", "purpose": "Introduce the topic and the central question."}]
        for index in range(count - 1):
            label, layout = steps[index % len(steps)]
            if index >= len(steps):
                label += f" · {index + 1}"
            slides.append({"title": label, "layout": layout, "purpose": f"Explain {label.lower()} in the context of {title}."})
        if schema == "HtmlPlan":
            return {"title": title, "background": "#FFFFFF", "ink": "#151923", "accent": "#126148",
                    "muted": "#485564", "surface": "#EFF5F2", "display": "Georgia",
                    "slides": [{"title": s["title"], "layout": s["layout"], "direction": s["purpose"]} for s in slides]}
        return {"title": title, "narrative": "Frame the opportunity, explain the mechanics, and highlight practical next steps.", "slides": slides}
    if schema in {"PageMarkup", "LayoutMarkup"}:
        slide = data["slide"]
        content = ('<matrix><tr><td>Classical bits</td><td>Quantum bits</td></tr>'
                   '<tr><td>Binary values</td><td>Superposition</td></tr></matrix>'
                   if slide["layout"] == "comparison" else f'<text>{escape(slide["direction"])}</text>')
        return {"markup": f'<page><col bg="surface" pad="32"><text size="48">{escape(slide["title"])}</text></col>{content}</page>'}
    if schema == "SlideContent":
        item = data["item"]
        title = item["title"]
        subtitle = item.get("purpose") or title
        points = [("Scope", "Define the problem."), ("Evidence", "Check the assumptions."),
                  ("Action", "Assign an owner.")]
        icons = {}
        if item["layout"] == "comparison" and "quantum computing" in data.get("topic", "").lower():
            points = [("Classical bits", "Binary values."), ("Quantum bits", "Superposition.")]
            icons = {"Classical bits": "cpu", "Quantum bits": "atom"}
        elif item["layout"] == "cover":
            points = []
        if data.get("required_exhibit") == "swimlane":
            return {"title": title, "swimlane": {
                "lanes": ["Requester", "Reviewer"],
                "nodes": [{"id": "request", "label": "Send request", "lane": 0, "column": 0},
                          {"id": "review", "label": "Review request", "lane": 1, "column": 0},
                          {"id": "result", "label": "Share decision", "lane": 1, "column": 1}],
                "edges": [{"source": "request", "target": "review", "label": "Request"},
                          {"source": "review", "target": "result"}],
            }}
        if data.get("required_exhibit") == "diagram":
            return {"title": title, "diagram": {"lanes": [{"nodes": [
                {"label": name[:46], "detail": body[:90]} for name, body in points
            ]}]}}
        return {"title": title, "subtitle": subtitle,
                "points": [{"title": name, "body": body, "icon": icons.get(name)} for name, body in points],
                "notes": "Speaker notes for the export test."}
    if schema == "ResearchQueries":
        return {"queries": [data["intent"]["subject"]]}
    if schema in {"ResearchBrief", "EvidenceSummary"}:
        return {"summary": "Reference material is available for review.", "uncertainties": []}
    if schema == "Review":
        return {"issues": [], "regenerate_content": False}
    raise AssertionError(f"Missing model fixture for {schema}")


if __name__ == "__main__":
    import runpy
    import sys

    module = sys.argv.pop(1)
    with model_fixture():
        runpy.run_module(module, run_name="__main__", alter_sys=True)
