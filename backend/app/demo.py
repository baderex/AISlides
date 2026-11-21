"""Explicit offline fixture provider for exercising the complete product workflow."""

import re


def demo_response(schema: str, data: dict) -> dict:
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
        style = re.search(r"(editorial|academic|minimal corporate|bold creative|modern technology)", prompt, re.I)
        return {"subject": subject or "Untitled presentation", "slide_count": count,
                "audience": audience[1][:250] if audience else "", "visual_direction": style[0] if style else "",
                "missing": [key for key, value in {"audience": audience, "visual_direction": style}.items() if not value]}
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
    if schema == "Outline":
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
        return {"title": title, "narrative": "Frame the opportunity, explain the mechanics, and highlight practical next steps.", "slides": slides}
    if schema == "SlideContent":
        item = data["item"]
        title = item["title"]
        if "quantum computing" in data.get("topic", "").lower():
            variants = {
                "cover": ("A practical briefing on the science, its potential, and what remains experimental.", []),
                "comparison": ("Quantum computers use a different model of information and computation.", [
                    ("Classical bits", "A bit has a definite value of 0 or 1. Classical systems remain the default for most workloads."),
                    ("Quantum bits", "Qubits can occupy a superposition, and algorithms use interference to change measurement probabilities."),
                ]),
                "closing": ("Focus on targeted experiments and measurable progress in error correction.", [
                    ("Start with a specific problem", "Evaluate whether it fits a credible quantum algorithm."),
                    ("Benchmark honestly", "Compare results with the strongest classical approach."),
                    ("Track logical qubits", "Look for reliable operations with improved error correction."),
                ]),
            }
            subtitle, points = variants.get(item["layout"], ("Practical systems require reliable control, measurement and error correction.", [
                ("Hardware", "Physical qubits must stay controllable despite environmental noise."),
                ("Software", "Algorithms must be benchmarked on appropriately scoped tasks."),
                ("Error correction", "Logical qubits use multiple physical qubits to reduce faults."),
            ]))
        else:
            subtitle = item.get("purpose", "")[:210]
            points = [("What matters", "Identify the main problem, who it affects, and what would count as a meaningful improvement."),
                      ("What to validate", "Compare assumptions with available evidence and document open questions."),
                      ("What happens next", "Choose one practical next step with a clear owner and a review date.")]
        return {"title": title, "subtitle": subtitle, "points": [{"title": name, "body": body} for name, body in points],
                "notes": "Offline Studio Demo content. Configure a model provider to generate source-grounded material."}
    if schema == "ResearchBrief":
        return {"summary": "Reference material is available for review.", "uncertainties": []}
    if schema == "Review":
        return {"issues": [], "regenerate_content": False}
    if schema == "EditProposal":
        raise ValueError("Conversational editing requires a configured live AI provider.")
    raise ValueError(f"Unsupported demo response schema: {schema}")
