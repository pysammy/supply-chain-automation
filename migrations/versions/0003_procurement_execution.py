"""approval procurement EDI and side effect persistence

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_type", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approval_decisions_workflow_id", "approval_decisions", ["workflow_id"])
    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("po_number", sa.String(100), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total", sa.Numeric(18, 2), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("po_number", "version"),
        sa.UniqueConstraint("supplier_id", "idempotency_key"),
    )
    op.create_index("ix_purchase_orders_workflow_id", "purchase_orders", ["workflow_id"])
    op.create_table(
        "purchase_order_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("component_sku", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("requested_date", sa.Date(), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(["po_id"], ["purchase_orders.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("po_id", "line_number"),
    )
    op.create_table(
        "edi_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", sa.String(100), nullable=False),
        sa.Column("document_type", sa.String(3), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("control_number", sa.String(20), nullable=False),
        sa.Column("related_document_id", postgresql.UUID(as_uuid=True)),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("parsed_json", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("supplier_id", "control_number", "document_type"),
    )
    op.create_index("ix_edi_documents_workflow_id", "edi_documents", ["workflow_id"])
    op.create_table(
        "supplier_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("supplier_id", sa.String(100), nullable=False),
        sa.Column("transaction_type", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("external_transaction_id", sa.String(255)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("poll_url", sa.String(1000)),
        sa.Column("next_poll_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("supplier_id", "idempotency_key"),
        sa.UniqueConstraint("external_transaction_id"),
    )
    op.create_table(
        "external_side_effects",
        sa.Column("idempotency_key", sa.String(64), primary_key=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_json", sa.JSON()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("external_side_effects")
    op.drop_table("supplier_transactions")
    op.drop_table("edi_documents")
    op.drop_table("purchase_order_lines")
    op.drop_table("purchase_orders")
    op.drop_table("approval_decisions")
