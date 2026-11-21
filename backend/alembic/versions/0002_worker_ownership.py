"""Record which worker owns an in-flight checkpoint."""

import sqlalchemy as sa
from alembic import op

revision = "0002_worker_ownership"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("lease_owner", sa.String(length=36), nullable=True))


def downgrade():
    with op.batch_alter_table("jobs") as table:
        table.drop_column("lease_owner")
