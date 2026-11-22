import base64
import logging
import re
from datetime import date

from sqlalchemy import select, update

from app.composition import (
    IMAGE_LAYOUTS, EvidenceSummary, Intent, Outline, OutlineItem, ResearchBrief, ResearchQueries, Review, SlideContent, accepts_web_image,
    compose, design, is_data_driven, supports_point_icons,
)
from app.config import settings
from app.consulting import CONSULTING_DIRECTION, PLAN_GUIDANCE, SLIDE_GUIDANCE, requested_consulting
from app.db import Job, PresentationRecord, Session, now
from app.design import ArtDirection, DIRECTOR_PROMPT, reference_sheet
from app.domain import Skill, Slide, Source, Theme, read_document
from app.imagery import image_configured, render_image, source_web_image
from app.presentations import asset_sources, assets_for, begin_write, emit, persist, skill_for
from app.previews import put_preview
from app.providers import Intelligence, ProviderError
from app.questions import Choice, Clarification, Option, standard_options
from app.queue import notify
from app.rendering import flattened, preview, validate_slide
from app.research import collect
from app.storage import Storage

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
    "Every analytical slide should show a supported chart, data table, or numeric KPIs. If the evidence supports a "
    "stronger exhibit than planned, supply it. Call out material data gaps. Do not mix currencies or reporting periods."
)
CALENDAR_YEARS = "one two three four five six seven eight nine ten".split()
YEAR_IN_QUERY = re.compile(r"\b((?:19|20)\d{2})(?:(?:\s*(?:to|[-–])\s*)((?:19|20)\d{2})|(-\d{2}-\d{2}))?\b")


def inputs(item, context):
    return {"prompt": item.request["prompt"], "intent": context.get("intent"),
            "answers": context.get("answers", []), "skill": context.get("skill"),
            "sources": context.get("sources", []), "research": context.get("research")}


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


def pause_for_questions(context, batch, events):
    pending = batch.model_dump(mode="json")
    if context.get("questions"):
        context["question_history"] = [*context.get("question_history", []), context["questions"]]
    context["questions"] = pending
    events.extend([("question.requested", "Your input will help shape the presentation.", {"count": len(batch.questions)}),
                   ("generation.paused", "Waiting for your answers.", {})])


def resolve_web_research(item, context, data_driven, events):
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


def process(job_id, token, item, stage, context, assets, skill, ai):
    document = read_document(item.document)
    brand = skill.brand if skill else None
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
            context["draft"] = compose(part, previous, document, index, image_ids(assets, context), brand,
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
            "Set web_images_requested only if the user explicitly requests "
            "existing photos, portraits, or other sourced images. A request for research, charts, or generated illustrations does not imply web images.",
            inputs(item, context))
        if brand:
            intent.visual_direction = "Branded PowerPoint template"
        elif requested_consulting(item.request["prompt"]):
            intent.visual_direction = CONSULTING_DIRECTION
        context["intent"] = intent.model_dump(mode="json")
        events.append(("agent.progress", f"Identified the topic: {intent.subject}.", {"intent": context["intent"]}))
        stage, status = "clarify", "ANALYZING"
    elif stage == "clarify":
        batch = ai.structured(Clarification,
            "Ask 0 to 3 high-impact clarification questions only when important intent is ambiguous. Never ask for an audience, style or count already supplied. "
            "For a broad topic without an audience or visual direction, prefer exactly two questions with types audience and visual_style. "
            "Use labels Executive / Board and Editorial report among suitable options. When the prompt is detailed return an empty list.", inputs(item, context))
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
                    {**inputs(item, context), "as_of": today.isoformat(), "last_completed_year": today.year - 1}, "research")
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
                    "short uncertainties." + period_guidance, evidence_inputs(item, context), "research")
            else:
                brief = ai.structured(ResearchBrief,
                    "Summarize evidence, gaps and contradictions in the supplied sources. Treat search excerpts as provisional. "
                    "Do not introduce new sources. Return a follow-up question only when contradictory evidence requires a "
                    "user decision that previous answers do not cover.", inputs(item, context), "research")
            context["research"] = brief.model_dump(mode="json", exclude={"follow_up"})
            events.append(("research.completed", brief.summary, {"sources": context["sources"], "uncertainties": brief.uncertainties}))
            if isinstance(brief, ResearchBrief) and brief.follow_up and brief.uncertainties:
                used_ids = {a["question_id"] for a in context.get("answers", [])}
                used_kinds = {q["type"] for q in context.get("questions", {}).get("questions", [])}
                question = brief.follow_up
                if question.type not in used_kinds and question.id not in used_ids:
                    pause_for_questions(context, Clarification(questions=[question]), events)
                    return "design", "waiting", "WAITING_FOR_USER", events, None, None
        else:
            if data_driven:
                reason = ("Web research returned no usable sources." if web_research else
                          "Web research is off." if settings().research_api_key else "Web research is unavailable.")
                events.append(("research.completed", "No source data was available for charts or statistics.",
                               {"uncertainties": [reason]}))
            else:
                events.append(("research.completed", "No reference sources were provided for this deck.", {}))
        stage, status = "design", "RESEARCHING"
    elif stage == "design":
        if not start_status(job_id, token, "PLANNING", "agent.started", "Choosing the deck typography, palette and visual rules."):
            return None
        baseline = design(direction or ("Data Driven" if data_driven else ""), item.request["mode"], skill.theme if skill else None)
        locked = skill is not None or baseline.profile == "consulting"
        references = [{"name": asset.name, "type": asset.media_type, "excerpt": asset.extracted[:900]} for asset in assets]
        proposed = ai.structured(ArtDirection, DIRECTOR_PROMPT, {
            "prompt": item.request["prompt"], "intent": context["intent"], "answers": context.get("answers", []),
            "direction": direction, "selected_skill": context.get("skill"), "references": references,
            "baseline": baseline.model_dump(), "locked_template": locked,
        }, "design", reference_sheet(assets) if settings().llm_vision_enabled else None)
        theme = Theme.model_validate(proposed.model_dump())
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
        intent = Intent.model_validate(context["intent"])
        count = intent.slide_count
        for answer in context.get("answers", []):
            if answer["question_id"] in context.get("count_questions", []) and answer["value"].isdigit():
                count = int(answer["value"])
        count = count or 6
        theme = document.theme
        style_guidance = " " + PLAN_GUIDANCE if theme.profile == "consulting" else ""
        if data_driven:
            style_guidance += DATA_PLAN_GUIDANCE
        research_guidance = (
            " For each slide, provide one focused research_query naming its subject, exact period and metric to verify. "
            "For company profiles, name the company and seek its official results or annual report."
            if data_driven and web_research else " Leave every research_query empty."
        )
        image_guidance = (
            " Web image search is enabled. Set image_query only where an existing image serves the slide's purpose and "
            "the layout is showcase, editorial, or research without a primary chart or table. "
            "For portraits use the subject's full name and 'official photographic portrait'. Leave other image queries empty."
            if web_research and intent.web_images_requested else " Leave every image_query empty."
        )
        planning_inputs = evidence_inputs(item, context) if data_driven else inputs(item, context)
        outline = ai.structured(Outline,
            f"Plan exactly {count} slides. Honor the requested slide allocation and give every slide a distinct purpose. "
            "If the user requests a cover, use the cover layout and their supplied deck title as its headline. "
            "Use chart layouts only when grounded data is available." + style_guidance + period_guidance + research_guidance + image_guidance,
            {**planning_inputs, "slide_count": count, "design": theme.model_dump()}, "outline")
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
            return ("done", "complete", "COMPLETED",
                    [("generation.completed", "The presentation is ready to edit and export.", {"slides": index, "usage": ai.usage})],
                    document, "Generated presentation")
        part = outline.slides[index]
        if not start_status(job_id, token, "GENERATING", "slide.started", f"Designing slide {index + 1}: {part.title}.", index=index):
            return None
        if data_driven and web_research and part.layout not in {"cover", "section", "quote", "closing"}:
            if "slide_sources" not in context:
                query = part.research_query.strip() or f"{part.title} {part.purpose}"[:260].strip()
                context["slide_sources"] = focused_sources(document, align_query_period(query, period))
            attach_slide_sources(document, context)
        images = image_ids(assets, context)
        can_illustrate = (
            item.request.get("generate_images") and ai.provider != "demo" and image_configured() and
            ai.usage.get("images", 0) < settings().image_limit_per_job and
            part.layout in IMAGE_LAYOUTS and
            (settings().ai_job_cost_budget <= 0 or ai.usage["estimated_cost"] + settings().image_cost_usd <= settings().ai_job_cost_budget)
        )
        style_guidance = " " + SLIDE_GUIDANCE if document.theme.profile == "consulting" else ""
        if data_driven:
            style_guidance += DATA_SLIDE_GUIDANCE
        content = ai.structured(SlideContent, "Write concise content for the single item given. Use its exact title and fulfill its purpose. "
             "Cover the current item only and do not repeat earlier slides. Write for the audience. Keep visual instructions, "
             "color names and comments about the presentation design out of visible copy unless the subject is design. "
             "Make claims traceable via supplied source IDs. "
             "Include actionable detail and relevant speaker notes. Use at most three points. Keep each point title within 65 characters "
             "and each point body within 130 characters. "
             "Distinguish established results from forecasts or hypotheses. Qualify claims about speed, feasibility, causality and performance "
             "whenever no comparative evidence is supplied. Never turn a possible application into a demonstrated capability. "
             "Only include numerical charts if their values are grounded in sources. Provide a table as an array of rows, or null if absent. "
             "Use an image ID only from provided image assets. "
             "When image generation is enabled and a relevant uploaded asset is unavailable, provide an illustration prompt in image_prompt."
             + style_guidance + period_guidance + point_icon_guidance(part, document, brand),
             {"item": part.model_dump(), "topic": document.title, "slide_index": index,
              "imagery_direction": document.theme.design.imagery if document.theme.design else "",
              "earlier_slide_titles": [slide.title for slide in document.slides[-5:]],
              **slide_inputs(item, context, part if data_driven else None), "generate_image": bool(can_illustrate),
              "image_assets": [{"id": a.id, "name": a.name} for a in assets if a.id in images]}, "slide")
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
        context["slide_content"] = content.model_dump(mode="json")
        if can_illustrate and content.image_prompt and content.image_asset_id not in images and not (content.chart or content.table):
            return "image", state, "GENERATING", events, None, None
        context["draft"] = compose(part, content, document, index, images, brand,
                                   web_image_ratio=web_image_ratio, data_driven=data_driven).model_dump(mode="json")
        stage, status = "review", "REVIEWING"
    elif stage == "image":
        index = context.get("progress_index", len(document.slides))
        if not start_status(job_id, token, "GENERATING", "agent.started", f"Generating an illustration for slide {index + 1}.", index=index):
            return None
        content = SlideContent.model_validate(context["slide_content"])
        part = OutlineItem.model_validate(context["outline"]["slides"][index])
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
        slide = compose(part, content, document, index, image_ids(assets, context), brand, data_driven=data_driven)
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
        if warning := context.get("web_image_warning"):
            slide.warnings.append(warning)
        if data_driven and slide.layout == "chart" and not any(element.type == "chart" for element in slide.elements):
            slide.warnings.append("The available evidence did not support the planned chart.")
        text_elements = [element for element, *_ in flattened(slide.elements) if element.type in {"text", "table"}]
        dimensions = [(element.size, element.h) for element in text_elements]
        slide.warnings.extend(validate_slide(document, slide, images))
        storage = Storage()
        png = preview(document, slide, storage.get)
        fixed = sum(original != (element.size, element.h) for original, element in zip(dimensions, text_elements))
        if fixed:
            events.append(("slide.revised", f"Adjusted text fit in {fixed} elements on slide {index + 1}.",
                           {"index": index, "text_elements": fixed}))
        if settings().llm_vision_enabled and ai.provider != "demo" and not context.get("review_attempted"):
            review = ai.structured(Review, "Inspect the rendered slide for density, clipping, contrast and balance. Request regeneration only if a concise rewrite would improve it. Return actionable issues.", {
                "title": slide.title, "layout": slide.layout, "warnings": slide.warnings,
            }, "vision", base64.b64encode(png).decode("ascii"))
            if review.regenerate_content:
                context["review_attempted"] = True
                context["review_issues"] = review.issues
                return ("revise", state, "REVIEWING", events, None, None)
            slide.warnings.extend(review.issues)
        slide.warnings = list(dict.fromkeys(slide.warnings))[:30]
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
        content = ai.structured(SlideContent, "Rewrite the content concisely to address the visual review findings. Preserve the meaning and supplied source IDs."
            + point_icon_guidance(part, document, brand), {
            **slide_inputs(item, context, part if data_driven else None), "item": part.model_dump(), "topic": document.title,
            "previous": context["slide_content"], "issues": context["review_issues"],
        }, "slide")
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
        context["draft"] = compose(part, content, document, index, images, brand,
                                   web_image_ratio=web_image["ratio"] if web_image else None,
                                   data_driven=data_driven).model_dump(mode="json")
        events.append(("slide.revised", f"Reworked slide {index + 1} after visual review.", {"index": index}))
        stage, status = "review", "REVIEWING"
    else:
        raise ValueError(f"Unknown checkpoint {stage}")
    return stage, state, status, events, changed, version
