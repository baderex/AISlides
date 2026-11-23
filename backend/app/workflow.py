import base64
import io
import logging
import xml.etree.ElementTree as ET
import re
from datetime import date

from PIL import Image as PILImage

from app.generation import run_checkpoint
from app.job_state import start_status
from app.branding import layout_for_slide, scan_saved_artwork
from app.composition import (
    EXHIBIT_LAYOUTS, IMAGE_LAYOUTS, EvidenceSummary, Intent, Outline, OutlineItem, ResearchBrief, ResearchQueries, Review,
    SlideContent, accepts_web_image, design, is_data_driven, planned_exhibit, supports_point_icons,
)
from app.config import settings
from app.composition_models import CoverageReview, validate_assignments
from app.consulting import CONSULTING_DIRECTION, PLAN_GUIDANCE, SLIDE_GUIDANCE, requested_consulting
from app.deck_design import DeckDesignSpec, FAMILIES, baseline_rules, design_family, resolve_fonts
from app.design import ArtDirection, DIRECTOR_PROMPT, apply_brand_kit, enforce_constraints, explicit_constraints, font_family, reference_sheet
from app.domain import Slide, Source, Theme, read_document
from app.evidence import EVIDENCE_GUIDANCE, EvidenceObservation, resolve_headline, verified_observations
from app.export_review import ReviewBusy, ReviewUnavailable, compare_slide_export, renderer
from app.imagery import image_configured, render_image, source_web_image
from app import html_layout
from app.html_layout.markup import DESIGN as HTML_DESIGN, PLAN as HTML_PLAN, HtmlPlan, LayoutMarkup, MarkupError, page_schema
from app.html_layout.markup import EXHIBIT_DESIGN, ExhibitFrameCopy, RecoveredExhibitFrame
from app.html_layout.review import design_memory, evaluate_quality, markup_content
from app.master_artwork import master_artwork
from app.source_sections import source_packet, terms, web_passage
from app.presentations import asset_sources, referenced_images
from app.providers import ProviderError
from app.questions import Choice, Clarification, Option, standard_options
from app.rendering import flattened
from app.render_geometry import available_family, connector_points
from app.research import collect
from app.slide_design import LAYOUT_RUBRIC, LayoutReview, build_slide, review_layouts
from app.storage import Storage
from app.visuals import CONTENT_GUIDANCE, NUMERIC_VISUAL_TYPES, SELECTION_GUIDANCE, VISUAL_LAYOUTS, named_visual

log = logging.getLogger(__name__)
POINT_ICON_GUIDANCE = (
    " Use the optional point.icon field only when a listed Lucide symbol helps readers recognize a concept or distinguish "
    "one step or card from another. Choose at most one icon per point. Leave it null for generic decoration, charts, tables, "
    "or when an illustration already carries the meaning. Respect requests to omit icons."
)
DATA_PLAN_GUIDANCE = (
    " Lead with the requested evidence. For a time series, dedicate a chart slide to the full requested period, using "
    "comparable observations and a clear unit. Give each requested ranked entity its own slide, named with its actual "
    "identity from the research. Honor the ranking metric and as-of date. Do not add cover, agenda or recap slides "
    "at the expense of requested content. Use chart for comparable series, research for concise data tables, and "
    "metrics for a few distinct sourced KPIs. Use these evidence layouts for ranked-entity slides. Preserve the exact "
    "rank order from the dated evidence. Describe what each slide must establish without embedding numeric claims in "
    "its purpose. State any missing evidence explicitly."
)
DATA_SLIDE_GUIDANCE = (
    " Cite the supplied excerpts for each figure, with its exact period, category and unit. The outline and research "
    "summary are planning material. For ranked entities report the rank, basis and date only when the evidence "
    "documents that snapshot. Include sourced operating or financial metrics and disclose gaps."
)
DATA_LAYOUT_GUIDANCE = {
    "chart": " Use the native chart with the ordered series, metric and unit. For annual data check year-end or full-year "
             "dates explicitly. Label provisional observations. Highlight and compare only meaningful zero-based indices.",
    "research": " Use a concise table with a header and comparable rows.",
    "metrics": " Put each sourced KPI in point.value with a short label and a date.",
}
COMMITMENT_EXHIBITS = {"allocation", "rollout", "decision", "cover_callout"}
COMPOSITION_PLAN = (
    " Give each slide one job and one primary visual. In topic name what the slide covers. In key_message write its concise "
    "supported finding, recommendation, or explanation in natural language. Examples of useful headline structure are "
    "'Operating profit recovers in Q4' and 'North and Central host the pilot'. Leave key_message empty for supplied titles, "
    "neutral look-up tables, or where evidence does not yet support a finding. Set lock_title only for exact titles the user supplies. "
    "Use title as a clear fallback. Keep introduced shorthand out of headings. Aim for 4–9 words. "
    "Set visual_priority to the central figure, exhibit, or action. For a product slide about two or three independent "
    "features set recipe to feature_panels. Use diagram_flow for a sequence and diagram_paths for alternatives. "
    "Use the swimlane exhibit and diagram_swimlane recipe for explicitly requested swimlanes or handoffs between named owners. "
    "Use closing_display for one clear closing action. Leave recipe null when unsure. "
    "Choose chart for a measured trend, research for exact lookup, process for ordered steps, "
    "comparison for parallel alternatives, and summary for unrelated explanatory points. Use a section divider only "
    "for a transition. Mark use_uploaded_image on a slide explicitly assigned a reference image, including a cover. "
    "Set the exhibit field where the brief supports the composition: cover_callout only for a cover asking for a specific approval, "
    "stacked_comparison when comparable totals have supplied "
    "component figures, allocation for an itemized budget, rollout for a deployment timeline, and decision for an approval "
    "slide with success gates and a stop rule. Select the corresponding layouts cover, chart, metrics, timeline, and closing. "
    "Use the diagram exhibit for a technical or educational slide about how entities relate, how steps follow in order, "
    "how two paths differ, or a worked example of a process. Use process as its layout. "
    "For a financial or analytical closing built around two or three sourced figures, select the readout exhibit "
    "and metrics layout. For a product or action closing, use closing and plan one concrete next step. "
    "For a site table use research. A decision lead can state the action without putting every figure into the title. "
    "In required_details list the exact details the brief requires on the assigned slide, including named entities, "
    "allocations, amounts, conditions, and exact terms. Keep table cells in the table. Distribute all requirements across the deck. "
    "When the brief prescribes one to three named points, steps or use cases on a slide, set point_count to that exact count. "
    "Use null when the count is unspecified or covered by a structured exhibit or table."
)
COMPOSITION_GUIDANCE = {
    "cover": " Keep title as the outline title. For an approval callout put only the exact funding cap in value, "
             "its description in label, and the supplied duration, locations and scope in detail or subtitle. "
             "Preserve exact site names. For other covers the subtitle is optional. If used, identify the audience or "
             "summarize the subject in a short factual phrase. Never tell the audience to proceed to a later slide.",
    "chart": " When comparable totals are made from supplied non-negative components, use stacked_comparison instead of "
             "chart or table. Set categories to the entities being compared, each series to one shared cost component, and "
             "series values to the component amounts in category order. Use short category and series names. Keep the unit "
             "and period in the label. Set value_prefix to the supplied currency symbol when applicable. Use the subtitle "
             "for the main finding, difference_label for the units and period of a lower total, and the exhibit caption "
             "for any required qualification. The renderer calculates the difference.",
    "research": " Use rows for the compared entities and columns for the supplied criteria. Keep every required row. "
                "State a supplied selection or allocation in the visible subtitle and set table_focus to the exact "
                "first-column labels of at most two focal or selected rows. When specific criteria explain the selection, "
                "put their exact column headers in table_focus_columns. Leave context-only criteria and deferred rows "
                "unhighlighted. Do not infer capacity, readiness, or feasibility from incomplete data. Call out material "
                "constraints using only the supplied figures.",
    "metrics": " For an itemized budget, use allocation with one line for every supplied cost item, including contingency. "
               "Keep individual amounts separate and set value_prefix to the supplied currency symbol. Do not include the total "
               "as a line. The renderer sums the line items.",
    "timeline": " Use rollout with concise period labels and complete milestone phrases of three to seven words. Put each risk "
                "beside its specific mitigation in rollout.risks. Allow time for operation before an evaluation milestone. "
                "Treat dates as a proposed plan when they were not supplied.",
    "closing": " Use decision.gates for the criteria. In threshold put only the comparator and measured value, such as ≥95% "
               "or ≤30 days. Put the measure in label and any necessary comparison basis in detail. The renderer joins "
               "decision.action with decision.request, so start request with a noun or article, never a second verb. "
               "Use request for the decision and any cap. Use scope for the remaining supplied limits, in one short "
               "complete sentence with no repeated details or unfinished clauses. Future expansion gates do not condition pilot approval today. "
             "In decision.if_missed write a short complete sentence with the supplied trigger and the action to take. "
             "If the subtitle describes the gates, describe their future review and keep the current approval separate. "
             "For a closing without gates set next_step to a single actionable sentence addressed directly to the audience, "
             "and leave redundant points empty.",
}
CALENDAR_YEARS = "one two three four five six seven eight nine ten".split()
YEAR_IN_QUERY = re.compile(r"\b((?:19|20)\d{2})(?:(?:\s*(?:to|[-–])\s*)((?:19|20)\d{2})|(-\d{2}-\d{2}))?\b")


def inputs(item, context, query=None):
    research = context.get("research")
    if research and research.get("observations"):
        research = {**research, "observations": [{key: value for key, value in row.items() if key != "quote"}
                    for row in research["observations"][:24]]}
    intent = context.get("intent")
    return {"prompt": item.request["prompt"], "intent": {k: v for k, v in intent.items() if k != "requirements"} if intent else intent,
            "answers": context.get("answers", []), "skill": context.get("skill"),
            "sources": [source_packet(s, query or item.request["prompt"], inventory=query is None)
                        for s in context.get("sources", [])], "research": research}


EXPLICIT_FUNDING_CAP = re.compile(r"\b(?:capped(?: at)?|cap of)\s+([$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:[kKmM]\b)?)", re.I)


def preserve_cover_cap(content, prompt):
    if content.cover_callout is None:
        return
    caps = set(EXPLICIT_FUNDING_CAP.findall(prompt))
    if len(caps) == 1:
        value = caps.pop()
        if len(value) <= 25 and value != content.cover_callout.value:
            content.cover_callout.value = value
            content.cover_callout.label = "Funding cap"


def omit_phase_quantities(part, intent):
    return part.exhibit == "rollout" and intent.get("supplied_evidence_only", False) and not intent.get("supplied_phase_quantities", False)


def image_aspect(asset_id, images):
    if asset_id in images:
        try:
            with PILImage.open(io.BytesIO(Storage().get(asset_id))) as image:
                return image.width / image.height
        except (OSError, ValueError):
            return None
    return None


def point_icon_guidance(part, document, brand):
    if supports_point_icons(part.layout, document.theme, brand):
        return POINT_ICON_GUIDANCE
    return " Leave point.icon null for this layout."


def visual_direction(item, context):
    if requested_consulting(item.request["prompt"]):
        direction = CONSULTING_DIRECTION
    else:
        direction = (context.get("intent") or {}).get("visual_direction", "")
    for answer in context.get("answers", []):
        if answer["question_id"] == "visual_direction" or answer["question_id"] in context.get("style_questions", []):
            direction = answer["value"]
    return direction


def requested_slide_count(context):
    count = context["intent"].get("slide_count")
    for answer in context.get("answers", []):
        if answer["question_id"] in context.get("count_questions", []) and answer["value"].isdigit():
            count = int(answer["value"])
    return count or 6


def requested_calendar_period(prompt, today):
    match = re.search(r"\b(?:last|past)\s+(\w+)\s+(?:(?:completed|calendar)\s+)*years?\b", prompt.lower())
    if not match or YEAR_IN_QUERY.search(prompt):
        return None
    span = match[1]
    count = int(span) if span.isascii() and span.isdecimal() and len(span) <= 2 else (
        CALENDAR_YEARS.index(span) + 1 if span in CALENDAR_YEARS else 0)
    return (today.year - count, today.year - 1) if count else None


def align_query_period(query, period):
    if period is None:
        return query
    start, end = period
    ranked = bool(re.search(r"\b(?:top\s+\d+|rank(?:ed|ing|s)?|largest)\b", query, re.IGNORECASE))

    def replace(match):
        years = [int(value) for value in match.group(1, 2) if value]
        ranking_date = ranked and not match[2] and re.search(r"\bas\s+of\s*$", query[:match.start()], re.IGNORECASE)
        if not ranking_date and all(start <= year <= end for year in years):
            return match[0]
        if match[2]:
            return f"{start}-{end}"
        return f"{end}-12-31" if match[3] else str(end)

    aligned = YEAR_IN_QUERY.sub(replace, query) if YEAR_IN_QUERY.search(query) else f"{query} {start}-{end}"
    if ranked and "market cap" in query.lower() and not re.search(r"\d{4}-\d{2}-\d{2}|December\s+31|31\s+December", aligned, re.IGNORECASE):
        aligned = f"{aligned} December 31, {end} historical snapshot"
    return aligned


def collect_data_sources(queries):
    results = {}
    failure = None
    for query in dict.fromkeys(query.strip() for query in queries if query.strip()):
        try:
            for source in collect(query, count=4, detailed=True):
                key = str(source.url)
                previous = results.get(key)
                if previous:
                    excerpts = dict.fromkeys([*previous.excerpt.splitlines(), *source.excerpt.splitlines()])
                    previous.excerpt = "\n".join(excerpts)[:3000]
                else:
                    results[key] = source
        except ProviderError as error:
            failure = error
    if failure and not results:
        raise failure
    return list(results.values())


def focused_sources(document, query):
    known = {str(source.url): source for source in document.sources if source.url}
    try:
        matches = collect(query, count=3, detailed=True)
    except ProviderError:
        log.info("Slide-specific web research was unavailable; using deck-level sources.")
        return []
    found = {}
    remaining = max(0, 59 - len(document.sources))
    for source in matches:
        url = str(source.url)
        if url in found:
            continue
        if url in known:
            saved = known[url]
            # Only supply new passages that can also be retained alongside earlier citations.
            if source.excerpt not in saved.excerpt and len(saved.excerpt) + len(source.excerpt) + 1 > 20000:
                source = saved
            else:
                source = source.model_copy(update={"id": saved.id})
        elif remaining:
            remaining -= 1
        else:
            continue
        found[url] = source.model_dump(mode="json")
    return list(found.values())


def evidence_inputs(item, context):
    supplied = inputs(item, context)
    web_count = sum(bool(source.get("url")) for source in supplied["sources"])
    size = min(1400, 14000 // max(1, web_count))
    supplied["sources"] = [source if not source.get("url") or source.get("asset_id")
                           else {**source, "excerpt": web_passage(source["excerpt"], item.request["prompt"], size)}
                           for source in supplied["sources"]]
    return supplied


def outline_inputs(item, context):
    supplied = evidence_inputs(item, context)
    research = supplied["research"] or {}
    if research.get("summary") or research.get("observations"):
        # The outline uses the researcher's findings and gaps. Slide authors still get the source text.
        supplied["sources"] = [
            {"id": source["id"], "title": source["title"]} if source.get("url") and not source.get("asset_id") else source
            for source in supplied["sources"]
        ]
    return supplied


def slide_inputs(item, context, part=None):
    if part is None:
        return inputs(item, context)
    query = " ".join([part.title, part.purpose, part.research_query, *part.required_details])
    supplied = inputs(item, context, query)
    supplied["assigned_requirements"] = assigned_requirements(part, context)
    if repair := context.get("coverage_repair"):
        supplied["required_correction"] = repair
    focused = context.get("slide_sources", [])
    seen = {source["id"] for source in focused}
    background = [source for source in supplied["sources"] if source["id"] not in seen]
    keywords = terms(query)
    background.sort(key=lambda s: len(keywords & terms(s["title"] + " " + s.get("excerpt", ""))), reverse=True)
    references = [source for source in background if not source.get("url") or source.get("asset_id")]
    web = [*focused, *(s for s in background if s.get("url") and not s.get("asset_id"))]
    if part.layout in {"chart", "metrics"}:
        sizes = (2000, 600, 300, 250)
    elif part.layout in {"cover", "section", "closing"} and not part.exhibit:
        sizes = (500, 350)
    else:
        sizes = (1000, 700, 500, 350)
    supplied["sources"] = [{"id": s["id"], "title": s["title"], "excerpt": web_passage(s["excerpt"], query, size)}
                           for s, size in zip(web, sizes)]
    supplied["sources"].extend(references[:4])
    if not web:
        return supplied
    if intent := supplied.get("intent"):
        supplied["intent"] = {k: intent[k] for k in ("audience", "tone", "supplied_evidence_only") if intent.get(k)}
    if research := supplied.get("research"):
        ids = {s["id"] for s in supplied["sources"]}
        gaps = sorted(research.get("uncertainties", []), key=lambda gap: len(terms(gap) & keywords), reverse=True)
        supplied["research"] = {"uncertainties": [gap for gap in gaps[:2] if terms(gap) & keywords], "observations": [
            {k: v for k, v in row.items() if k != "quote"} for row in context["research"].get("observations", [])
            if row["source_id"] in ids and (part.layout in {"chart", "metrics", "research"} or
                                            terms(row.get("metric", "")) & keywords)][:16]}
    return supplied


def html_slide_inputs(item, context, part):
    supplied = inputs(item, context, " ".join([part.title, part.direction, *part.required_details]))
    supplied["assigned_requirements"] = assigned_requirements(part, context)
    if repair := context.get("coverage_repair"):
        supplied["required_correction"] = repair
    sources = supplied["sources"]
    if part.source_ids is None or set(part.source_ids) - {source["id"] for source in sources}:
        # Older outlines and unrecognized references still get the original evidence packet.
        return supplied
    selected = set(part.source_ids) | {context.get("brief_source_id")}
    supplied["sources"] = [source for source in sources if source["id"] in selected]
    if research := supplied["research"]:
        supplied["research"] = {"uncertainties": research.get("uncertainties", []),
                                "observations": [row for row in research.get("observations", [])
                                                 if row["source_id"] in selected]}
    return supplied


def attach_slide_sources(document, context, cited=None):
    known = {source.id: source for source in document.sources}
    for raw in context.get("slide_sources", []):
        if cited is not None and raw["id"] not in cited:
            continue
        if saved := known.get(raw["id"]):
            extra = raw["excerpt"]
            if extra not in saved.excerpt and len(saved.excerpt) + len(extra) + 1 <= 20000:
                saved.excerpt += "\n" + extra
        else:
            source = Source.model_validate(raw)
            document.sources.append(source)
            known[source.id] = source


def review_content(slide):
    layers = [layer for layer in flattened(slide.elements) if layer[0].opacity]
    shapes = {el.id: frame for el, *frame in layers if el.type == "shape"}
    nodes = {key: i for i, key in enumerate(shapes, 1)}
    labels = [(el.text, x, y, w, h) for el, x, y, w, h in layers if el.type == "text"]
    diagram = any(el.type in {"line", "shape"} for el, *_ in layers)

    def endpoint_labels(point, anchor):
        if anchor and anchor.shape_id in shapes:
            x, y, w, h = shapes[anchor.shape_id]
            return [name for name, lx, ly, lw, lh in labels
                    if x <= lx + lw/2 <= x+w and y <= ly + lh/2 <= y+h]
        # Report proximity, including ties. Explicit anchors and measured points remain authoritative.
        px, py = point
        distances = [(max(x-px, px-x-w, 0)**2 + max(y-py, py-y-h, 0)**2, name)
                     for name, x, y, w, h in labels]
        nearest = min((d for d, _ in distances), default=1)
        return [name for d, name in distances if d <= .10**2 and d <= nearest + .0001]

    visible, geometry = [], []
    for el, *frame in layers:
        if el.type == "text":
            visible.append(el.text)
            if diagram:
                geometry.append({"label": el.text, "bounds": [round(v, 4) for v in frame]})
        elif el.type == "shape" and diagram:
            geometry.append({"node": nodes[el.id], "shape": el.shape, "bounds": [round(v, 4) for v in frame],
                             "polygon": el.polygon})
        elif el.type == "line":
            points = connector_points(el, *frame, shapes)
            geometry.append({"edge": [[round(x, 4), round(y, 4)] for x, y in points], "arrowheads": el.arrows,
                             "from": nodes.get(el.start_anchor.shape_id) if el.start_anchor else None,
                             "to": nodes.get(el.end_anchor.shape_id) if el.end_anchor else None,
                             "labels_near_start": endpoint_labels(points[0], el.start_anchor),
                             "labels_near_end": endpoint_labels(points[-1], el.end_anchor)})
        elif el.type == "table":
            visible.append({"table": el.rows})
        elif el.type == "chart":
            visible.append(el.model_dump(include={"chart", "categories", "series", "value_prefix", "value_suffix"}))
        elif el.type == "image":
            visible.append({"picture": el.alt, "asset_id": el.asset_id})
    return {"visible": visible, "diagram": geometry, "speaker_notes": slide.notes}


def review_slide_content(ai, slide, part, item, context, *, png=None, previous_reviewed=None):
    packet = html_slide_inputs(item, context, part) if hasattr(part, "direction") else slide_inputs(item, context, part)
    content = review_content(slide)
    sources = packet["sources"]
    checked_values = {}
    if not hasattr(part, "direction"):
        web_ids = {s["id"] for s in [*context.get("sources", []), *context.get("slide_sources", [])]
                   if s.get("url") and not s.get("asset_id")}
        review_query = " ".join([part.title, *part.required_details, *(str(v) for v in content["visible"])])
        web_count = sum(s["id"] in web_ids for s in sources)
        size = max(350, 1600 // max(1, web_count))
        sources = [{**s, "excerpt": web_passage(s["excerpt"], review_query, size)}
                   if s["id"] in web_ids else s for s in sources]
        if slide.spec:
            for binding in slide.spec.value_bindings:
                if binding.get("status") == "verified" and {"source_id", "label", "value"} <= binding.keys():
                    checked_values.setdefault(binding["source_id"], []).append([binding["label"], binding["value"]])
    review = ai.structured(Review,
        "Check the finished slide against the user's brief, its assigned purpose and required_details, and "
        "the supplied evidence. checked_values lists exhibit cells already matched to the original sources. "
        "Check quantities, units, directed edges, arrowheads, qualifications, and decision gates. "
        "Check assigned_requirements including required speaker notes and image credits. "
        "Speaker notes can satisfy notes requirements only. Edge points run from source to destination. "
        "When previous_reviewed is supplied, preserve its meaningful nodes, connections, labels and graphics. "
        "Allow decorative cleanup and repositioning that retain their meaning; flag removal of meaningful "
        "anonymous shapes such as chart bars or parts of an illustration. "
        "Evaluate only requirements assigned to this slide. Accept faithful paraphrases. Flag a material "
        "contradiction even if the plan repeats it. Use only the visible content to establish what the audience "
        "can read. When an image is supplied also check whether the exhibit communicates the intended point. "
        "Set regenerate_content for material defects only. For each defect name the claim or missing requirement "
        "and the specific correction. Leave issues empty when the slide fulfills its purpose.",
        {"brief": packet["prompt"], "answers": packet["answers"],
         "assignment": part.model_dump(include={"title", "direction", "purpose", "key_message", "required_details"}),
         "sources": sources, "assigned_requirements": packet.get("assigned_requirements", []),
         "checked_values": checked_values, **content, "cited_source_ids": slide.source_ids,
         **({"previous_reviewed": previous_reviewed} if previous_reviewed is not None else {})},
        "content-review", base64.b64encode(png).decode("ascii") if png else None)
    if review.regenerate_content:
        return review.issues or ["Revise the visible content to fulfill the assigned brief requirements."]
    return []


def apply_slide_evidence(part, content, item, document, context):
    observations = [EvidenceObservation.model_validate(row) for row in (context.get("research") or {}).get("observations", [])]
    return resolve_headline(part, content, item.request["prompt"], document.sources, observations=observations,
                            strict_copy=bool(context.get("intent", {}).get("supplied_evidence_only")))


def pause_for_questions(context, batch, events):
    pending = batch.model_dump(mode="json")
    if context.get("questions"):
        context["question_history"] = [*context.get("question_history", []), context["questions"]]
    context["questions"] = pending
    events.extend([("question.requested", "Your input will help shape the presentation.", {"count": len(batch.questions)}),
                   ("generation.paused", "Waiting for your answers.", {})])


def resolve_web_research(item, context, data_driven, events):
    if (context.get("intent") or {}).get("supplied_evidence_only"):
        return False
    if "web_research_enabled" in context:
        return context["web_research_enabled"]
    preference = item.request.get("research")
    if preference is True or not data_driven or context["sources"] or not settings().research_api_key:
        return preference is True
    if preference is None:
        return True

    question_id = context.get("web_research_question")
    if question_id:
        return any(answer["question_id"] == question_id and "use_web" in answer["option_ids"]
                   for answer in context.get("answers", []))
    question = Choice(type="yes_no", title="Use web research for your charts and statistics?",
                      description="Web research is off and there are no reference sources for this data-driven deck.",
                      allow_custom_answer=False, recommended_option_id="use_web", options=[
                          Option(id="use_web", label="Use web research", description="Find sources for figures and charts."),
                          Option(id="continue", label="Continue without sources", description="Generate a qualitative overview."),
                      ])
    context["web_research_question"] = question.id
    pause_for_questions(context, Clarification(questions=[question]), events)
    return None


def image_ids(assets, context):
    images = {asset.id for asset in assets if asset.media_type.startswith("image/")} | set(context.get("generated_images", []))
    if web_image := context.get("web_image"):
        images.add(web_image["source"]["asset_id"])
    return images


def image_generation_blocker(usage):
    if not image_configured():
        return "The image provider is unavailable."
    config = settings()
    if usage.get("images", 0) >= config.image_limit_per_job:
        return "The illustration limit for this presentation has been reached."
    if config.ai_job_cost_budget > 0 and usage["estimated_cost"] + config.image_cost_usd > config.ai_job_cost_budget:
        return "The estimated job budget has no room for this illustration."
    return None


def generate_slide_image(job_id, token, index, owner_id, prompt, ai, context, orientation):
    if reason := image_generation_blocker(ai.usage):
        raise ProviderError(reason)
    key = render_image(job_id, token, index, owner_id, prompt, orientation)
    if key and key not in context.get("generated_images", []):
        context["generated_images"] = [*context.get("generated_images", []), key]
        ai.usage["images"] = ai.usage.get("images", 0) + 1
        ai.usage["estimated_cost"] = round(ai.usage["estimated_cost"] + settings().image_cost_usd, 6)
    return key


def prepare_html_image(job_id, token, index, part, item, context, ai, web_research):
    try:
        if part.image_query and web_research and context["intent"].get("web_images_requested"):
            found = source_web_image(job_id, token, index, item.owner_id, part.image_query)
            if found is None:
                return None
            source, _ = found
            return {"asset_id":source.asset_id, "description":source.title, "source":source.model_dump(mode="json")}
        if part.image_prompt and item.request.get("generate_images"):
            key = generate_slide_image(job_id, token, index, item.owner_id, part.image_prompt, ai, context, part.image_orientation)
            return {"asset_id":key, "description":part.image_prompt} if key else None
    except ProviderError as error:
        return {"warning":str(error)}
    return {"skipped":True}


def advance(job_id: str, token: str):
    run_checkpoint(job_id, token, process)


def finish_slide(document, context, events, slide, png, index):
    if context.pop("coverage_repair", None):
        document.slides[index] = slide
    else:
        document.slides.append(slide)
    context["progress_index"] = len(document.slides)
    context["_preview_index"] = index
    context["_preview_bytes"] = png
    events.append(("slide.completed", f"Slide {index + 1} is ready.",
                   {"slide_id": slide.id, "index": index, "warnings": slide.warnings}))


def assigned_requirements(part, context):
    inventory = (context.get("intent") or {}).get("requirements", [])
    return [text for i, text in enumerate(inventory, 1) if i in part.requirement_ids]


def completed(document, ai, plan, context, item):
    inventory = (context.get("intent") or {}).get("requirements", [])
    if inventory:
        # Audit the delivered slide content independently of the local review verdicts.
        # Batch by slide to keep long decks within the reviewer's context window.
        validate_assignments(plan.slides, {"brief_requirements": inventory})
        for start in range(context.get("coverage_cursor", 0), len(document.slides), 4):
            pages = [{"number": i + 1, "requirements": assigned_requirements(plan.slides[i], context),
                      **review_content(slide)} for i, slide in enumerate(document.slides[start:start + 4], start)
                     if plan.slides[i].requirement_ids]
            if pages:
                review = ai.structured(CoverageReview, "Audit requirements against the finished slides and original brief. "
                    "Check each listed requirement in its requested location, including credits in speaker notes. "
                    "Report material omissions or contradictions with the supplied slide number and a specific correction. "
                    "Return an empty issues list when the assigned requirements are met.",
                    {"brief": item.request["prompt"], "answers": context.get("answers", []), "slides": pages}, "content-review")
                if review.issues:
                    number = review.issues[0].slide_number
                    if number not in {page["number"] for page in pages}:
                        raise ProviderError("Coverage review identified a slide outside its review batch.")
                    correction = " ".join(issue.correction for issue in review.issues if issue.slide_number == number)
                    attempts = context.setdefault("coverage_revisions", [])
                    if number in attempts:
                        raise ProviderError(f"Slide {number} still fails brief coverage review. {correction}")
                    attempts.append(number)
                    context.update(coverage_repair=correction, progress_index=number-1)
                    stage = "html_slide" if isinstance(plan, HtmlPlan) else "slide"
                    return stage, "queued", "GENERATING", [("slide.revised", correction, {"index": number-1})], None, None
            context["coverage_cursor"] = start + 4
    message = ("The presentation was generated. Check the slide review notes before exporting."
               if any(slide.warnings for slide in document.slides) else "The presentation is ready to edit and export.")
    return ("done", "complete", "COMPLETED",
            [("generation.completed", message, {"slides":len(document.slides), "usage":ai.usage})], document, "Generated presentation")


def process(job_id, token, item, stage, context, assets, skill, ai):
    document = read_document(item.document)
    brand = skill.brand if skill else None
    if brand and not brand.artwork_scanned and stage in {"slide", "image", "revise"}:
        try:
            brand = scan_saved_artwork(brand.asset_id)
        except (FileNotFoundError, ValueError):
            log.warning("Could not refresh artwork metadata for PowerPoint template %s", brand.asset_id)
    direction = visual_direction(item, context)
    inferred_intent = context.get("intent") or {}
    data_driven = (is_data_driven(direction) or inferred_intent.get("requires_quantitative_evidence", False)
                   or is_data_driven(inferred_intent.get("tone", "")))
    web_research = context.get("web_research_enabled", item.request.get("research") is True)
    today = date.today()
    period = (context.get("annual_period") or requested_calendar_period(item.request["prompt"], today)) if data_driven else None
    if period and "annual_period" not in context:
        context["annual_period"] = list(period)
    period_guidance = (
        f" The requested period is calendar years {period[0]} through {period[1]}, inclusive. "
        f"Use December 31, {period[1]} as the ranking date if the user supplied no date. Avoid snapshots from any other date."
        if period else ""
    )
    if stage in {"image", "revise"}:
        attach_slide_sources(document, context)
    web_image = context.get("web_image") if stage in {"review", "revise"} else None
    if web_image:
        document.sources.append(Source.model_validate(web_image["source"]))
    events = []
    version = None
    changed = None
    state = "queued"
    if stage == "analyze":
        if not start_status(job_id, token, "ANALYZING", "agent.started", "Analyzing the request and reference materials."):
            return None
        context["sources"] = [source.model_dump(mode="json") for source in asset_sources(assets)]
        context["skill"] = skill.model_dump(mode="json", exclude={"brand"}) if skill else None
        intent = ai.structured(Intent,
            "Extract intent, audience, style and requested slide count. Inventory every explicit content, exhibit, image "
            "and notes requirement from the original brief in requirements, including any specified slide assignment. "
            "Recognize spelled-out numbers. Use empty values for missing information. "
            "Only supply visual_direction if the user specified a visual style or selected a skill. A request for impressive slides or a topic that "
            "needs charts does not specify a visual style. Set requires_quantitative_evidence for factual statistics, historical performance, "
            "rankings by measurable criteria, and numeric comparisons, even when no visual style is specified. "
            "Set requires_chart_workbook only for explicit requests for a native chart with editable series or an embedded data workbook. "
            "Set supplied_figures if the user includes quantitative evidence in their own brief. A requested slide count "
            "or date range alone is not evidence. Set supplied_evidence_only if the user asks to use only the inputs or "
            "references they supplied, or forbids external evidence. Set supplied_phase_quantities only if the brief assigns "
            "quantities to specific rollout phases. Overall site or project totals do not establish phase allocations. "
            "Set web_images_requested only if the user explicitly requests "
            "existing photos, portraits, or other sourced images. A request for research, charts, or generated illustrations does not imply web images. "
            "Set reuse_uploaded_images only when the brief explicitly asks to repeat the same image on multiple slides.",
            inputs(item, context), "analyze")
        if brand:
            intent.visual_direction = "Branded PowerPoint template"
        elif requested_consulting(item.request["prompt"]):
            intent.visual_direction = CONSULTING_DIRECTION
        if intent.supplied_figures:
            brief = Source(title="User-provided brief", excerpt=item.request["prompt"])
            context["sources"].append(brief.model_dump(mode="json"))
            context["brief_source_id"] = brief.id
        context["intent"] = intent.model_dump(mode="json")
        events.append(("agent.progress", f"Identified the topic: {intent.subject}.", {"intent": context["intent"]}))
        stage, status = "clarify", "ANALYZING"
    elif stage == "clarify":
        batch = ai.structured(Clarification,
            "Ask 0 to 3 high-impact clarification questions only when important intent is ambiguous. Never ask for an audience, style or count already supplied. "
            "For a broad topic without an audience or visual direction, prefer exactly two questions with types audience and visual_style. "
            "Use labels Executive / Board and Editorial report among suitable options. When the prompt is detailed return an empty list.",
            inputs(item, context), "clarify")
        batch = standard_options(batch, Intent.model_validate(context["intent"]))
        if brand:
            batch.questions = [question for question in batch.questions if question.type != "output_format"]
        stage, status = "research", "ANALYZING"
        if batch.questions:
            pause_for_questions(context, batch, events)
            status, state = "WAITING_FOR_USER", "waiting"
    elif stage == "research":
        if not start_status(job_id, token, "RESEARCHING", "agent.started", "Reviewing available sources."):
            return None
        web_research = resolve_web_research(item, context, data_driven, events)
        if web_research is None:
            return "research", "waiting", "WAITING_FOR_USER", events, None, None
        context["web_research_enabled"] = web_research
        if context["layout_engine"] == "auto":
            # Preserve master placeholders and the focused-research/claim reconciliation pipeline.
            native = (brand
                      or (data_driven and (web_research or any(not a.media_type.startswith("image/") for a in assets))))
            context["layout_engine"] = "native" if native else "html"
        after_research = "design"
        if context["layout_engine"] == "html" and not web_research and not any(a.extracted.strip() for a in assets):
            events.append(("research.completed", "Using the supplied prompt as the slide brief.", {}))
            return after_research, "queued", "PLANNING", events, None, None
        if web_research and not context.get("web_research_complete"):
            if data_driven:
                research = ai.structured(ResearchQueries,
                    "Write up to four focused web search queries that collectively cover all requested evidence. Search "
                    "separately for distinct metrics, time series and rankings. For a ranking, seek a published ranked table "
                    "with the actual names and values on a single stated date. Use the user's ranking basis, or market "
                    "capitalization for public companies if unspecified. If the ranking accompanies a historical review and "
                    "has no specified date, use the end of the review period. Search for a dated historical ranking using the full "
                    "day, month and year. For a calendar-year series, query specifically "
                    "for the annual observations or year-end closes. If unspecified, end the series in the last completed "
                    "calendar year. Use the full name of the conventional broad benchmark when the prompt uses shorthand. "
                    "Put exact years or dates in queries. Prefer official statistics, filings, and original "
                    "reports. Use a targeted query for the likely publisher or its domain when known." + period_guidance,
                    {**inputs(item, context), "as_of": today.isoformat(), "last_completed_year": today.year - 1}, "research-queries")
                sources = collect_data_sources(align_query_period(query, period) for query in research.queries)
            else:
                sources = collect(context["intent"]["subject"])
            context["sources"] = context["sources"] + [source.model_dump(mode="json") for source in sources]
            context["web_research_complete"] = True
        if context["sources"]:
            if data_driven:
                brief = ai.structured(EvidenceSummary,
                    "Extract the available figures by period with units and source IDs. Report the requested rankings "
                    "using a single documented date and metric. An entity mentioned in an article is not evidence of rank. "
                    "Distinguish annual observations from quarterly or estimated figures. Prefer primary sources. "
                    "Report evidence gaps in uncertainties. Keep the summary under 1,500 characters and include up to four "
                    "short uncertainties." + period_guidance + EVIDENCE_GUIDANCE, evidence_inputs(item, context), "research")
            else:
                brief = ai.structured(ResearchBrief,
                    "Summarize evidence, gaps and contradictions in the supplied sources. Treat search excerpts as provisional. "
                    "Do not introduce new sources. Return a follow-up question only when contradictory evidence requires a "
                    "user decision that previous answers do not cover." + EVIDENCE_GUIDANCE, evidence_inputs(item, context), "research")
            checked = list(verified_observations(brief.observations, [Source.model_validate(s) for s in context["sources"]],
                                                extract_table_labels=True))
            if len(checked) < len(brief.observations) and len(brief.uncertainties) < 6:
                brief.uncertainties.append("Some extracted figures could not be matched to the cited excerpts.")
            brief.observations = checked
            context["research"] = brief.model_dump(mode="json", exclude={"follow_up"})
            events.append(("research.completed", brief.summary, {"sources": context["sources"], "uncertainties": brief.uncertainties}))
            if isinstance(brief, ResearchBrief) and brief.follow_up and brief.uncertainties:
                used_ids = {a["question_id"] for a in context.get("answers", [])}
                used_kinds = {q["type"] for q in context.get("questions", {}).get("questions", [])}
                question = brief.follow_up
                if question.type not in used_kinds and question.id not in used_ids:
                    pause_for_questions(context, Clarification(questions=[question]), events)
                    return after_research, "waiting", "WAITING_FOR_USER", events, None, None
        else:
            if data_driven:
                reason = ("Web research returned no usable sources." if web_research else
                          "Web research is off." if settings().research_api_key else "Web research is unavailable.")
                events.append(("research.completed", "No source data was available for charts or statistics.",
                               {"uncertainties": [reason]}))
            else:
                events.append(("research.completed", "No reference sources were provided for this deck.", {}))
        stage, status = after_research, "PLANNING"
    elif stage == "html_plan":
        if not document.theme.design:  # Resume an older job that went directly from research to outline.
            return "design", state, "PLANNING", events, None, None
        if not start_status(job_id, token, "PLANNING", "agent.started", "Planning the deck and its visual layout."):
            return None
        plan = ai.structured(HtmlPlan, HTML_PLAN + " Assign each one-based brief_requirements ID to its intended slide.", {
            "brief_requirements": inferred_intent.get("requirements", []),
            **inputs(item, context), "slide_count":requested_slide_count(context), "aspect":document.aspect_ratio,
            "visual_direction":direction, "requires_chart_workbook":bool(inferred_intent.get("requires_chart_workbook")),
            "web_images":bool(web_research and inferred_intent.get("web_images_requested")),
            "generate_images":bool(item.request.get("generate_images") and image_configured()),
            "reuse_uploaded_images":bool(inferred_intent.get("reuse_uploaded_images")),
            "deck_design":DeckDesignSpec.from_theme(document.theme, document.aspect_ratio).model_dump(),
            "available_pictures":{a.id:a.name for a in assets if a.media_type.startswith("image/")},
        }, "outline", reference_sheet(assets) if settings().llm_vision_enabled else None)
        context["html_plan"] = plan.model_dump(mode="json")
        document.title = plan.title
        document.sources = [Source.model_validate(s) for s in context["sources"]]
        events.append(("outline.completed", "Slide outline ready.", {"outline":{"title":plan.title,
                       "slides":[{"title":p.title} for p in plan.slides]}}))
        stage, status, changed = "html_slide", "GENERATING", document
    elif stage in {"html_slide", "html_measure"}:
        saved_plan = context["html_plan"]
        plan = HtmlPlan.model_validate({key:saved_plan[key] for key in HtmlPlan.model_fields})
        index = context.get("progress_index", len(document.slides))
        if index == len(plan.slides):
            return completed(document, ai, plan, context, item)
        part = plan.slides[index]
        theme = document.theme.model_dump(include={"background","ink","accent","muted","surface","font"})
        if not document.theme.design:
            # A pre-design-stage checkpoint already has the old plan's selected palette.
            document.theme.design = baseline_rules(document.theme, design_family(direction, item.request["mode"], skill))
            document.theme.design.heading_font = saved_plan.get("display", document.theme.font)
        theme["display"] = document.theme.design.heading_font
        pictures = {a.id:a.name for a in assets if a.media_type.startswith("image/")}
        if not inferred_intent.get("reuse_uploaded_images"):
            for key in referenced_images([s for i, s in enumerate(document.slides) if i != index]):
                pictures.pop(key, None)
            for future in plan.slides[index+1:]:
                if future.picture_id:
                    pictures.pop(future.picture_id, None)
        previous = context.get("layout_retry", {})
        media = context.get("html_media", {})
        if stage == "html_slide" and not part.picture_id and not media and (part.image_query or part.image_prompt):
            media = prepare_html_image(job_id, token, index, part, item, context, ai, web_research)
            if media is None:
                return None
            context["html_media"] = media
            return "html_slide", state, "GENERATING", events, None, None
        if media.get("asset_id"):
            pictures[media["asset_id"]] = media["description"]
        required_picture = part.picture_id or media.get("asset_id")
        schema = page_schema(part.exhibit)
        if stage == "html_slide":
            if not start_status(job_id, token, "GENERATING", "slide.started", f"Designing slide {index+1}: {part.title}.", index=index):
                return None
            request = {"slide":part.model_dump(exclude={"source_ids"}), "slide_number":index+1,
                       "available_pictures":pictures, "required_picture":required_picture, "theme":theme,
                       "deck_design":DeckDesignSpec.from_theme(document.theme, document.aspect_ratio, part.layout).model_dump(),
                       "recent_compositions":design_memory(document.slides[:index]),
                       "aspect":document.aspect_ratio, "width":html_layout.WIDTH[document.aspect_ratio]}
            if previous and "html_candidate" in context:
                saved = context["html_candidate"]
                request.update(previous=saved["markup"], correction=previous["error"])
                try:
                    if part.exhibit and "html_best" not in context:
                        copy = ai.structured(ExhibitFrameCopy,
                            "The slide title, picture and native exhibit will be placed automatically. Extract the "
                            "remaining required supporting text from the prior composition into lead and caption. "
                            "Retain any source note. Omit labels already in the exhibit. Return plain text.",
                            {"title":part.title, "previous":saved["markup"], "exhibit_data":saved["exhibit_data"]},
                            "html-slide")
                        markup = copy.markup(part.title, required_picture)
                    else:
                        repair = ai.structured(LayoutMarkup,
                            "Repair the supplied slide XML using the renderer's correction. Return one complete <page> "
                            "in markup. Preserve all copy, figures, pictures and source notes. Use the deck_design roles, "
                            "canvas width and a 720px height. Adjust layout, contrast, gap or padding as requested. "
                            "Include one exhibit for a planned exhibit and the exact asset ID for a required picture. "
                            "Put box, label, arrow and poly in drawing. Use text for flex copy. XML-escape text and "
                            "close every tag.", request, "html-slide")
                        markup = repair.markup
                    candidate = schema.model_validate({**saved, "markup": markup})
                except ProviderError:
                    if "html_best" not in context:
                        raise
                    context["html_candidate"] = context["html_best"]["candidate"]
                    return "html_measure", state, "REVIEWING", events, None, None
            elif ("exhibit_data" in previous and part.exhibit not in {*NUMERIC_VISUAL_TYPES, "workbook-chart"}
                  and previous["error"].startswith(("Use exactly one exhibit frame", "Unexpected nesting inside",
                                                    "Cite only source IDs"))):
                # Older workers saved the XML and exhibit but discarded the rest of the response.
                sources = html_slide_inputs(item, context, part)["sources"]
                recovered = ai.structured(RecoveredExhibitFrame,
                    "Recover the supporting text and notes for this saved editable diagram. The title and all "
                    "diagram labels will be rendered automatically. Keep any essential qualification concise. "
                    "Choose source numbers for supported claims. Omit color legends and raw source IDs from copy.",
                    {"title":part.title, "previous":previous["markup"], "exhibit_data":previous["exhibit_data"],
                     "sources":[{"number":i, "title":s["title"], "excerpt":s.get("excerpt", "")}
                                for i, s in enumerate(sources, 1)], "source_count":len(sources)}, "html-slide")
                candidate = schema(markup=recovered.markup(part.title, required_picture), notes=recovered.notes,
                                   source_ids=[sources[i-1]["id"] for i in recovered.sources],
                                   exhibit_data=previous["exhibit_data"])
            else:
                request.update(html_slide_inputs(item, context, part),
                               outline=[p.title for p in plan.slides], deck_title=document.title)
                if previous:
                    request.update(previous=previous["markup"], correction=previous["error"])
                    if "exhibit_data" in previous:
                        request["previous_exhibit_data"] = previous["exhibit_data"]
                candidate = ai.structured(schema, EXHIBIT_DESIGN if part.exhibit else HTML_DESIGN, request, "html-slide")
            context["html_candidate"] = candidate.model_dump(mode="json")
            return "html_measure", state, "REVIEWING", events, None, None
        candidate = schema.model_validate(context["html_candidate"])
        best = context.get("html_best")
        def measure(page):
            observations = verified_observations(getattr(page, "observations", []), document.sources)
            return html_layout.compose(page.markup, part.title, theme, document.aspect_ratio, pictures,
                                       document, part, page, list(observations), required_picture)

        def fit_error(issues):
            message = " ".join(issues)
            if part.exhibit and previous and not best:
                # A markup-only repair cannot change labels or topology inside the native exhibit.
                return ValueError("Give the exhibit the main frame. Simplify exhibit_data labels and grouping "
                                  "while preserving the required information and connections. " + message)
            return MarkupError(message)

        try:
            if media.get("source"):
                credit = Source.model_validate(media["source"])
                if credit.id not in {s.id for s in document.sources}:
                    document.sources.append(credit)
                candidate.source_ids = list(dict.fromkeys([credit.id, *candidate.source_ids]))[:20]
            if set(candidate.source_ids) - {source.id for source in document.sources}:
                raise ValueError("Cite only source IDs supplied with this brief.")
            if best:
                retained = schema.model_validate(best["candidate"])
                if (markup_content(candidate.markup) != markup_content(retained.markup)
                        or candidate.model_dump(exclude={"markup"}) != retained.model_dump(exclude={"markup"})):
                    raise MarkupError("Retain the earlier composition's content, diagram relationships, notes and sources.")
            slide, png = measure(candidate)
            quality = evaluate_quality(slide, document.slides[:index])
            if quality.hard:
                raise fit_error(quality.hard)
            if not best or candidate.markup != best["candidate"]["markup"]:
                try:
                    previous_reviewed = (best.get("reviewed_content") or review_content(measure(retained)[0])) if best else None
                    issues = review_slide_content(ai, slide, part, item, context,
                                                  png=png if settings().llm_vision_enabled else None,
                                                  previous_reviewed=previous_reviewed)
                except ProviderError as error:
                    if not best:
                        raise
                    raise MarkupError("The revised layout could not be reviewed.") from error
                if issues:
                    # A semantic correction must be allowed to change the copy and exhibit data.
                    raise ValueError("Content review: " + " ".join(issues))
            score = quality.penalty
            if renderer():
                try:
                    issues, difference = compare_slide_export(document, slide, png, Storage().get, brand)
                    if any(issue.startswith("Export fit:") for issue in issues):
                        raise fit_error(issues)
                    quality.soft.extend(issues)
                    score += difference + len(issues)
                except (ReviewBusy, ReviewUnavailable) as error:
                    slide.warnings.append(f"Export comparison unavailable: {error}")
                    if best:
                        raise MarkupError(str(error)) from error
            elif index == 0:
                events.append(("agent.progress", "PowerPoint render comparison is unavailable on this worker.", {}))
            if best and best["score"] <= score:
                if candidate.markup != best["candidate"]["markup"]:
                    slide, png = measure(schema.model_validate(best["candidate"]))
                slide.warnings = best["warnings"]
            elif quality.soft and not best and previous.get("attempt", 0) < 2:
                context["html_best"] = {"score":score, "candidate":candidate.model_dump(mode="json"),
                                        "warnings":list(dict.fromkeys([*slide.warnings, *quality.soft]))[:30],
                                        "reviewed_content":review_content(slide)}
                raise MarkupError(" ".join(quality.soft))
            else:
                slide.warnings = list(dict.fromkeys([*slide.warnings, *quality.soft]))[:30]
        except (ValueError, ET.ParseError) as error:
            if best:
                slide, png = measure(schema.model_validate(best["candidate"]))
                slide.warnings = best["warnings"]
            else:
                attempt = previous.get("attempt", 0) + 1
                issue = str(error)[:800]
                retry = {"attempt":attempt, "error":issue}
                if not isinstance(error, (MarkupError, ET.ParseError)):
                    retry["markup"] = candidate.markup
                    if part.exhibit:
                        retry["exhibit_data"] = candidate.exhibit_data.model_dump(mode="json")
                    if attempt < 3:
                        context.pop("html_candidate")
                context["layout_retry"] = retry
                if attempt >= 3:
                    raise ProviderError(f"Slide {index+1} failed visual review after three layouts: {issue}") from error
                events.append(("slide.revised", "Revising the slide after visual review.", {"index":index, "issue":issue}))
                return "html_slide", state, "GENERATING", events, None, None
        context.pop("html_best", None)
        context.pop("layout_retry", None)
        context.pop("html_candidate")
        context.pop("html_media", None)
        if media.get("warning"):
            slide.warnings = [media["warning"], *slide.warnings][:30]
        finish_slide(document, context, events, slide, png, index)
        stage, status, changed = "html_slide", "GENERATING", document
    elif stage == "design":
        if not start_status(job_id, token, "PLANNING", "agent.started", "Choosing the deck typography, palette and visual rules."):
            return None
        baseline = design(direction or ("Data Driven" if data_driven else ""), item.request["mode"], skill.theme if skill else None)
        family = design_family(direction or ("Data Driven" if data_driven else ""), item.request["mode"], skill)
        if skill and skill.theme.design:
            family = skill.theme.design.family
        seed = baseline.model_copy(deep=True)
        if not seed.design:
            seed.design = baseline_rules(seed, family)
            if not skill:
                seed.font = FAMILIES[family].body
        locked = brand is not None
        references = [{"name": asset.name, "type": asset.media_type, "excerpt": asset.extracted[:900]} for asset in assets]
        style_answers = [answer["value"] for answer in context.get("answers", [])
                         if answer["question_id"] in {"visual_direction", *context.get("style_questions", [])}]
        constraints = explicit_constraints(item.request["prompt"], " ".join(style_answers))
        proposed = ai.structured(ArtDirection, DIRECTOR_PROMPT, {
            "prompt": item.request["prompt"], "intent": context["intent"], "answers": context.get("answers", []),
            "direction": direction, "selected_skill": context.get("skill"), "references": references,
            "baseline": seed.model_dump(exclude={"constraints", "brand_kit"}), "locked_template": locked,
            "deck_design":DeckDesignSpec.from_theme(seed, document.aspect_ratio).model_dump(),
            "available_fonts":[f for f in ("Source Serif 4", "Manrope", "IBM Plex Mono", "Arial", "Arial Black", "Aptos", "Calibri", "Cambria", "Georgia", "Consolas",
                                           "Trebuchet MS", "Verdana", "Liberation Sans", "Liberation Serif",
                                           "DejaVu Sans", "DejaVu Serif") if available_family(f)],
            "protected_brand_roles": baseline.brand_kit.protected if baseline.brand_kit else [],
            "explicit_constraints": constraints.model_dump() if constraints else None,
        }, "design", reference_sheet(assets) if settings().llm_vision_enabled else None)
        theme = Theme.model_validate({**proposed.model_dump(), "profile": baseline.profile})
        theme.design.family = family
        if not locked:
            if skill and not baseline.brand_kit:
                for role in ("background", "ink", "accent", "muted", "surface", "font"):
                    setattr(theme, role, getattr(baseline, role))
                if baseline.design:
                    theme.design = baseline.design.model_copy(deep=True)
                else:
                    for role in ("cover_background", "dense_surface", "dense_ink", "chart_colors"):
                        setattr(theme.design, role, getattr(seed.design, role))
            theme.font = font_family(theme.font)
            theme.design.heading_font = font_family(theme.design.heading_font)
            theme = enforce_constraints(theme, constraints)
            theme = apply_brand_kit(theme, baseline)
            resolve_fonts(theme)
        if locked:
            rules = proposed.design
            if baseline.design:
                brief = rules.model_dump(include={"audience", "purpose", "industry", "mood", "visual_references"})
                rules = baseline.design.model_copy(update=brief)
            theme = baseline.model_copy(update={"design": rules})
        document.theme = theme
        missing_fonts = sorted({f for f in (theme.font, theme.design.heading_font) if not available_family(f)})
        events.append(("design.completed", "Deck design system ready.", {"mood":theme.design.mood,
                       "family":theme.design.family, "unavailable_fonts":missing_fonts}))
        stage = "html_plan" if context.get("layout_engine") == "html" else "plan"
        status, changed = "PLANNING", document
    elif stage == "plan":
        if not start_status(job_id, token, "PLANNING", "agent.started", "Planning the narrative and slide sequence."):
            return None
        count = requested_slide_count(context)
        theme = document.theme
        style_guidance = " " + PLAN_GUIDANCE if theme.profile == "consulting" else ""
        if data_driven:
            style_guidance += DATA_PLAN_GUIDANCE
        if theme.design and not brand:
            style_guidance += COMPOSITION_PLAN
        research_guidance = (
            " For each evidence slide, provide one focused research_query naming its subject, exact period and metric to verify. "
            "For company profiles, name the company and seek its official results or annual report."
            if data_driven and web_research else " Leave every research_query empty."
        )
        image_guidance = (
            " Web image search is enabled. Set image_query only where an existing image serves the slide's purpose and "
            "the layout is showcase, editorial, or research without a primary chart or table. "
            "For portraits use the subject's full name and 'official photographic portrait'. Leave other image queries empty."
            if web_research and inferred_intent.get("web_images_requested") else " Leave every image_query empty."
        )
        planning_inputs = outline_inputs(item, context) if data_driven else inputs(item, context)
        outline = ai.structured(Outline,
            f"Plan exactly {count} slides. Honor the requested slide allocation and give every slide a distinct purpose. "
            "If the user requests a cover, use the cover layout and their supplied deck title as its headline. "
            "Assign each one-based brief_requirements ID to its intended slide. "
            "Use chart layouts only when grounded data is available." + SELECTION_GUIDANCE + style_guidance
            + period_guidance + research_guidance + image_guidance,
            {**planning_inputs, "brief_requirements": inferred_intent.get("requirements", []), "slide_count": count, "design": theme.model_dump(),
             "deck_design":DeckDesignSpec.from_theme(theme, document.aspect_ratio).model_dump(),
             "uploaded_images": [asset.name for asset in assets if asset.media_type.startswith("image/")]}, "outline")
        use_exhibits = theme.design and not brand
        prompt = item.request["prompt"]
        prefer_diagrams = bool(re.search(r"\b(diagram[- ]friendly|(?:use|include|draw|with)\s+(?:editable\s+)?diagrams?\b)",
                                        prompt, re.I) and not re.search(r"\b(no|omit|avoid|without)\s+(?:any\s+)?diagrams?\b", prompt, re.I))
        supplied_breakdown = len(re.findall(r"\([^()\n]*[$€£¥]\s*\d[^()\n]*[$€£¥]\s*\d[^()\n]*\)", prompt)) >= 2
        for part in outline.slides:
            if not part.visual and part.layout not in {"cover", "section", "quote"}:
                part.visual = named_visual(f"{part.title} {part.topic} {part.purpose}")
            if part.visual:
                part.exhibit, part.layout = "visual", VISUAL_LAYOUTS[part.visual.type]
                part.point_count = None
                continue
            part.exhibit = (planned_exhibit(part, bool(context["sources"]), prefer_diagrams=prefer_diagrams,
                                            supplied_breakdown=supplied_breakdown) if use_exhibits else None)
            if part.exhibit:
                part.layout = EXHIBIT_LAYOUTS[part.exhibit]
        if use_exhibits and re.search(r"\b(?:use|include|draw|show|make|as|with)\b[^.\n]{0,42}\bswim[ -]?lanes?\b", prompt, re.I):
            if not any(part.exhibit == "swimlane" for part in outline.slides):
                processes = [part for part in outline.slides if part.layout == "process" and not part.visual]
                candidates = processes or (outline.slides if len(outline.slides) == 1 else [])
                if len(candidates) == 1 and not candidates[0].visual:
                    candidates[0].exhibit, candidates[0].layout = "swimlane", "process"
        context["outline"] = outline.model_dump(mode="json")
        document.title = outline.title
        document = read_document({**document.model_dump(mode="json"), "sources": context["sources"]})
        changed = document
        events.append(("outline.completed", outline.narrative or "Slide outline ready.", {"outline": context["outline"]}))
        stage, status = "slide", "GENERATING"
    elif stage == "slide":
        context.pop("web_image", None)
        context.pop("web_image_warning", None)
        index = context.get("progress_index", len(document.slides))
        outline = Outline.model_validate(context["outline"])
        if index == len(outline.slides):
            return completed(document, ai, outline, context, item)
        part = outline.slides[index]
        if not start_status(job_id, token, "GENERATING", "slide.started", f"Designing slide {index + 1}: {part.title}.", index=index):
            return None
        if data_driven and web_research and part.layout not in {"cover", "section", "quote", "closing"}:
            if "slide_sources" not in context:
                query = part.research_query.strip() or f"{part.title} {part.purpose}"[:260].strip()
                context["slide_sources"] = focused_sources(document, align_query_period(query, period))
            attach_slide_sources(document, context)
        images = image_ids(assets, context)
        if not inferred_intent.get("reuse_uploaded_images"):
            images.difference_update(referenced_images([s for i, s in enumerate(document.slides) if i != index]))
        can_illustrate = (
            item.request.get("generate_images") and not part.visual and
            (part.layout in IMAGE_LAYOUTS or (part.layout == "cover" and not part.exhibit)) and
            image_generation_blocker(ai.usage) is None
        )
        style_guidance = ""
        if part.visual:
            if data_driven:
                style_guidance = (
                    " Match every plotted figure to its source category, unit, and reporting period. "
                    "Use the requested ranking basis and as-of date, and call out any material evidence gaps."
                )
        else:
            if document.theme.profile == "consulting":
                style_guidance += " " + SLIDE_GUIDANCE
            if data_driven:
                style_guidance += DATA_SLIDE_GUIDANCE + DATA_LAYOUT_GUIDANCE.get(part.layout, "")
            if document.theme.design and not brand:
                style_guidance += COMPOSITION_GUIDANCE.get(part.layout, "")
        if context.get("brief_source_id"):
            style_guidance += (
                " The user-provided brief is a valid source for the exact figures it supplies. Cite its source ID "
                "for exhibits based on those figures. Keep explicit evidence qualifications visible."
            )
        phases_without_quantities = omit_phase_quantities(part, inferred_intent)
        if phases_without_quantities:
            style_guidance += (
                " For rollout put dates in period and only tasks in milestone. The brief does not assign quantities "
                "to phases, so omit such allocations. For example, a milestone could be 'Commission vehicles in stages'. "
                "Include only the risks and mitigations supplied by the user."
            )
        exhibit_instruction = (
            f"The selected composition is {part.exhibit}. Populate that top-level object. "
            if part.exhibit else ""
        )
        if part.exhibit == "visual":
            exhibit_instruction += CONTENT_GUIDANCE
        elif part.exhibit == "swimlane":
            exhibit_instruction += (
                "Use the lane names for distinct owners, actors or systems. Put nodes in the lane of the actor performing "
                "the action, and use column to order the work from left to right. Assign unique IDs and cells. "
                "Use a decision node for a branch and label its outgoing paths. A local path stays in its lane. "
                "Use directed cross-lane edges to show requests and responses separately. Keep same-axis routes clear of "
                "intervening nodes and use adjacent columns for diagonal routes. Keep node and edge labels short. "
                "Preserve exact protocol terms and mark conditional responses with their condition. "
            )
        elif part.exhibit == "diagram":
            exhibit_instruction += (
                "Draw the actual relationships as one or two ordered lanes. Use a flow lane for a sequence or an interaction. "
                "Use two named flow lanes when the slide compares two paths and keep each result in its own path. "
                "Use a branch lane for a decision with its test first, followed by two or three mutually exclusive outcomes. "
                "Never separate an action and its result into two sibling outcomes. "
                "For a definition, show the parts and their relationship. Use consistent roles for recurring actors, services and stores. "
                "Use contains only for a named subsystem inside another system, such as a cache within a browser. "
                "Leave contains empty for a tangible resource and for a node that is already the store. "
                "When explaining a request and response, show the client and responding service as distinctly named nodes. "
                "Name the request and response and use a two-way link if the traffic goes both ways. Give any later storage "
                "its own node. For an overview, show the actors together with any local store nested in its owner. "
                "Use clearly named actors, services or stores where possible. Keep node labels under five words. Keep link labels empty "
                "unless they provide a short transition or condition. Put exact protocol terms in node details. Preserve the prescribed step count. "
                "For a path that stays local, use explicit labels such as 'Browser checks local cache' and 'Browser "
                "reuses cached response'. Avoid 'sends request' anywhere in a local-only path. "
                "For a network path name the server and clearly label the network exchange. "
                "If a response is described as possible or conditional, say 'may return' or qualify it with 'if unchanged' "
                "where that response appears, including the recap. "
                "For a worked example with an initial visit and a later decision to reuse or revalidate, keep the first "
                "visit in its own flow lane. Begin the later branch lane with the later lookup or decision and put each "
                "revalidation result within the same node as the conditional request. Use recap for the short requested takeaway. "
            )
        elif part.exhibit == "readout":
            exhibit_instruction += (
                "Put every explicitly requested result into metrics, with a compact exact signed value and unit in value "
                "(for example, −$1m or $4m) and a short label in title. Set highlight to the index of the main result. Keep metrics in chronological "
                "order for comparisons over time. Describe a change only when supported by the source values. "
            )
        point_count = part.point_count if part.layout in {"summary", "comparison", "timeline", "process", "metrics",
                                                        "showcase", "editorial"} else None
        if point_count and not part.exhibit:
            exhibit_instruction += f"Write exactly {point_count} visible points in the points array. "
        if part.layout == "chart" or part.exhibit == "stacked_comparison" or (part.visual and part.visual.type in NUMERIC_VISUAL_TYPES):
            exhibit_instruction += (
                "For plotted numbers include exhibit_observations with source IDs and exact quotes. "
                "Match metric and period to each series and category. Use plain numbers for amount, "
                "short units such as bn or %, and currency separately. For a table use the exact column header "
                "as metric, the exact year or category cell as period and one full data row as quote. Keep the "
                "quoted scale. Plot only supported rows for a single metric and date basis. "
            )
        content = ai.structured(SlideContent, exhibit_instruction + "Write concise content for the single item given. Use its exact title and fulfill its purpose. "
             "Put required_details in the visible copy or requested notes. Preserve terms, signs, units, rows and commitments. "
             "Cite source IDs. Distinguish observations from forecasts and label illustrative figures when requested. "
             "Limit factual and causal claims to the evidence. Write for the audience and omit design commentary. "
             "Supply relevant notes and up to three points unless more are requested. Keep point titles under 65 characters "
             "and bodies under 130. Use unnumbered action phrases for steps. Use only supplied image IDs, honor assigned "
             "uploads, and supply an image_prompt if generation is enabled and no suitable asset is available."
             + style_guidance + period_guidance + point_icon_guidance(part, document, brand),
             {"item": part.model_dump(exclude_defaults=True, exclude={"research_query", "image_query"}), "topic": document.title, "slide_index": index,
              "imagery_direction": document.theme.design.imagery if document.theme.design else "",
              "brief_source_id": context.get("brief_source_id"),
              "required_exhibit": part.exhibit,
              "visual_choice": part.visual.model_dump(mode="json") if part.visual else None,
              "point_count": point_count,
              "omit_phase_quantities": phases_without_quantities,
              "earlier_exhibits": [entry for entry in context.get("earlier_exhibits", []) if entry.keys() & COMMITMENT_EXHIBITS]
                                  if part.exhibit == "decision" or part.layout == "closing" else [],
              "earlier_slide_titles": [slide.title for slide in document.slides[max(0, index-5):index]],
              **slide_inputs(item, context, part), "generate_image": bool(can_illustrate),
              "image_assets": [{"id": a.id, "name": a.name} for a in assets if a.id in images]}, "slide")
        preserve_cover_cap(content, item.request["prompt"])
        uploaded_images = [asset.id for asset in assets if asset.id in images]
        if content.image_asset_id not in images:
            content.image_asset_id = None
        if part.use_uploaded_image and content.image_asset_id not in images and len(uploaded_images) == 1:
            content.image_asset_id = uploaded_images[0]
        web_image_ratio = None
        if (web_research and context["intent"].get("web_images_requested") and part.image_query.strip()
                and content.image_asset_id not in images and accepts_web_image(part, content, brand)):
            try:
                found = source_web_image(job_id, token, index, item.owner_id, part.image_query)
                if found is None:
                    return None
                source, web_image_ratio = found
                document.sources.append(source)
                images.add(source.asset_id)
                content.image_asset_id = source.asset_id
                content.source_ids = [*content.source_ids[:11], source.id]
                context["web_image"] = {"source": source.model_dump(mode="json"), "ratio": web_image_ratio}
            except ProviderError as error:
                context["web_image_warning"] = str(error)
        part = apply_slide_evidence(part, content, item, document, context)
        context["slide_content"] = content.model_dump(mode="json")
        if can_illustrate and content.image_prompt and content.image_asset_id not in images and not (
            content.chart or content.table or content.stacked_comparison or content.swimlane
        ):
            return "image", state, "GENERATING", events, None, None
        context["draft"] = build_slide(part, content, document, index, images, brand,
                                   web_image_ratio=web_image_ratio, image_ratio=image_aspect(content.image_asset_id, images),
                                   data_driven=data_driven).model_dump(mode="json")
        stage, status = "review", "REVIEWING"
    elif stage == "image":
        index = context.get("progress_index", len(document.slides))
        if not start_status(job_id, token, "GENERATING", "agent.started", f"Generating an illustration for slide {index + 1}.", index=index):
            return None
        content = SlideContent.model_validate(context["slide_content"])
        part = OutlineItem.model_validate(context["outline"]["slides"][index]).model_copy(update={"title": content.title})
        if part.key_message:
            part.key_message = content.title
        warning = ""
        try:
            prompt = content.image_prompt
            if document.theme.design:
                prompt += " Deck imagery direction: " + document.theme.design.imagery
            key = generate_slide_image(job_id, token, index, item.owner_id, prompt, ai, context, content.image_orientation)
            if not key:
                return None
            content.image_asset_id = key
        except ProviderError as error:
            warning = str(error)
        images = image_ids(assets, context)
        slide = build_slide(part, content, document, index, images, brand,
                        image_ratio=image_aspect(content.image_asset_id, images), data_driven=data_driven)
        if warning:
            slide.warnings.append(warning)
        context["draft"], context["slide_content"] = slide.model_dump(mode="json"), content.model_dump(mode="json")
        stage, status = "review", "REVIEWING"
    elif stage == "review":
        index = context.get("progress_index", len(document.slides))
        if not start_status(job_id, token, "REVIEWING", "agent.started", f"Rendering and reviewing slide {index + 1}.", index=index):
            return None
        slide = Slide.model_validate(context["draft"])
        attach_slide_sources(document, context, set(slide.source_ids))
        images = image_ids(assets, context)
        original_recipe = slide.spec.recipe if slide.spec else None
        text_elements = [element for element, *_ in flattened(slide.elements) if element.type in {"text", "table"}]
        dimensions = [(element.size, element.h) for element in text_elements]
        storage = Storage()
        artwork = None
        if brand and (executable := renderer()):
            try:
                artwork = master_artwork(executable, brand.asset_id, layout_for_slide(brand, slide),
                                         slide.background, document.aspect_ratio)
            except (ReviewBusy, ReviewUnavailable, FileNotFoundError):
                log.warning("Could not render inherited artwork for slide %s", slide.id)
        draft_content = SlideContent.model_validate(context["slide_content"])
        important = slide.layout in {"cover", "chart", "research", "process"} or bool(draft_content.visual)
        use_vision = settings().llm_vision_enabled and important

        def evaluate(part, recipes, image):
            try:
                return ai.structured(LayoutReview, LAYOUT_RUBRIC, {
                    "title": part.title, "purpose": part.purpose, "priority": part.visual_priority or part.key_message,
                    "candidate_recipes": recipes, "design": document.theme.model_dump(mode="json"),
                }, "vision", base64.b64encode(image).decode("ascii"))
            except ProviderError:
                log.info("Visual comparison unavailable for slide %s; using the local rubric", slide.id)
                return None

        slide, png = review_layouts(document, slide, index, images, storage.get, artwork=artwork,
                                    evaluate=evaluate if use_vision and not context.get("review_attempted") else None)
        if slide.spec and original_recipe != slide.spec.recipe:
            events.append(("slide.revised", f"Selected a clearer layout for slide {index + 1}.",
                           {"index": index, "recipe": slide.spec.recipe}))
        if warning := context.get("web_image_warning"):
            slide.warnings.append(warning)
        if data_driven and slide.layout == "chart" and not draft_content.visual and not any(
            element.type == "chart" for element in slide.elements
        ):
            slide.warnings.append("The available evidence did not support the planned chart.")
        fixed = sum(original != (element.size, element.h) for original, element in zip(dimensions, text_elements))
        if fixed:
            events.append(("slide.revised", f"Adjusted text fit in {fixed} elements on slide {index + 1}.",
                           {"index": index, "text_elements": fixed}))
        part = OutlineItem.model_validate(context["outline"]["slides"][index])
        issues = review_slide_content(ai, slide, part, item, context, png=png if use_vision else None)
        issues.extend(w for w in slide.warnings if w.startswith(("Text may overflow", "Some exhibit cells", "Chart labels")))
        if issues:
            if context.get("review_attempted"):
                raise ProviderError(f"Slide {index+1} still needs revision: {' '.join(issues)}")
            context["review_attempted"] = True
            context["review_issues"] = issues
            events.append(("slide.revised", "Revising slide content after review.", {"index": index, "issues": issues}))
            return ("revise", state, "REVIEWING", events, None, None)
        slide.warnings = list(dict.fromkeys(slide.warnings))[:30]
        if part.exhibit in COMMITMENT_EXHIBITS and (exhibit := context["slide_content"].get(part.exhibit)):
            earlier = context.get("earlier_exhibits", [])
            context["earlier_exhibits"] = [*earlier[-5:], {"slide": index + 1, part.exhibit: exhibit}]
        finish_slide(document, context, events, slide, png, index)
        changed = document
        context.pop("draft", None)
        context.pop("slide_content", None)
        context.pop("review_attempted", None)
        context.pop("review_issues", None)
        context.pop("web_image", None)
        context.pop("web_image_warning", None)
        context.pop("slide_sources", None)
        stage, status = "slide", "GENERATING"
    elif stage == "revise":
        index = context.get("progress_index", len(document.slides))
        part = OutlineItem.model_validate(context["outline"]["slides"][index])
        content = ai.structured(SlideContent, "Rewrite the content concisely to address the visual review findings. Preserve the meaning, "
            "exact figures, specialized exhibits, decision rules, and supplied source IDs."
            + point_icon_guidance(part, document, brand), {
            **slide_inputs(item, context, part), "item": part.model_dump(exclude_defaults=True), "topic": document.title,
            "previous": SlideContent.model_validate(context["slide_content"]).model_dump(mode="json", exclude_defaults=True),
            "issues": context["review_issues"], "required_exhibit": part.exhibit,
            "visual_choice": part.visual.model_dump(mode="json") if part.visual else None,
            "point_count": part.point_count if context["slide_content"].get("points") else None,
            "omit_phase_quantities": omit_phase_quantities(part, inferred_intent),
        }, "slide")
        preserve_cover_cap(content, item.request["prompt"])
        images = image_ids(assets, context)
        previous = SlideContent.model_validate(context["slide_content"])
        if previous.image_asset_id in images:
            content.image_asset_id = previous.image_asset_id
            content.picture_placement = previous.picture_placement
        if web_image:
            source_id = web_image["source"]["id"]
            if accepts_web_image(part, content, brand):
                content.source_ids = [*content.source_ids[:11], source_id]
            else:
                context.pop("web_image")
                web_image = None
                content.image_asset_id = None
                content.source_ids = [key for key in content.source_ids if key != source_id]
        part = apply_slide_evidence(part, content, item, document, context)
        context["slide_content"] = content.model_dump(mode="json")
        context["draft"] = build_slide(part, content, document, index, images, brand,
                                   web_image_ratio=web_image["ratio"] if web_image else None,
                                   image_ratio=image_aspect(content.image_asset_id, images),
                                   data_driven=data_driven).model_dump(mode="json")
        events.append(("slide.revised", f"Reworked slide {index + 1} after visual review.", {"index": index}))
        stage, status = "review", "REVIEWING"
    else:
        raise ValueError(f"Unknown checkpoint {stage}")
    return stage, state, status, events, changed, version
