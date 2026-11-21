"""Persist generated slide previews in the configured object store."""

import sqlalchemy as sa
from alembic import op

revision = "0003_slide_previews"
down_revision = "0002_worker_ownership"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "slide_previews",
        sa.Column("presentation_id", sa.String(length=36), sa.ForeignKey("presentations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("slide_id", sa.String(length=70), primary_key=True),
        sa.Column("storage_key", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created", sa.String(length=40), nullable=False),
    )


def downgrade():
    op.drop_table("slide_previews")
