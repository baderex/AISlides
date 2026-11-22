import base64
import logging

from sqlalchemy import select, update

from app.composition import (
    IMAGE_LAYOUTS, Intent, Outline, OutlineItem, ResearchBrief, Review, SlideContent, accepts_web_image, compose, design,
)
from app.config import settings
from app.consulting import CONSULTING_DIRECTION, PLAN_GUIDANCE, SLIDE_GUIDANCE, requested_consulting
from app.db import Job, PresentationRecord, Session, now
from app.domain import Skill, Slide, Source, read_document
from app.imagery import image_configured, render_image, source_web_image
from app.presentations import asset_sources, assets_for, begin_write, emit, persist, skill_for
from app.previews import put_preview
from app.providers import Intelligence, ProviderError
from app.questions import Clarification, standard_options
from app.queue import notify
from app.rendering import preview, validate_slide
from app.research import collect
from app.storage import Storage

log = logging.getLogger(__name__)


def inputs(item, context):
    return {"prompt": item.request["prompt"], "intent": context.get("intent"),
            "answers": context.get("answers", []), "skill": context.get("skill"),
            "sources": context.get("sources", []), "research": context.get("research")}


def pause_for_questions(context, batch, events):
    pending = batch.model_dump(mode="json")
    if context.get("questions"):
        context["question_history"] = [*context.get("question_history", []), context["questions"]]
    context["questions"] = pending
    events.extend([("question.requested", "Your input will help shape the presentation.", {"count": len(batch.questions)}),
                   ("generation.paused", "Waiting for your answers.", {})])


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
            context["draft"] = compose(part, previous, document, index, image_ids(assets, context), brand).model_dump(mode="json")
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
            "Extract intent, audience, style and requested slide count. Recognize spelled-out numbers. Use empty values for missing information. "
            "Treat a selected skill as a supplied style when appropriate. Set web_images_requested only if the user explicitly requests "
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
        if item.request["research"]:
            context["sources"] = context["sources"] + [s.model_dump(mode="json") for s in collect(context["intent"]["subject"])]
        if context["sources"]:
            brief = ai.structured(ResearchBrief, "Summarize evidence, gaps and contradictions in the supplied sources. Treat search excerpts as provisional. "
                "Do not introduce new sources. Return a follow-up question only when contradictory evidence requires a user decision that previous answers do not cover.", inputs(item, context), "research")
            context["research"] = brief.model_dump(mode="json", exclude={"follow_up"})
            events.append(("research.completed", brief.summary, {"sources": context["sources"], "uncertainties": brief.uncertainties}))
            if brief.follow_up and brief.uncertainties:
                used_ids = {a["question_id"] for a in context.get("answers", [])}
                used_kinds = {q["type"] for q in context.get("questions", {}).get("questions", [])}
                question = brief.follow_up
                if question.type not in used_kinds and question.id not in used_ids:
                    pause_for_questions(context, Clarification(questions=[question]), events)
                    return "plan", "waiting", "WAITING_FOR_USER", events, None, None
        else:
            events.append(("research.completed", "No reference sources were provided for this deck.", {}))
        stage, status = "plan", "RESEARCHING"
    elif stage == "plan":
        if not start_status(job_id, token, "PLANNING", "agent.started", "Planning the narrative and slide sequence."):
            return None
        intent = Intent.model_validate(context["intent"])
        choices = {a["question_id"]: a["value"] for a in context.get("answers", [])}
        count = intent.slide_count
        for answer in context.get("answers", []):
            if answer["question_id"] in context.get("count_questions", []) and answer["value"].isdigit():
                count = int(answer["value"])
        count = count or 6
        requested_direction = CONSULTING_DIRECTION if requested_consulting(item.request["prompt"]) else intent.visual_direction
        direction = choices.get("visual_direction", requested_direction)
        for answer in context.get("answers", []):
            if answer["question_id"] in context.get("style_questions", []):
                direction = answer["value"]
        theme = design(direction, item.request["mode"], skill.theme if skill else None)
        style_guidance = " " + PLAN_GUIDANCE if theme.profile == "consulting" else ""
        image_guidance = (
            " Web image search is enabled. Set image_query only where an existing image serves the slide's purpose and "
            "the layout is showcase, editorial, or research without a primary chart or table. "
            "For portraits use the subject's full name and 'official photographic portrait'. Leave other image queries empty."
            if item.request["research"] and intent.web_images_requested else " Leave every image_query empty."
        )
        outline = ai.structured(Outline,
            f"Plan exactly {count} slides. Use varied purposeful layouts and provide an actionable story. "
            "Use chart layouts only when grounded data is available." + style_guidance + image_guidance,
            {**inputs(item, context), "slide_count": count}, "outline")
        context["outline"] = outline.model_dump(mode="json")
        document.title = outline.title
        document.theme = theme
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
        images = image_ids(assets, context)
        can_illustrate = (
            item.request.get("generate_images") and ai.provider != "demo" and image_configured() and
            ai.usage.get("images", 0) < settings().image_limit_per_job and
            part.layout in IMAGE_LAYOUTS and
            (settings().ai_job_cost_budget <= 0 or ai.usage["estimated_cost"] + settings().image_cost_usd <= settings().ai_job_cost_budget)
        )
        style_guidance = " " + SLIDE_GUIDANCE if document.theme.profile == "consulting" else ""
        content = ai.structured(SlideContent, "Write concise content for the single item given. Use its exact title and fulfill its purpose. "
             "Cover the current item only and do not repeat earlier slides. Make claims traceable via supplied source IDs. "
             "Include actionable detail and relevant speaker notes. Use at most three points. Keep each point title within 65 characters "
             "and each point body within 130 characters. "
             "Distinguish established results from forecasts or hypotheses. Qualify claims about speed, feasibility, causality and performance "
             "whenever no comparative evidence is supplied. Never turn a possible application into a demonstrated capability. "
             "Only include numerical charts if their values are grounded in sources. Provide a table as an array of rows, or null if absent. "
             "Use an image ID only from provided image assets. "
             "When image generation is enabled and a relevant uploaded asset is unavailable, provide an illustration prompt in image_prompt." + style_guidance,
             {"item": part.model_dump(), "topic": document.title, "slide_index": index,
              "earlier_slide_titles": [slide.title for slide in document.slides[-5:]],
              **inputs(item, context), "generate_image": bool(can_illustrate),
              "image_assets": [{"id": a.id, "name": a.name} for a in assets if a.id in images]}, "slide")
        web_image_ratio = None
        if (item.request["research"] and context["intent"].get("web_images_requested") and part.image_query.strip()
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
                                   web_image_ratio=web_image_ratio).model_dump(mode="json")
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
            key = render_image(job_id, token, index, item.owner_id, content.image_prompt)
            if not key:
                return None
            content.image_asset_id = key
            context["generated_images"] = [*context.get("generated_images", []), key]
            ai.usage["images"] = ai.usage.get("images", 0) + 1
            ai.usage["estimated_cost"] = round(ai.usage["estimated_cost"] + settings().image_cost_usd, 6)
        except ProviderError as error:
            warning = str(error)
        slide = compose(part, content, document, index, image_ids(assets, context), brand)
        if warning:
            slide.warnings.append(warning)
        context["draft"], context["slide_content"] = slide.model_dump(mode="json"), content.model_dump(mode="json")
        stage, status = "review", "REVIEWING"
    elif stage == "review":
        index = context.get("progress_index", len(document.slides))
        if not start_status(job_id, token, "REVIEWING", "agent.started", f"Rendering and reviewing slide {index + 1}.", index=index):
            return None
        slide = Slide.model_validate(context["draft"])
        images = image_ids(assets, context)
        if warning := context.get("web_image_warning"):
            slide.warnings.append(warning)
        initial = {element.id: element.size for element in slide.elements if element.type == "text"}
        slide.warnings.extend(validate_slide(document, slide, images))
        storage = Storage()
        png = preview(document, slide, storage.get)
        fixed = sum(initial[element.id] != element.size for element in slide.elements if element.type == "text")
        if fixed:
            events.append(("slide.revised", f"Adjusted {fixed} text boxes to fit slide {index + 1}.", {"index": index}))
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
        events.append(("slide.completed", f"Slide {index + 1} is ready.", {"slide_id": slide.id, "index": index, "warnings": slide.warnings}))
        stage, status = "slide", "GENERATING"
    elif stage == "revise":
        index = context.get("progress_index", len(document.slides))
        part = OutlineItem.model_validate(context["outline"]["slides"][index])
        content = ai.structured(SlideContent, "Rewrite the content concisely to address the visual review findings. Preserve the meaning and supplied source IDs.", {
            **inputs(item, context), "item": part.model_dump(), "topic": document.title,
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
                                   web_image_ratio=web_image["ratio"] if web_image else None).model_dump(mode="json")
        events.append(("slide.revised", f"Reworked slide {index + 1} after visual review.", {"index": index}))
        stage, status = "review", "REVIEWING"
    else:
        raise ValueError(f"Unknown checkpoint {stage}")
    return stage, state, status, events, changed, version
