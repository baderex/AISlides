"""Keep deleted skills available to presentations that already use them."""

import sqlalchemy as sa
from alembic import op

revision = "0004_skill_deletion"
down_revision = "0003_slide_previews"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skills", sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("skills", "deleted")
