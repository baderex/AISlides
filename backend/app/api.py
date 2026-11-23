import asyncio
import base64
import json
import re
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from sqlalchemy import select, tuple_, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app import artifacts
from app.artifact_render import MIME, build
from app.asset_lifecycle import collect_unused, drain_deletes, referenced_ids, retire_skill, schedule_deletes, store_asset, track, track_document
from app.auth import COOKIE, authenticate, digest, login_session, passwords, resolve
from app.branding import PPTX, inspect_template
from app.config import settings
from app.composition_models import OutlineItem, SlideContent
from app.db import (
    ArtifactJob, Asset, AssetReference, ExportRecord, Job, LoginSession, PresentationRecord,
    Session, SkillRecord, SlidePreview, User, Version, begin_write, lock_account, now, uid,
)
from app.domain import Contract, CreatePresentation, Document, Layout, Recipe, Skill, Slide, read_document
from app.edits import ApplyEdit, ChatRequest, apply, propose
from app.events import event_response, fetch_events
from app.export_review import renderer
from app.imagery import image_configured
from app.limits import chat_slot, consume
from app.lucide import bundled_icon, icon_png
from app.presentations import (
    Conflict, assert_editable, assets_for, details, emit, image_ids, latest_job, persist,
    present_owned, queue_generation, referenced_images, skill_for, summary,
)
from app.previews import cached_preview, fingerprint
from app.questions import Answers, Clarification, Choice, SlideCount, validate_answers
from app.queue import notify
from app.slide_design import recipe_options, recompose_slide
from app.skills import list_skills
from app.storage import Storage, decode_upload
from app.visuals import Visual

api = APIRouter(prefix="/api/v1")
owner = Depends(resolve)


class Credentials(Contract):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=10, max_length=128)


class Save(Contract):
    revision: int = Field(ge=0)
    document: Document
    label: str = Field(default="Manual edit", min_length=1, max_length=100)


class SaveSlide(Contract):
    revision: int = Field(ge=0)
    slide: Slide


class EditExhibit(SaveSlide):
    visual: Visual
    recipe: Recipe | None = None
    illustrative: bool | None = None


def limit_auth(request):
    address = request.client.host if request.client else "unknown"
    with Session.begin() as db:
        consume(db, "auth:" + digest(address), 60, 1, 10, "Too many sign-in attempts. Try again in a minute.")


def item_for(db, item_id, owner_id, lock=False):
    item = present_owned(db, item_id, owner_id, lock)
    if not item:
        raise HTTPException(404, "Presentation not found.")
    if lock:
        lock_account(db, owner_id)
    return item


def validate_document_assets(db, owner_id, document):
    ids = {key for key in referenced_images(document.slides) if not bundled_icon(key)}
    if (kit := document.theme.brand_kit) and kit.logo_asset_id:
        ids.add(kit.logo_asset_id)
    if image_ids(db, owner_id, ids) != ids:
        raise HTTPException(422, "The deck references a missing or inaccessible image.")
    source_ids = {source.asset_id for source in document.sources if source.asset_id}
    if len(assets_for(db, owner_id, source_ids)) != len(source_ids):
        raise HTTPException(422, "The deck references an inaccessible source asset.")


def presentation_brand(db, item, owner_id):
    record = skill_for(db, owner_id, item.request.get("skill_id"), include_deleted=True)
    brand = Skill.model_validate(record.manifest).brand if record else None
    if brand:
        asset = db.get(Asset, brand.asset_id)
        if not asset or asset.owner_id != owner_id or asset.media_type != PPTX:
            raise HTTPException(409, "The original PowerPoint template is unavailable.")
    return brand


@api.get("/session")
def session_info(owner_id: str = owner):
    with Session() as db:
        return {"email": db.get(User, owner_id).email, "local": settings().implicit_session}


@api.get("/auth/config")
def auth_config():
    return {"allow_signup": settings().allow_signup}


@api.post("/auth/register", status_code=201)
def register(data: Credentials, request: Request, response: Response):
    limit_auth(request)
    if not settings().allow_signup:
        raise HTTPException(403, "Registration is disabled.")
    hashed = passwords.hash(data.password)
    try:
        with Session.begin() as db:
            user = User(email=data.email.lower(), password=hashed)
            db.add(user)
            db.flush()
            login_session(db, user, response)
        return {"email": user.email}
    except IntegrityError as error:
        raise HTTPException(409, "An account with that email already exists.") from error


@api.post("/auth/login")
def login(data: Credentials, request: Request, response: Response):
    limit_auth(request)
    with Session.begin() as db:
        user = authenticate(db, data.email.lower(), data.password)
        if not user:
            raise HTTPException(401, "Email or password is incorrect.")
        login_session(db, user, response)
    return {"email": user.email}


@api.post("/auth/logout")
def logout(request: Request, response: Response):
    if token := request.cookies.get(COOKIE):
        with Session.begin() as db:
            session = db.get(LoginSession, digest(token))
            if session:
                db.delete(session)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@api.get("/capabilities")
def capabilities(owner_id: str = owner):
    return {"research": bool(settings().research_api_key), "images": image_configured()}


@api.get("/schemas/presentation")
def presentation_schema():
    return Document.model_json_schema()


@api.get("/skills")
def skills(owner_id: str = owner):
    with Session() as db:
        return list_skills(db, owner_id)


@api.get("/skills/{skill_id}")
def skill(skill_id: str, owner_id: str = owner):
    with Session() as db:
        record = skill_for(db, owner_id, skill_id)
        if not record:
            raise HTTPException(404, "Skill not found.")
        return {"id": record.id, **record.manifest}


@api.get("/skills/{skill_id}/export")
def export_skill(skill_id: str, owner_id: str = owner):
    with Session() as db:
        record = skill_for(db, owner_id, skill_id)
        if not record:
            raise HTTPException(404, "Skill not found.")
        if brand := record.manifest.get("brand"):
            asset = db.get(Asset, brand["asset_id"])
            if not asset or asset.owner_id != owner_id or asset.media_type != PPTX:
                raise HTTPException(404, "The original PowerPoint template is unavailable.")
            return download(Storage().get(asset.id), PPTX, record.manifest["name"], "pptx")
        return download(json.dumps(record.manifest, indent=2).encode(), "application/json", record.manifest["name"], "json")


@api.delete("/skills/{skill_id}", status_code=204, response_class=Response)
def delete_skill(skill_id: str, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        lock_account(db, owner_id)
        result = db.execute(update(SkillRecord).where(
            SkillRecord.id == skill_id, SkillRecord.owner_id == owner_id, SkillRecord.deleted.is_(False),
        ).values(deleted=True))
        if result.rowcount != 1:
            raise HTTPException(404, "Skill not found.")
        collect_unused(db, owner_id, retire_skill(db, owner_id, skill_id))
    drain_deletes()
    return Response(status_code=204)


@api.post("/skills", status_code=201)
def create_skill(manifest: Skill, owner_id: str = owner):
    if manifest.brand:
        raise HTTPException(422, "Import the original PowerPoint file to create a branded template.")
    if kit := manifest.theme.brand_kit:
        if not manifest.theme.design and set(kit.protected) & {"heading_font", "cover_background", "dense_surface", "chart_colors"}:
            raise HTTPException(422, "Include design rules for the protected brand roles.")
        if kit.logo_asset_id:
            with Session() as db:
                logos = assets_for(db, owner_id, [kit.logo_asset_id])
                if not logos or not logos[0].media_type.startswith("image/"):
                    raise HTTPException(422, "Select an image from your uploads for the brand logo.")
    return store_skill(manifest, owner_id)


def store_skill(manifest: Skill, owner_id: str):
    with Session.begin() as db:
        begin_write(db)
        lock_account(db, owner_id)
        asset_ids = referenced_ids(manifest.model_dump())
        if len(assets_for(db, owner_id, asset_ids)) != len(asset_ids):
            raise HTTPException(422, "A skill asset is unavailable.")
        record = SkillRecord(owner_id=owner_id, manifest=manifest.model_dump(mode="json"))
        db.add(record)
        db.flush()
        track(db, owner_id, "skill:" + record.id, record.manifest, skill_id=record.id)
        result = {"id": record.id, **record.manifest}
    return result


@api.post("/skills/from-asset/{asset_id}", status_code=201)
def create_skill_from_asset(asset_id: str, owner_id: str = owner, *, branded: bool = False):
    with Session() as db:
        assets = assets_for(db, owner_id, [asset_id])
        if not assets:
            raise HTTPException(404, "Reference asset not found.")
        asset = assets[0]
    if branded:
        if asset.media_type != PPTX:
            raise HTTPException(422, "Choose a .pptx file for the branded template.")
        raw = Storage().get(asset.id)
        try:
            manifest = inspect_template(raw, asset.id, asset.name.rsplit(".", 1)[0] or "PowerPoint template")
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except Exception as error:
            raise HTTPException(422, "The PowerPoint template contains unreadable masters or layouts.") from error
        return store_skill(manifest, owner_id)
    if asset.media_type == "application/json":
        try:
            return create_skill(Skill.model_validate_json(Storage().get(asset.id)), owner_id)
        except ValueError as error:
            raise HTTPException(422, "The skill package does not match the version 1 manifest.") from error
    if not asset.extracted.strip():
        raise HTTPException(422, "The reference has no extractable text.")
    manifest = Skill(name=asset.name.rsplit(".", 1)[0], category="Custom",
                     description=f"Presentation recipe based on {asset.name}.",
                     instructions="Use the following reference as a guide to narrative structure.\n" + asset.extracted[:2800],
                     recipes=["cover", "summary", "closing"])
    return create_skill(manifest, owner_id)


@api.post("/assets", status_code=201)
async def upload(file: UploadFile = File(...), owner_id: str = owner):
    name = re.split(r"[/\\]", file.filename or "upload")[-1][:200]
    maximum = settings().asset_limit_bytes
    raw = await file.read(maximum + 1)
    if len(raw) > maximum:
        raise HTTPException(413, f"File exceeds the {maximum // (1024*1024)} MB upload limit.")
    declared = (file.content_type or "").split(";")[0].lower()
    if name.lower().endswith((".pptx", ".docx", ".pdf")):
        key = await run_in_threadpool(artifacts.enqueue, owner_id, None, "conversion", {"name": name, "declared": declared}, raw)
        return await completed_artifact(key, owner_id)
    return await run_in_threadpool(save_upload, owner_id, name, raw, declared)


def save_upload(owner_id, name, raw, declared):
    try:
        media_type, raw, extracted = decode_upload(name, raw, declared)
    except Exception as error:
        raise HTTPException(415, "The file is invalid or uses an unsupported format.") from error
    with Session.begin() as db:
        asset = Asset(owner_id=owner_id, name=name, media_type=media_type, size=len(raw), extracted=extracted)
        begin_write(db)
        store_asset(db, asset, raw)
        result = {"id": asset.id, "name": asset.name, "media_type": asset.media_type, "size": asset.size}
    return result


@api.get("/assets/{asset_id}")
def get_asset(asset_id: str, owner_id: str = owner):
    if icon := bundled_icon(asset_id):
        return Response(icon_png(*icon), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
    with Session() as db:
        asset = db.get(Asset, asset_id)
        if not asset or asset.owner_id != owner_id:
            raise HTTPException(404, "Asset not found.")
    raw = Storage().get(asset.id)
    if asset.media_type.startswith("image/"):
        return Response(raw, media_type=asset.media_type, headers={"Cache-Control": "private, max-age=3600"})
    return download(raw, asset.media_type, asset.name, None)


@api.get("/presentations")
def presentations(response: Response, owner_id: str = owner, cursor: str = Query(default="", max_length=256),
                  q: str = Query(default="", max_length=200), limit: int = Query(default=100, ge=1, le=100)):
    with Session() as db:
        deck = PresentationRecord
        query = select(deck).where(deck.owner_id == owner_id)
        if q.strip():
            query = query.where(deck.title.icontains(q.strip(), autoescape=True))
        if cursor:
            try:
                boundary = json.loads(base64.urlsafe_b64decode(cursor))
                if not isinstance(boundary, list) or len(boundary) != 2 or not all(isinstance(v, str) for v in boundary):
                    raise ValueError
                query = query.where(tuple_(deck.updated, deck.id) < tuple(boundary))
            except (ValueError, TypeError) as error:
                raise HTTPException(422, "Invalid presentation cursor.") from error
        rows = list(db.scalars(query.order_by(deck.updated.desc(), deck.id.desc()).limit(limit + 1)))
        if len(rows) > limit:
            last = rows[limit - 1]
            response.headers["X-Next-Cursor"] = base64.urlsafe_b64encode(json.dumps([last.updated, last.id]).encode()).decode()
        return [summary(item) for item in rows[:limit]]


@api.post("/presentations", status_code=201)
def create_presentation(data: CreatePresentation, owner_id: str = owner):
    if not data.prompt.strip():
        raise HTTPException(422, "Enter a presentation prompt.")
    with Session.begin() as db:
        begin_write(db)
        lock_account(db, owner_id)
        if len(assets_for(db, owner_id, data.asset_ids)) != len(set(data.asset_ids)):
            raise HTTPException(422, "One or more reference assets are unavailable.")
        skill = skill_for(db, owner_id, data.skill_id)
        if data.skill_id and not skill:
            raise HTTPException(422, "The selected skill is unavailable.")
        manifest = Skill.model_validate(skill.manifest) if skill else None
        if manifest and manifest.brand:
            data.aspect_ratio = manifest.formats[0]
        if data.generate_images and not image_configured():
            raise HTTPException(422, "Image generation is unavailable. Configure an image provider in the backend.")
        title = " ".join(data.prompt.split())[:100]
        doc = Document(title=title, aspect_ratio=data.aspect_ratio)
        if manifest and manifest.brand:
            doc.theme = manifest.theme
        item = PresentationRecord(owner_id=owner_id, title=title, request=data.model_dump(mode="json"), document=doc.model_dump(mode="json"))
        db.add(item)
        db.flush()
        track_document(db, item)
        emit(db, item, "conversation.user", data.prompt)
        if data.generate:
            job_id = queue_generation(db, item).id
        result = details(item)
    if data.generate:
        notify(job_id)
    return result


@api.get("/presentations/{item_id}")
def presentation(item_id: str, owner_id: str = owner):
    with Session() as db:
        return details(item_for(db, item_id, owner_id))


@api.patch("/presentations/{item_id}")
def save_presentation(item_id: str, data: Save, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        assert_editable(item, data.revision)
        if item.document["aspect_ratio"] != data.document.aspect_ratio:
            record = skill_for(db, owner_id, item.request.get("skill_id"), include_deleted=True)
            if record and record.manifest.get("brand"):
                raise HTTPException(422, "A branded presentation uses the original template aspect ratio.")
        validate_document_assets(db, owner_id, data.document)
        persist(db, item, data.document, data.label)
        return details(item)


@api.delete("/presentations/{item_id}", status_code=204, response_class=Response)
def delete_presentation(item_id: str, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        keys = list(db.scalars(select(ExportRecord.id).where(ExportRecord.presentation_id == item.id)))
        keys.extend(db.scalars(select(SlidePreview.storage_key).where(SlidePreview.presentation_id == item.id)))
        candidates = list(db.scalars(select(AssetReference.asset_id).where(AssetReference.presentation_id == item.id)))
        skill_id = item.request.get("skill_id")
        keys.extend(db.scalars(select(ArtifactJob.id).where(ArtifactJob.presentation_id == item.id,
                               ArtifactJob.state == "complete", ArtifactJob.kind.in_(("preview", "artwork")))))
        schedule_deletes(db, keys)
        db.delete(item)
        db.flush()
        candidates.extend(retire_skill(db, owner_id, skill_id))
        collect_unused(db, owner_id, candidates)
    drain_deletes()
    return Response(status_code=204)


@api.post("/presentations/{item_id}/duplicate", status_code=201)
def duplicate(item_id: str, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        original = item_for(db, item_id, owner_id, True)
        doc = read_document(original.document)
        if not doc.slides:
            raise HTTPException(409, "There are no slides to duplicate.")
        doc.title = original.title[:190] + " (copy)"
        item = PresentationRecord(owner_id=owner_id, title=doc.title, status="COMPLETED",
                                  request=original.request, document=doc.model_dump(mode="json"))
        db.add(item)
        db.flush()
        persist(db, item, doc, "Duplicated presentation")
        return details(item)


@api.post("/presentations/{item_id}/generate", status_code=202)
def generate(item_id: str, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        if item.status == "COMPLETED":
            raise Conflict("Generation has already completed.")
        job = queue_generation(db, item, restart_failed=True)
        if item.request.get("generate") is False:
            item.request = {**item.request, "generate": True}
        result = {"job_id": job.id, "state": job.state}
    notify(job.id)
    return result


@api.post("/presentations/{item_id}/cancel")
def cancel(item_id: str, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        job = latest_job(db, item.id)
        if not job or job.state not in {"queued", "running", "waiting"}:
            raise Conflict("There is no active generation job.")
        job.state, job.cancelled, job.lease, job.updated = "cancelled", True, None, now()
        job.lease_owner = None
        item.status, item.updated = "CANCELLED", now()
        emit(db, item, "generation.cancelled", "Generation stopped. Completed slides were kept.", job)
        return details(item)


@api.get("/jobs/{job_id}")
def job_details(job_id: str, owner_id: str = owner):
    with Session() as db:
        job = db.get(Job, job_id)
        if not job or not present_owned(db, job.presentation_id, owner_id):
            raise HTTPException(404, "Job not found.")
        return {"id": job.id, "state": job.state, "checkpoint": job.checkpoint, "error": job.error, "usage": job.context.get("usage")}


@api.get("/presentations/{item_id}/events")
async def events(item_id: str, request: Request, owner_id: str = owner,
                 after: int = Query(default=0, ge=0, le=2**63-1), last_event_id: str | None = Header(default=None)):
    def check_access():
        with Session() as db:
            item_for(db, item_id, owner_id)
    await run_in_threadpool(check_access)
    cursor = max(after, int(last_event_id) if last_event_id and last_event_id.isascii() and last_event_id.isdecimal()
                 and len(last_event_id) <= 18 else 0)
    return event_response(item_id, cursor, request)


@api.get("/presentations/{item_id}/questions")
def questions(item_id: str, owner_id: str = owner):
    with Session() as db:
        item = item_for(db, item_id, owner_id)
        job = latest_job(db, item.id)
        return {"waiting": bool(job and job.state == "waiting"), "questions": job.context.get("questions", {}).get("questions", []) if job else [],
                "answers": job.context.get("answers", []) if job else [], "history": job.context.get("question_history", []) if job else []}


@api.post("/presentations/{item_id}/answers", status_code=202)
def answer(item_id: str, submission: Answers, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        job = latest_job(db, item.id)
        if not job or job.state != "waiting":
            raise Conflict("The job is no longer waiting for answers.")
        batch = Clarification.model_validate(job.context["questions"])
        try:
            resolved = validate_answers(batch, submission)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        job.context = {**job.context, "answers": [*job.context.get("answers", []), *resolved],
                       "count_questions": [*job.context.get("count_questions", []), *[q.id for q in batch.questions if isinstance(q, SlideCount)]],
                       "style_questions": [*job.context.get("style_questions", []), *[q.id for q in batch.questions if isinstance(q, Choice) and q.type in {"visual_style", "theme"}]]}
        job.state, job.updated = "queued", now()
        item.status, item.updated = ("PLANNING" if job.checkpoint == "plan" else "RESEARCHING"), now()
        emit(db, item, "conversation.user", "\n".join(f"{entry['title']} {entry['value']}" for entry in resolved), job)
        emit(db, item, "generation.resumed", "Answers saved. Continuing from the saved checkpoint.", job)
    notify(job.id)
    return {"job_id": job.id, "state": "queued"}


@api.get("/presentations/{item_id}/slides")
def slides(item_id: str, owner_id: str = owner):
    with Session() as db:
        return read_document(item_for(db, item_id, owner_id).document).slides


@api.patch("/presentations/{item_id}/slides/{slide_id}")
def save_slide(item_id: str, slide_id: str, data: SaveSlide, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        assert_editable(item, data.revision)
        document = read_document(item.document)
        if data.slide.id != slide_id or not any(slide.id == slide_id for slide in document.slides):
            raise HTTPException(404, "Slide not found.")
        document.slides = [data.slide if slide.id == slide_id else slide for slide in document.slides]
        document = read_document(document.model_dump(mode="json"))
        validate_document_assets(db, owner_id, document)
        persist(db, item, document, "Slide edit")
        return details(item)


@api.post("/presentations/{item_id}/slides/{slide_id}/exhibit", response_model=Slide)
def edit_exhibit(item_id: str, slide_id: str, data: EditExhibit, owner_id: str = owner):
    with Session() as db:
        item = item_for(db, item_id, owner_id)
        assert_editable(item, data.revision)
        document = read_document(item.document)
        draft = data.slide
        if draft.id != slide_id:
            raise HTTPException(404, "Slide not found.")
        original = next((slide for slide in document.slides if slide.id == slide_id), None)
        if not original and draft.spec:
            original = next((slide for slide in document.slides if slide.spec and
                             slide.spec.id_namespace == draft.spec.id_namespace), None)
        # Existing slides can be edited locally more than once before saving.
        # Accept their generated baseline along with the current visual overrides.
        if not original or not draft.spec or not draft.spec.content.get("visual"):
            raise HTTPException(422, "This slide has no editable exhibit.")
        if not set(draft.source_ids) <= {source.id for source in document.sources}:
            raise HTTPException(422, "The slide cites an unknown source.")
        brand = presentation_brand(db, item, owner_id)
        try:
            content = SlideContent.model_validate(draft.spec.content)
            part = OutlineItem.model_validate(draft.spec.plan)
        except ValidationError as error:
            raise HTTPException(422, "The saved exhibit data is invalid.") from error
        if not content.visual or content.visual.type != data.visual.type:
            raise HTTPException(422, "The exhibit type must match the slide.")
        content.visual = data.visual
        if data.illustrative is not None:
            content.illustrative_exhibit = data.illustrative
        options = ["standard"] if brand else recipe_options(part, content, document)
        if data.recipe is not None and data.recipe not in options:
            raise HTTPException(422, "That layout is unavailable for this exhibit.")
        index = next(i for i, slide in enumerate(document.slides) if slide.id == original.id)
        result = recompose_slide(draft, document, index, keep_recipe=True, replacement_content=content,
                                 selected_recipe=data.recipe, brand=brand)
        if result is None:
            raise HTTPException(422, "The exhibit could not be rebuilt with these values and edits.")
        validate_document_assets(db, owner_id, document.model_copy(update={"slides": [result]}))
        from app.exhibit_evidence import display_evidence

        return display_evidence(document, result)


@api.get("/presentations/{item_id}/slides/{slide_id}/preview")
async def slide_preview(item_id: str, slide_id: str, owner_id: str = owner):
    def prepare():
        with Session() as db:
            item = item_for(db, item_id, owner_id)
            document = read_document(item.document)
            slide = next((entry for entry in document.slides if entry.id == slide_id), None)
            if not slide:
                raise HTTPException(404, "Slide not found.")
            validate_document_assets(db, owner_id, document)
            brand = presentation_brand(db, item, owner_id)
            executable = (settings().powerpoint_renderer or renderer()) if brand else None
            if not executable and (raw := cached_preview(db, item_id, document, slide)):
                return Response(raw, media_type="image/png")
            params = {"document": document.model_copy(update={"slides": [slide]}).model_dump(mode="json"),
                      "brand": brand.model_dump(mode="json") if brand else None, "renderer": executable,
                      "fingerprint": fingerprint(document, slide)}
        return artifacts.enqueue(owner_id, item_id, "preview", params)
    return await image_response(prepare, owner_id)


async def image_response(prepare, owner_id):
    for _ in range(2):
        value = await run_in_threadpool(prepare)
        if isinstance(value, Response):
            return value
        result = await completed_artifact(value, owner_id)
        try:
            raw = await run_in_threadpool(lambda: Storage().get(result["file_key"]))
            return Response(raw, media_type="image/png", headers={"Cache-Control": "private, no-cache"})
        except FileNotFoundError:
            await run_in_threadpool(artifacts.forget_image, value, owner_id)
    raise HTTPException(503, "The rendered preview is unavailable.")


@api.get("/presentations/{item_id}/master-artwork")
async def slide_master_artwork(item_id: str, layout: Layout, background: str = Query(pattern=r"^#[0-9a-fA-F]{6}$"),
                              index: int | None = Query(default=None, ge=0), owner_id: str = owner):
    def prepare():
        with Session() as db:
            item = item_for(db, item_id, owner_id)
            brand = presentation_brand(db, item, owner_id)
            aspect = item.document["aspect_ratio"]
        if not brand or not (executable := settings().powerpoint_renderer or renderer()):
            return Response(status_code=204)
        if index is not None and not any(entry.index == index for entry in brand.layouts):
            raise HTTPException(404, "PowerPoint layout not found.")
        slide = Slide(id="master", title="Master artwork", layout=layout, background=background.upper(), brand_layout_index=index)
        return artifacts.enqueue(owner_id, item_id, "artwork", {"brand": brand.model_dump(mode="json"),
            "slide": slide.model_dump(mode="json"), "aspect": aspect, "renderer": executable})
    return await image_response(prepare, owner_id)


@api.get("/presentations/{item_id}/versions")
def versions(item_id: str, owner_id: str = owner):
    with Session() as db:
        item = item_for(db, item_id, owner_id)
        query = select(Version).where(Version.presentation_id == item.id).order_by(Version.revision.desc())
        return [{"revision": item.revision, "label": item.label, "created": item.created} for item in db.scalars(query)]


@api.post("/presentations/{item_id}/versions/{revision}/restore")
def restore(item_id: str, revision: int, expected: int, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        assert_editable(item, expected)
        saved = db.get(Version, (item.id, revision))
        if not saved:
            raise HTTPException(404, "Version not found.")
        persist(db, item, read_document(saved.document), f"Restored version {revision}")
        return details(item)


@api.post("/presentations/{item_id}/skill", status_code=201)
def save_as_skill(item_id: str, owner_id: str = owner):
    with Session() as db:
        item = item_for(db, item_id, owner_id)
        document = read_document(item.document)
        existing = skill_for(db, owner_id, item.request.get("skill_id"), include_deleted=True)
        original = Skill.model_validate(existing.manifest) if existing else None
    return store_skill(Skill(name=document.title, category=original.category if original and original.brand else "Custom",
                             description=f"Design and layout recipe from {document.title}.", theme=document.theme,
                             formats=[document.aspect_ratio], recipes=list(dict.fromkeys(slide.layout for slide in document.slides)),
                             brand=original.brand if original else None), owner_id)


@api.post("/presentations/{item_id}/chat")
def chat(item_id: str, data: ChatRequest, owner_id: str = owner):
    with Session() as db:
        item = item_for(db, item_id, owner_id)
        assert_editable(item, item.revision)
        document = read_document(item.document)
        locked_theme = presentation_brand(db, item, owner_id) is not None
        revision = item.revision
    with chat_slot(owner_id):
        proposal = propose(document, data)
    revised = apply(document, proposal, locked_theme=locked_theme)
    with Session.begin() as db:
        begin_write(db)
        current = item_for(db, item_id, owner_id, True)
        assert_editable(current, revision)
        validate_document_assets(db, owner_id, revised)
        emit(db, current, "conversation.user", data.instruction)
        emit(db, current, "conversation.assistant", proposal.summary)
    return {"proposal": proposal, "revision": revision, "document": revised}


@api.post("/presentations/{item_id}/chat/apply")
def apply_chat(item_id: str, data: ApplyEdit, owner_id: str = owner):
    with Session.begin() as db:
        begin_write(db)
        item = item_for(db, item_id, owner_id, True)
        assert_editable(item, data.revision)
        updated = apply(read_document(item.document), data.proposal,
                        locked_theme=presentation_brand(db, item, owner_id) is not None)
        validate_document_assets(db, owner_id, updated)
        persist(db, item, updated, "AI-assisted edit")
        emit(db, item, "conversation.assistant", "Applied the proposed slide edits.")
        return details(item)


def download(raw, media_type, title, extension):
    title = "".join(c for c in title if not unicodedata.category(c).startswith("C") and c not in '/\\:"<>|?*')
    title = title.strip(" .")[:150] or "presentation"
    fallback = re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip(" .")[:100] or "presentation"
    suffix = "." + extension if extension else ""
    encoded = quote(title + suffix, safe="")
    disposition = f"""attachment; filename="{fallback}{suffix}"; filename*=UTF-8''{encoded}"""
    return Response(raw, media_type=media_type, headers={"Content-Disposition": disposition})


@api.get("/artifacts/{key}")
def artifact_status(key: str, owner_id: str = owner):
    status = artifacts.status(key, owner_id)
    if not status:
        raise HTTPException(404, "File job not found or expired.")
    return status


async def completed_artifact(key, owner_id):
    deadline = asyncio.get_running_loop().time() + settings().artifact_timeout_seconds * 2
    while True:
        status = await run_in_threadpool(artifact_status, key, owner_id)
        if status["state"] == "complete":
            return status["result"]
        if status["state"] == "failed":
            error = status["result"]
            raise HTTPException(error["status"], error["detail"])
        if asyncio.get_running_loop().time() >= deadline:
            raise HTTPException(503, "File processing is still pending.", headers={"Location": f"/api/v1/artifacts/{key}"})
        await asyncio.sleep(.5)


@api.post("/presentations/{item_id}/export")
def export(item_id: str, format: str = "pptx", background: bool = False, owner_id: str = owner):
    if format not in MIME:
        raise HTTPException(422, "Select pptx or pdf.")
    params = export_snapshot(item_id, owner_id, format)
    if background or len(params["document"]["slides"]) >= settings().export_queue_slide_threshold or (
            format == "pdf" and params["brand"]):
        key = artifacts.enqueue(owner_id, item_id, "export", params)
        location = f"/api/v1/artifacts/{key}"
        return JSONResponse({"id": key, "status_url": location}, status_code=202, headers={"Location": location})
    raw, media_type, result = build("export", params)
    store_export(item_id, owner_id, result["revision"], format, raw, result["title"])
    return download(raw, media_type, result["title"], format)


@api.post("/presentations/{item_id}/export/review")
async def review_export(item_id: str, expected: int, owner_id: str = owner):
    params = await run_in_threadpool(export_snapshot, item_id, owner_id, "pptx", expected)
    key = await run_in_threadpool(artifacts.enqueue, owner_id, item_id, "review", params)
    return await completed_artifact(key, owner_id)


@api.get("/presentations/{item_id}/exports/{key}")
def download_export(item_id: str, key: str, owner_id: str = owner):
    with Session() as db:
        item = item_for(db, item_id, owner_id)
        record = db.get(ExportRecord, key)
        if not record or record.presentation_id != item.id:
            raise HTTPException(404, "Export not found or expired.")
    try:
        raw = Storage().get(record.id)
    except FileNotFoundError as error:
        raise HTTPException(404, "Export not found or expired.") from error
    return download(raw, MIME[record.format], record.title or item.title, record.format)


def export_snapshot(item_id, owner_id, format, expected=None):
    with Session() as db:
        item = item_for(db, item_id, owner_id)
        if expected is not None:
            assert_editable(item, expected)
        document = read_document(item.document)
        if not document.slides:
            raise Conflict("Generate or add a slide before exporting.")
        validate_document_assets(db, owner_id, document)
        revision = item.revision
        brand = presentation_brand(db, item, owner_id)
        return {"document": document.model_dump(mode="json"), "revision": revision, "format": format,
                "brand": brand.model_dump(mode="json") if brand else None}


def store_export(item_id, owner_id, revision, format, raw, title):
    key = uid()
    with Session.begin() as db:
        begin_write(db)
        item_for(db, item_id, owner_id, True)
        db.add(ExportRecord(id=key, presentation_id=item_id, revision=revision, format=format, title=title))
        db.flush()
        Storage().put(key, raw, MIME[format])
    return key
