import logging
from app.db import Job, PresentationRecord, Session
from app.domain import Skill
from app.presentations import assets_for, skill_for
from app.providers import Intelligence, ProviderError
from app.job_state import commit
from app.limits import billing_account


log = logging.getLogger(__name__)


def run_checkpoint(job_id: str, token: str, process):
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
        with billing_account(item.owner_id):
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
