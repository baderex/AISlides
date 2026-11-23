import base64
import io
import logging
import xml.etree.ElementTree as ET
import re
from datetime import date

from PIL import Image as PILImage
from sqlalchemy import select, update

from app.branding import layout_for_slide, scan_saved_artwork
from app.composition import (
    EXHIBIT_LAYOUTS, IMAGE_LAYOUTS, EvidenceSummary, Intent, Outline, OutlineItem, ResearchBrief, ResearchQueries, Review,
    SlideContent, accepts_web_image, design, is_data_driven, planned_exhibit, supports_point_icons,
)
from app.config import settings
from app.consulting import CONSULTING_DIRECTION, PLAN_GUIDANCE, SLIDE_GUIDANCE, requested_consulting
from app.db import Job, PresentationRecord, Session, now
from app.design import ArtDirection, DIRECTOR_PROMPT, apply_brand_kit, enforce_constraints, explicit_constraints, font_family, reference_sheet
from app.domain import Image, Skill, Slide, Source, Theme, read_document
from app.evidence import EVIDENCE_GUIDANCE, EvidenceObservation, resolve_headline, verified_observations
from app.export_review import ReviewBusy, ReviewUnavailable, renderer
from app.imagery import image_configured, render_image, source_web_image
from app import html_layout
from app.html_layout.markup import DESIGN as HTML_DESIGN, PLAN as HTML_PLAN, HtmlPlan, PageMarkup
from app.master_artwork import master_artwork
from app.presentations import asset_sources, assets_for, begin_write, emit, persist, skill_for
from app.previews import put_preview
from app.providers import Intelligence, ProviderError
from app.questions import Choice, Clarification, Option, standard_options
from app.queue import notify
from app.rendering import flattened
from app.research import collect
from app.slide_design import LAYOUT_RUBRIC, LayoutReview, build_slide, review_layouts
from app.storage import Storage
from app.visuals import CONTENT_GUIDANCE, SELECTION_GUIDANCE, VISUAL_LAYOUTS, named_visual

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
    " Put specific sourced figures with dates and units in visible slide content. For a chart layout, supply the native "
    "chart with all evidenced observations in order, the metric and unit in the label, and their source IDs. Include a "
    "short finding in the subtitle. For a research layout, supply a concise table with a header and comparable rows. "
    "For a metrics layout, put the figures in point.value. For a ranked entity, include its rank, the ranking basis and "
    "date, and entity-specific financial or operating metrics. Cite only sources that actually support the numbers. "
    "A figure in the outline or research summary is not evidence by itself. For a dated ranking, only use figures from "
    "sources that actually report the specified snapshot. If unavailable, leave the ranking metric value unreported. "
    "Match every plotted value to its exact category in the evidence. In annual charts, use full-year data or year-end "
    "observations as specified. Never substitute quarterly observations or infer dates from column position. "
    "Use reported observations for historical series and label any provisional observations explicitly. "
    "Set chart.highlight only when one category supports the takeaway. Set chart.compare_to only when another "
    "category is a meaningful baseline measured in the same units. Both are zero-based category indices. "
    "Every analytical slide should show a supported chart, data table, or numeric KPIs. If the evidence supports a "
    "stronger exhibit than planned, supply it. Call out material data gaps. Do not mix currencies or reporting periods."
)
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


def inputs(item, context):
    research = context.get("research")
    if research and research.get("observations"):
        research = {**research, "observations": [{key: value for key, value in row.items() if key != "quote"}
                    for row in research["observations"][:24]]}
    return {"prompt": item.request["prompt"], "intent": context.get("intent"),
            "answers": context.get("answers", []), "skill": context.get("skill"),
            "sources": context.get("sources", []), "research": research}


EXPLICIT_FUNDING_CAP = re.compile(r"\b(?:capped|cap of)\s+([$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:[kKmM]\b)?)", re.I)


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
    known = {str(source.url): source.id for source in document.sources if source.url}
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
            source = source.model_copy(update={"id": known[url]})
        elif remaining:
            remaining -= 1
        else:
            continue
        found[url] = source.model_dump(mode="json")
    return list(found.values())


def evidence_inputs(item, context):
    supplied = inputs(item, context)
    supplied["sources"] = [{**source, "excerpt": source["excerpt"][:1400]} for source in supplied["sources"]]
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
    supplied = inputs(item, context)
    if part is None:
        return supplied
    focused = context.get("slide_sources", [])
    seen = {source["id"] for source in focused}
    background = [source for source in supplied["sources"] if source["id"] not in seen]
    terms = set(re.findall(r"[a-z0-9]{3,}", f"{part.title} {part.research_query}".lower()))
    background.sort(key=lambda source: len(terms.intersection(re.findall(r"[a-z0-9]{3,}", source["title"].lower()))), reverse=True)
    limit = 700 if focused else 1500
    supplied["sources"] = [*[{**source, "excerpt": source["excerpt"][:2000]} for source in focused],
                           *[{**source, "excerpt": source["excerpt"][:limit]} for source in background[:4]]]
    return supplied


def attach_slide_sources(document, context, cited=None):
    known = {source.id for source in document.sources}
    for raw in context.get("slide_sources", []):
        if raw["id"] not in known and (cited is None or raw["id"] in cited):
            document.sources.append(Source.model_validate(raw))


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


def advance(job_id: str, token: str):
    with Session() as db:
        job = db.get(Job, job_id)
        if not job or job.lease != token or job.cancelled:
            return
        item = db.get(PresentationRecord, job.presentation_id)
        if not item:
            return
        context, stage = dict(job.context), job.checkpoint
        assets = assets_for(db, item.owner_id, item.request["asset_ids"])
        record = skill_for(db, item.owner_id, item.request.get("skill_id"), include_deleted=True)
        skill = Skill.model_validate(record.manifest) if record else None
        db.expunge_all()
    ai = None
    try:
        ai = Intelligence(usage=context.get("usage"))
        step = process(job_id, token, item, stage, context, assets, skill, ai)
        if step is None:
            return
        next_stage, state, status, events, document, version_label = step
        context["usage"] = ai.usage
        pending_preview = context.pop("_preview_bytes", None)
        commit(job_id, token, next_stage, state, status, context, events, document, version_label, png=pending_preview)
    except Exception as error:
        if ai:
            context["usage"] = ai.usage
        log.exception("workflow failed job=%s checkpoint=%s", job_id, stage)
        message = str(error) if isinstance(error, ProviderError) else "Generation failed while processing the current stage. Retry from the saved checkpoint."
        commit(job_id, token, stage, "failed", "FAILED", context,
               [("generation.failed", message, {})], error=message)


def start_status(job_id, token, status, event_type, message, **payload):
    with Session.begin() as db:
        begin_write(db)
        item = lock_presentation(db, job_id)
        if not item:
            return False
        guarded = db.execute(update(Job).where(Job.id == job_id, Job.lease == token, Job.cancelled.is_(False))
                             .values(updated=now()).returning(Job.presentation_id)).scalar_one_or_none()
        if not guarded:
            return False
        item.status = status
        item.updated = now()
        emit(db, item, event_type, message, db.get(Job, job_id), **payload)
        return True


def commit(job_id, token, checkpoint, state, status, context, events, document=None, version_label=None, error=None, png=None):
    with Session.begin() as db:
        begin_write(db)
        item = lock_presentation(db, job_id)
        if not item:
            return
        guarded = db.execute(update(Job).where(Job.id == job_id, Job.lease == token, Job.cancelled.is_(False))
                             .values(checkpoint=checkpoint, state=state, context=context, lease=None, lease_owner=None, lease_until=0,
                                     error=error, updated=now()).returning(Job.presentation_id)).scalar_one_or_none()
        if not guarded:
            return
        job = db.get(Job, job_id)
        item.status, item.updated = status, now()
        if document:
            if png:
                put_preview(db, item.id, document, document.slides[-1], png)
            persist(db, item, document, version_label)
        elif version_label:
            persist(db, item, read_document(item.document), version_label)
        for event_type, message, payload in events:
            emit(db, item, event_type, message, job, **payload)
    if state == "queued":
        notify(job_id)


def lock_presentation(db, job_id):
    return db.scalar(select(PresentationRecord).where(
        PresentationRecord.id == select(Job.presentation_id).where(Job.id == job_id).scalar_subquery()
    ).with_for_update())


def completed(document, usage, count):
    message = ("The presentation was generated. Check the slide review notes before exporting."
               if any(slide.warnings for slide in document.slides) else "The presentation is ready to edit and export.")
    return ("done", "complete", "COMPLETED",
            [("generation.completed", message, {"slides":count, "usage":usage})], document, "Generated presentation")


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
        source = Source.model_validate(web_image["source"])
        index = context.get("progress_index", len(document.slides))
        part = OutlineItem.model_validate(context["outline"]["slides"][index])
        previous = SlideContent.model_validate(context["slide_content"])
        if accepts_web_image(part, previous, brand):
            document.sources.append(source)
        else:
            # Recompose older drafts that switched to an editorial layout to fit the image.
            context.pop("web_image")
            web_image = None
            previous.image_asset_id = None
            previous.source_ids = [key for key in previous.source_ids if key != source.id]
            context["slide_content"] = previous.model_dump(mode="json")
            context["draft"] = build_slide(part, previous, document, index, image_ids(assets, context), brand,
                                       data_driven=data_driven).model_dump(mode="json")
    events = []
    version = None
    changed = None
    state = "queued"
    if stage == "plan" and not context.get("design_completed"):
        stage = "design"
    if stage == "analyze":
        if not start_status(job_id, token, "ANALYZING", "agent.started", "Analyzing the request and reference materials."):
            return None
        context["sources"] = [source.model_dump(mode="json") for source in asset_sources(assets)]
        context["skill"] = skill.model_dump(mode="json", exclude={"brand"}) if skill else None
        intent = ai.structured(Intent,
            "Extract intent, audience, style and requested slide count. Recognize spelled-out numbers. Use empty values for missing information. "
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
            brief = Source(title="User-provided brief", excerpt=item.request["prompt"][:3000])
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
        if context.get("layout_engine") == "auto":
            # These paths bind template slots, place actual image assets, or verify quantitative
            # exhibits against retrieved observations. They still require the native compositor.
            native = (ai.provider == "demo" or brand or (skill and skill.theme.brand_kit)
                      or item.request.get("generate_images")
                      or inferred_intent.get("requires_chart_workbook")
                      or (web_research and inferred_intent.get("web_images_requested"))
                      or (data_driven and (web_research or any(not a.media_type.startswith("image/") for a in assets))))
            context["layout_engine"] = "native" if native else "html"
        after_research = "html_plan" if context.get("layout_engine") == "html" else "design"
        if after_research == "html_plan" and not web_research and not any(a.extracted.strip() for a in assets):
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
                    "user decision that previous answers do not cover." + EVIDENCE_GUIDANCE, inputs(item, context), "research")
            checked = list(verified_observations(brief.observations,
                                                [Source.model_validate(source) for source in context["sources"]]))
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
        if not start_status(job_id, token, "PLANNING", "agent.started", "Planning the deck and its visual layout."):
            return None
        plan = ai.structured(HtmlPlan, HTML_PLAN, {
            **inputs(item, context), "slide_count":requested_slide_count(context), "aspect":document.aspect_ratio,
            "visual_direction":direction,
            "available_pictures":{a.id:a.name for a in assets if a.media_type.startswith("image/")},
        }, "outline", reference_sheet(assets) if settings().llm_vision_enabled else None)
        context["html_plan"] = plan.model_dump(mode="json")
        document.title = plan.title
        document.theme = document.theme.model_copy(update=plan.model_dump(include={"background","ink","accent","muted","surface"}))
        attach_slide_sources(document, context)
        events.extend([("design.completed", "HTML/CSS layout and palette ready.", {}),
                       ("outline.completed", "Slide outline ready.", {"outline":{"title":plan.title,
                        "slides":[{"title":p.title} for p in plan.slides]}})])
        stage, status, changed = "html_slide", "GENERATING", document
    elif stage in {"html_slide", "html_measure"}:
        plan = HtmlPlan.model_validate(context["html_plan"])
        index = context.get("progress_index", len(document.slides))
        if index == len(plan.slides):
            return completed(document, ai.usage, index)
        part = plan.slides[index]
        theme = plan.model_dump(exclude={"slides", "title"})
        pictures = {a.id:a.name for a in assets if a.media_type.startswith("image/")}
        if not inferred_intent.get("reuse_uploaded_images"):
            for prior in document.slides:
                for element, *_ in flattened(prior.elements):
                    if isinstance(element, Image):
                        pictures.pop(element.asset_id, None)
        previous = context.get("layout_retry", {})
        if stage == "html_slide":
            if not start_status(job_id, token, "GENERATING", "slide.started", f"Designing slide {index+1}: {part.title}.", index=index):
                return None
            request = {**inputs(item, context), "slide":part.model_dump(), "slide_number":index+1,
                       "available_pictures":pictures,
                       "outline":[p.title for p in plan.slides], "theme":theme, "deck_title":document.title,
                       "aspect":document.aspect_ratio, "width":html_layout.WIDTH[document.aspect_ratio]}
            if previous:
                request.update(previous=previous["markup"], correction=previous["error"])
            candidate = ai.structured(PageMarkup, HTML_DESIGN, request, "html-slide")
            context["html_candidate"] = candidate.model_dump(mode="json")
            return "html_measure", state, "REVIEWING", events, None, None
        candidate = PageMarkup.model_validate(context["html_candidate"])
        try:
            if set(candidate.source_ids) - {source.id for source in document.sources}:
                raise ValueError("Cite only source IDs supplied with this brief.")
            slide, png = html_layout.compose(candidate.markup, part.title, theme, document.aspect_ratio, pictures)
        except (ValueError, ET.ParseError) as error:
            attempt = previous.get("attempt", 0) + 1
            issue = str(error)[:800]
            if attempt >= 3:
                context.pop("layout_retry", None)
                raise ProviderError(f"Could not fit slide {index+1} after three layouts: {issue}") from error
            context["layout_retry"] = {"attempt":attempt,"markup":candidate.markup,"error":issue}
            context.pop("html_candidate")
            return "html_slide", state, "GENERATING", [("slide.revised", "Adjusting the slide to fit its layout.", {"index":index})], None, None
        context.pop("layout_retry", None)
        context.pop("html_candidate")
        slide.notes, slide.source_ids, slide.layout = candidate.notes, candidate.source_ids, part.layout
        document.slides.append(slide)
        context["progress_index"] = index+1
        context["_preview_bytes"] = png
        events.append(("slide.completed", f"Slide {index+1} is ready.", {"slide_id":slide.id,"index":index,"warnings":slide.warnings}))
        stage, status, changed = "html_slide", "GENERATING", document
    elif stage == "design":
        if not start_status(job_id, token, "PLANNING", "agent.started", "Choosing the deck typography, palette and visual rules."):
            return None
        baseline = design(direction or ("Data Driven" if data_driven else ""), item.request["mode"], skill.theme if skill else None)
        locked = brand is not None
        references = [{"name": asset.name, "type": asset.media_type, "excerpt": asset.extracted[:900]} for asset in assets]
        style_answers = [answer["value"] for answer in context.get("answers", [])
                         if answer["question_id"] in {"visual_direction", *context.get("style_questions", [])}]
        constraints = explicit_constraints(item.request["prompt"], " ".join(style_answers))
        proposed = ai.structured(ArtDirection, DIRECTOR_PROMPT, {
            "prompt": item.request["prompt"], "intent": context["intent"], "answers": context.get("answers", []),
            "direction": direction, "selected_skill": context.get("skill"), "references": references,
            "baseline": baseline.model_dump(exclude={"constraints", "brand_kit"}), "locked_template": locked,
            "protected_brand_roles": baseline.brand_kit.protected if baseline.brand_kit else [],
            "explicit_constraints": constraints.model_dump() if constraints else None,
        }, "design", reference_sheet(assets) if settings().llm_vision_enabled else None)
        theme = Theme.model_validate({**proposed.model_dump(), "profile": baseline.profile})
        if not locked:
            theme.font = font_family(theme.font)
            theme.design.heading_font = font_family(theme.design.heading_font)
            theme = enforce_constraints(theme, constraints)
            theme = apply_brand_kit(theme, baseline)
        if locked:
            rules = proposed.design
            if baseline.design:
                brief = rules.model_dump(include={"audience", "purpose", "industry", "mood", "visual_references"})
                rules = baseline.design.model_copy(update=brief)
            theme = baseline.model_copy(update={"design": rules})
        document.theme = theme
        context["design_completed"] = True
        events.append(("design.completed", "Deck design system ready.", {"mood": theme.design.mood}))
        stage, status, changed = "plan", "PLANNING", document
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
            " For each slide, provide one focused research_query naming its subject, exact period and metric to verify. "
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
            "Use chart layouts only when grounded data is available." + SELECTION_GUIDANCE + style_guidance
            + period_guidance + research_guidance + image_guidance,
            {**planning_inputs, "slide_count": count, "design": theme.model_dump(),
             "uploaded_images": [asset.name for asset in assets if asset.media_type.startswith("image/")]}, "outline")
        use_exhibits = theme.design and not brand
        prompt = item.request["prompt"]
        prefer_diagrams = bool(re.search(r"\b(diagram[- ]friendly|(?:use|include|draw|with)\s+(?:editable\s+)?diagrams?\b)",
                                        prompt, re.I) and not re.search(r"\b(no|omit|avoid|without)\s+(?:any\s+)?diagrams?\b", prompt, re.I))
        supplied_breakdown = len(re.findall(r"\([^()\n]*[$€£¥]\s*\d[^()\n]*[$€£¥]\s*\d[^()\n]*\)", prompt)) >= 2
        for part in outline.slides:
            if not part.visual and ai.provider != "demo" and part.layout not in {"cover", "section", "quote"}:
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
            return completed(document, ai.usage, index)
        part = outline.slides[index]
        if not start_status(job_id, token, "GENERATING", "slide.started", f"Designing slide {index + 1}: {part.title}.", index=index):
            return None
        if data_driven and web_research and part.layout not in {"cover", "section", "quote", "closing"}:
            if "slide_sources" not in context:
                query = part.research_query.strip() or f"{part.title} {part.purpose}"[:260].strip()
                context["slide_sources"] = focused_sources(document, align_query_period(query, period))
            attach_slide_sources(document, context)
        images = image_ids(assets, context)
        if not inferred_intent.get("reuse_uploaded_images", False):
            used = {el.asset_id for slide in document.slides for el, *_ in flattened(slide.elements)
                    if isinstance(el, Image)}
            images.difference_update(used)
        can_illustrate = (
            item.request.get("generate_images") and ai.provider != "demo" and image_configured() and
            not part.visual and
            ai.usage.get("images", 0) < settings().image_limit_per_job and
            (part.layout in IMAGE_LAYOUTS or (part.layout == "cover" and not part.exhibit)) and
            (settings().ai_job_cost_budget <= 0 or ai.usage["estimated_cost"] + settings().image_cost_usd <= settings().ai_job_cost_budget)
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
                style_guidance += DATA_SLIDE_GUIDANCE
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
        content = ai.structured(SlideContent, exhibit_instruction + "Write concise content for the single item given. Use its exact title and fulfill its purpose. "
             "Include the item's required_details in its main visible content or specified notes, with exact terms and figures. "
             "Cover the current item only and do not repeat earlier slides. Write for the audience. Keep visual instructions, "
             "color names and comments about the presentation design out of visible copy unless the subject is design. "
             "Make claims traceable via supplied source IDs. "
             "Include actionable detail and relevant speaker notes. Use at most three points unless the user requires more. "
             "Use short action phrases for process steps. Do not put step numbers or generic prefixes in point titles. "
             "For product features, name the feature and describe only what the brief establishes. Keep each point title within 65 characters "
             "and each point body within 130 characters. "
             "Distinguish established results from forecasts or hypotheses. If the brief limits claims to supplied facts, omit "
             "unsupported qualitative claims too, such as calling USB-C charging fast. If the brief asks to identify figures "
             "as illustrative, label them explicitly in the visible copy on slides that report those figures. "
             "Qualify claims about speed, feasibility, causality and performance when comparative evidence is absent. "
             "Never turn a possible application into a demonstrated capability. "
             "Only include numerical charts if their values are grounded in sources. For semantic exhibits, populate exhibit_observations "
             "with the numerical inputs, source IDs and exact supporting quotes from the supplied excerpts. Map the metric to the "
             "series, criterion, axis or flow name and the period to the exact displayed category. "
             "Provide a table as an array of rows, or null if absent. "
             "Keep repeated scope, units and commitments consistent with the brief and earlier exhibits. "
             "Preserve required terms, every requested table row, and negative signs. Keep unsupported explanations out of "
             "findings, including implied causes for changes over time. Keep the measurement separate from a metric label. "
             "Use an image ID only from provided image assets. Keep any requested uploaded image on its assigned slide. "
             "When image generation is enabled and a relevant uploaded asset is unavailable, provide an illustration prompt in image_prompt."
             + style_guidance + period_guidance + point_icon_guidance(part, document, brand),
             {"item": part.model_dump(), "topic": document.title, "slide_index": index,
              "imagery_direction": document.theme.design.imagery if document.theme.design else "",
              "brief_source_id": context.get("brief_source_id"),
              "required_exhibit": part.exhibit,
              "visual_choice": part.visual.model_dump(mode="json") if part.visual else None,
              "point_count": point_count,
              "omit_phase_quantities": phases_without_quantities,
              "earlier_exhibits": context.get("earlier_exhibits", []),
              "earlier_slide_titles": [slide.title for slide in document.slides[-5:]],
              **slide_inputs(item, context, part if data_driven else None), "generate_image": bool(can_illustrate),
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
            if not image_configured():
                raise ProviderError("The image provider is unavailable.")
            if settings().ai_job_cost_budget > 0 and ai.usage["estimated_cost"] + settings().image_cost_usd > settings().ai_job_cost_budget:
                raise ProviderError("The estimated job budget has no room for this illustration.")
            prompt = content.image_prompt
            if document.theme.design:
                prompt += " Deck imagery direction: " + document.theme.design.imagery
            key = render_image(job_id, token, index, item.owner_id, prompt)
            if not key:
                return None
            content.image_asset_id = key
            context["generated_images"] = [*context.get("generated_images", []), key]
            ai.usage["images"] = ai.usage.get("images", 0) + 1
            ai.usage["estimated_cost"] = round(ai.usage["estimated_cost"] + settings().image_cost_usd, 6)
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
        use_vision = settings().llm_vision_enabled and ai.provider != "demo" and important

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
        if use_vision and not context.get("review_attempted"):
            try:
                review = ai.structured(Review, "Review the selected slide's reading order, emphasis, imagery, and density. "
                    "Request regeneration only if concise copy could fix a material problem.", {
                        "title": slide.title, "layout": slide.layout, "warnings": slide.warnings,
                    }, "vision", base64.b64encode(png).decode("ascii"))
            except ProviderError:
                review = Review()
            if review.regenerate_content:
                context["review_attempted"] = True
                context["review_issues"] = review.issues
                return ("revise", state, "REVIEWING", events, None, None)
            slide.warnings.extend(review.issues)
        slide.warnings = list(dict.fromkeys(slide.warnings))[:30]
        part = OutlineItem.model_validate(context["outline"]["slides"][index])
        if part.exhibit and (exhibit := context["slide_content"].get(part.exhibit)):
            earlier = context.get("earlier_exhibits", [])
            context["earlier_exhibits"] = [*earlier[-5:], {"slide": index + 1, part.exhibit: exhibit}]
        document.slides.append(slide)
        context["_preview_bytes"] = png
        context["progress_index"] = index + 1
        changed = document
        context.pop("draft", None)
        context.pop("slide_content", None)
        context.pop("review_attempted", None)
        context.pop("review_issues", None)
        context.pop("web_image", None)
        context.pop("web_image_warning", None)
        context.pop("slide_sources", None)
        events.append(("slide.completed", f"Slide {index + 1} is ready.", {"slide_id": slide.id, "index": index, "warnings": slide.warnings}))
        stage, status = "slide", "GENERATING"
    elif stage == "revise":
        index = context.get("progress_index", len(document.slides))
        part = OutlineItem.model_validate(context["outline"]["slides"][index])
        content = ai.structured(SlideContent, "Rewrite the content concisely to address the visual review findings. Preserve the meaning, "
            "exact figures, specialized exhibits, decision rules, and supplied source IDs."
            + point_icon_guidance(part, document, brand), {
            **slide_inputs(item, context, part if data_driven else None), "item": part.model_dump(), "topic": document.title,
            "previous": context["slide_content"], "issues": context["review_issues"], "required_exhibit": part.exhibit,
            "visual_choice": part.visual.model_dump(mode="json") if part.visual else None,
            "point_count": part.point_count if context["slide_content"].get("points") else None,
            "omit_phase_quantities": omit_phase_quantities(part, inferred_intent),
        }, "slide")
        preserve_cover_cap(content, item.request["prompt"])
        images = image_ids(assets, context)
        previous = SlideContent.model_validate(context["slide_content"])
        if previous.image_asset_id in images:
            content.image_asset_id = previous.image_asset_id
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
