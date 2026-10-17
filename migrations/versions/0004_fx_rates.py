"""persistent foreign exchange cache

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("base_currency", sa.String(3), primary_key=True),
        sa.Column("quote_currency", sa.String(3), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fx_rates_expires_at", "fx_rates", ["expires_at"])


def downgrade() -> None:
    op.drop_table("fx_rates")
