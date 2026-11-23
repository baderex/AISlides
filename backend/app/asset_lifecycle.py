"""Reference accounting for current documents, saved versions, jobs and reusable skills."""
import logging
from datetime import datetime, timedelta, timezone
from itertools import islice

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db import ArtifactJob, Asset, AssetReference, ExportRecord, PendingDelete, PresentationRecord, Session, SkillRecord, SlidePreview, begin_write, lock_account
from app.limits import LimitExceeded
from app.storage import Storage


log = logging.getLogger(__name__)


def referenced_ids(value):
    ids = set()
    if isinstance(value, list):
        for entry in value:
            ids.update(referenced_ids(entry))
    elif isinstance(value, dict):
        for key, entry in value.items():
            if key in {"asset_id", "logo_asset_id", "image_asset_id"} and isinstance(entry, str):
                ids.add(entry)
            elif key in {"asset_ids", "generated_images"} and isinstance(entry, list):
                ids.update(x for x in entry if isinstance(x, str))
            elif isinstance(entry, (list, dict)):
                ids.update(referenced_ids(entry))
    return ids


def track(db, owner_id, scope, value, *, presentation_id=None, skill_id=None):
    wanted = referenced_ids(value)
    existing = set(db.scalars(select(AssetReference.asset_id).where(AssetReference.scope == scope)))
    removed = existing - wanted
    if removed:
        db.execute(delete(AssetReference).where(AssetReference.scope == scope, AssetReference.asset_id.in_(removed)))
    if added := wanted - existing:
        ids = db.scalars(select(Asset.id).where(Asset.id.in_(added), Asset.owner_id == owner_id).with_for_update())
        db.add_all(AssetReference(scope=scope, asset_id=key, presentation_id=presentation_id, skill_id=skill_id)
                   for key in ids)


def track_document(db, item, *, version=False):
    track(db, item.owner_id, "deck:" + item.id, [item.request, item.document], presentation_id=item.id)
    if version:
        track(db, item.owner_id, f"version:{item.id}:{item.revision}", item.document, presentation_id=item.id)


def store_asset(db, asset, raw):
    lock_account(db, asset.owner_id)
    used = db.scalar(select(func.coalesce(func.sum(Asset.size), 0)).where(Asset.owner_id == asset.owner_id))
    if used + len(raw) > settings().account_asset_limit_bytes:
        raise LimitExceeded("Your account has reached its asset storage limit.")
    db.add(asset)
    db.flush()
    Storage().put(asset.id, raw, asset.media_type)


def retire_skill(db, owner_id, skill_id):
    skill = db.get(SkillRecord, skill_id) if skill_id else None
    if not skill or not skill.deleted or skill.owner_id != owner_id:
        return []
    in_use = select(PresentationRecord.id).where(PresentationRecord.owner_id == owner_id,
               PresentationRecord.request["skill_id"].as_string() == skill_id).limit(1)
    if db.scalar(in_use):
        return []
    ids = list(db.scalars(select(AssetReference.asset_id).where(AssetReference.skill_id == skill_id)))
    db.delete(skill)
    db.flush()
    return ids


def schedule_deletes(db, keys):
    rows = [{"id": key} for key in set(keys)]
    if rows:
        insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
        db.execute(insert(PendingDelete).on_conflict_do_nothing(), rows)


def collect_unused(db, owner_id, candidates):
    if not candidates:
        return
    # Serialize attachment and collection on each asset. Recheck references after taking the locks.
    assets = list(db.scalars(select(Asset).where(Asset.owner_id == owner_id, Asset.id.in_(set(candidates)))
                             .order_by(Asset.id).with_for_update()))
    unused = []
    for asset in assets:
        if db.scalar(select(AssetReference.scope).where(AssetReference.asset_id == asset.id).limit(1)) is None:
            unused.append(asset.id)
            db.delete(asset)
    schedule_deletes(db, unused)


def drain_deletes():
    # Keep the tombstone until the idempotent storage delete succeeds.
    with Session.begin() as db:
        begin_write(db)
        pending = list(db.scalars(select(PendingDelete).limit(100).with_for_update(skip_locked=True)))
        if pending:
            try:
                storage = Storage()
                for entry in pending:
                    storage.delete(entry.id)
                    db.delete(entry)
            except Exception:
                log.exception("Storage deletion deferred until the next cleanup")


def collect_orphans():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=settings().orphan_grace_hours)).isoformat()
    with Session.begin() as db:
        begin_write(db)
        unused = ~select(AssetReference.asset_id).where(AssetReference.asset_id == Asset.id).exists()
        candidates = db.execute(select(Asset.owner_id, Asset.id).where(Asset.created < cutoff, unused).limit(100)).all()
        for owner_id in sorted({row.owner_id for row in candidates}):
            lock_account(db, owner_id)
            collect_unused(db, owner_id, [row.id for row in candidates if row.owner_id == owner_id])
    drain_deletes()


def reconcile_storage():
    """Recover bytes left by process failure between storage writes and SQL commit."""
    storage = Storage()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=settings().orphan_grace_hours)).timestamp()
    keys = iter(storage.old_objects(cutoff))
    while batch := list(islice(keys, 250)):
        with Session.begin() as db:
            begin_write(db)
            registered = set()
            for column in (Asset.id, ExportRecord.id, SlidePreview.storage_key, ArtifactJob.id, PendingDelete.id):
                registered.update(db.scalars(select(column).where(column.in_(batch))))
            input_key = ArtifactJob.params["input_key"].as_string()
            registered.update(db.scalars(select(input_key).where(input_key.in_(batch))))
            schedule_deletes(db, set(batch) - registered)
    drain_deletes()
