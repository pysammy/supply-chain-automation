"""Persist confirmed supplier cancellation without deleting original order evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_order_history", sa.Column("cancellation_response", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("supplier_order_history", "cancellation_response")
