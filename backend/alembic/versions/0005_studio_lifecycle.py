"""Track assets, shared limits, and worker artifacts."""

from alembic import op
import sqlalchemy as sa

revision = "0005_studio_lifecycle"
down_revision = "0004_skill_deletion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pending_deletes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('usage_buckets',
    sa.Column('key', sa.String(length=150), nullable=False),
    sa.Column('window', sa.Integer(), nullable=False),
    sa.Column('used', sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint('key', 'window')
    )
    op.create_table('ai_slots',
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('expires', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_slots_owner_id'), 'ai_slots', ['owner_id'], unique=False)
    op.create_table('artifact_jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('presentation_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('cache_key', sa.String(length=64), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('lease', sa.String(length=36), nullable=True),
    sa.Column('lease_owner', sa.String(length=36), nullable=True),
    sa.Column('lease_until', sa.Integer(), nullable=False),
    sa.Column('created', sa.String(length=40), nullable=False),
    sa.Column('updated', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['presentation_id'], ['presentations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_artifact_jobs_cache_key'), 'artifact_jobs', ['cache_key'], unique=False)
    op.create_index(op.f('ix_artifact_jobs_owner_id'), 'artifact_jobs', ['owner_id'], unique=False)
    op.create_index(op.f('ix_artifact_jobs_presentation_id'), 'artifact_jobs', ['presentation_id'], unique=False)
    op.create_index(op.f('ix_artifact_jobs_state'), 'artifact_jobs', ['state'], unique=False)
    op.create_table('asset_references',
    sa.Column('scope', sa.String(length=100), nullable=False),
    sa.Column('asset_id', sa.String(length=36), nullable=False),
    sa.Column('presentation_id', sa.String(length=36), nullable=True),
    sa.Column('skill_id', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
    sa.ForeignKeyConstraint(['presentation_id'], ['presentations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('scope', 'asset_id')
    )
    op.create_index(op.f('ix_asset_references_asset_id'), 'asset_references', ['asset_id'], unique=False)
    op.create_index(op.f('ix_asset_references_presentation_id'), 'asset_references', ['presentation_id'], unique=False)
    op.create_index(op.f('ix_asset_references_skill_id'), 'asset_references', ['skill_id'], unique=False)
    if op.get_bind().dialect.name == "sqlite":
        # Keep SSE cursor IDs increasing after retention deletes the last event.
        with op.batch_alter_table("events", recreate="always", table_kwargs={"sqlite_autoincrement": True}):
            pass
    op.create_index('ix_events_presentation_cursor', 'events', ['presentation_id', 'id'], unique=False)
    op.add_column('exports', sa.Column('title', sa.String(length=200), nullable=True))
    op.create_index('ix_presentations_owner_updated', 'presentations', ['owner_id', 'updated', 'id'], unique=False)

    backfill_references()


def downgrade():
    op.drop_index('ix_presentations_owner_updated', table_name='presentations')
    op.drop_column('exports', 'title')
    op.drop_index('ix_events_presentation_cursor', table_name='events')
    op.drop_index(op.f('ix_asset_references_skill_id'), table_name='asset_references')
    op.drop_index(op.f('ix_asset_references_presentation_id'), table_name='asset_references')
    op.drop_index(op.f('ix_asset_references_asset_id'), table_name='asset_references')
    op.drop_table('asset_references')
    op.drop_index(op.f('ix_artifact_jobs_state'), table_name='artifact_jobs')
    op.drop_index(op.f('ix_artifact_jobs_presentation_id'), table_name='artifact_jobs')
    op.drop_index(op.f('ix_artifact_jobs_owner_id'), table_name='artifact_jobs')
    op.drop_index(op.f('ix_artifact_jobs_cache_key'), table_name='artifact_jobs')
    op.drop_table('artifact_jobs')
    op.drop_index(op.f('ix_ai_slots_owner_id'), table_name='ai_slots')
    op.drop_table('ai_slots')
    op.drop_table('usage_buckets')
    op.drop_table('pending_deletes')


def backfill_references():
    db = op.get_bind()
    schema = sa.MetaData()
    schema.reflect(db, only=["presentations", "versions", "jobs", "skills", "assets", "asset_references"])
    decks, versions, jobs, skills, assets, refs = (schema.tables[name] for name in
        ("presentations", "versions", "jobs", "skills", "assets", "asset_references"))

    def ids(value):
        if isinstance(value, list):
            for child in value:
                yield from ids(child)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"asset_id", "image_asset_id", "logo_asset_id"} and isinstance(child, str):
                    yield child
                elif key in {"asset_ids", "generated_images"} and isinstance(child, list):
                    yield from (item for item in child if isinstance(item, str))
                else:
                    yield from ids(child)

    def link(scope, owner, value, presentation_id=None, skill_id=None):
        candidates = set(ids(value))
        if candidates:
            owned = db.scalars(sa.select(assets.c.id).where(assets.c.owner_id == owner, assets.c.id.in_(candidates)))
            rows = [dict(scope=scope, asset_id=key, presentation_id=presentation_id, skill_id=skill_id) for key in owned]
            if rows:
                db.execute(refs.insert(), rows)

    used_skills = set()
    for row in db.execute(sa.select(decks)).mappings():
        link("deck:" + row["id"], row["owner_id"], [row["request"], row["document"]], presentation_id=row["id"])
        used_skills.add(row["request"].get("skill_id"))
    for table, field, kind in [(versions, "document", "version"), (jobs, "context", "job")]:
        query = sa.select(table, decks.c.owner_id).join(decks, decks.c.id == table.c.presentation_id)
        for row in db.execute(query).mappings():
            pid = row["presentation_id"]
            scope = f"version:{pid}:{row['revision']}" if kind == "version" else "job:" + row["id"]
            link(scope, row["owner_id"], row[field], presentation_id=pid)
    for row in db.execute(sa.select(skills)).mappings():
        if not row["deleted"] or row["id"] in used_skills:
            link("skill:" + row["id"], row["owner_id"], row["manifest"], skill_id=row["id"])
