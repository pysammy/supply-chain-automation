"""Persist supplier-side purchase and change order history.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_order_history",
        sa.Column("transaction_id", sa.String(100), primary_key=True),
        sa.Column("manufacturer_id", sa.String(100), nullable=False),
        sa.Column("manufacturer_name", sa.String(255), nullable=False),
        sa.Column("supplier_id", sa.String(100), nullable=False),
        sa.Column("document_type", sa.String(3), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("po_number", sa.String(100), nullable=False),
        sa.Column("po_version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total", sa.Numeric(20, 4), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("x12", sa.Text(), nullable=False),
        sa.Column("accepted_response", sa.JSON(), nullable=False),
        sa.Column("status_response", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "manufacturer_id",
            "supplier_id",
            "document_type",
            "idempotency_key",
            name="uq_supplier_history_request",
        ),
        sa.UniqueConstraint(
            "manufacturer_id",
            "supplier_id",
            "po_number",
            "po_version",
            name="uq_supplier_history_po_version",
        ),
    )
    op.create_index(
        "ix_supplier_order_history_manufacturer_id", "supplier_order_history", ["manufacturer_id"]
    )
    op.create_index(
        "ix_supplier_order_history_supplier_id", "supplier_order_history", ["supplier_id"]
    )


def downgrade() -> None:
    op.drop_table("supplier_order_history")
