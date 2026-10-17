"""procurement evidence and restart recovery metadata

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("supplier_transactions", sa.Column("workflow_id", postgresql.UUID(as_uuid=True)))
    op.add_column("supplier_transactions", sa.Column("po_id", postgresql.UUID(as_uuid=True)))
    op.add_column("supplier_transactions", sa.Column("po_version", sa.Integer()))
    op.add_column(
        "supplier_transactions", sa.Column("correlation_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column(
        "supplier_transactions", sa.Column("request_message_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column(
        "supplier_transactions", sa.Column("response_message_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column("supplier_transactions", sa.Column("approval_id", postgresql.UUID(as_uuid=True)))
    op.add_column("supplier_transactions", sa.Column("reply_address", sa.String(255)))
    op.create_foreign_key(
        "fk_supplier_transactions_po_id",
        "supplier_transactions",
        "purchase_orders",
        ["po_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_supplier_transactions_workflow_id", "supplier_transactions", ["workflow_id"]
    )
    op.create_index("ix_supplier_transactions_po_id", "supplier_transactions", ["po_id"])

    op.create_table(
        "acknowledgments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "po_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("po_version", sa.Integer(), nullable=False),
        sa.Column("supplier_ack_ref", sa.String(100), nullable=False),
        sa.Column("acknowledgment_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("po_id", "po_version", "supplier_ack_ref"),
    )
    op.create_index("ix_acknowledgments_po_id", "acknowledgments", ["po_id"])
    op.create_table(
        "acknowledgment_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "acknowledgment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("acknowledgments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("po_line_number", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.String(16), nullable=False),
        sa.Column("accepted_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("promised_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.String(1000)),
        sa.UniqueConstraint("acknowledgment_id", "po_line_number"),
    )
    op.create_index(
        "ix_acknowledgment_lines_acknowledgment_id",
        "acknowledgment_lines",
        ["acknowledgment_id"],
    )
    op.create_table(
        "exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "po_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "acknowledgment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("acknowledgments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("expected_value", sa.String(1000)),
        sa.Column("actual_value", sa.String(1000)),
        sa.Column("cost_impact", sa.Numeric(18, 4), nullable=False),
        sa.Column("schedule_impact_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_exceptions_workflow_id", "exceptions", ["workflow_id"])
    op.create_index("ix_exceptions_po_id", "exceptions", ["po_id"])
    op.create_index("ix_exceptions_acknowledgment_id", "exceptions", ["acknowledgment_id"])
    op.create_table(
        "change_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "po_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("po_id", "version"),
    )
    op.create_index("ix_change_orders_po_id", "change_orders", ["po_id"])
    op.create_index("ix_change_orders_workflow_id", "change_orders", ["workflow_id"])


def downgrade() -> None:
    op.drop_table("change_orders")
    op.drop_table("exceptions")
    op.drop_table("acknowledgment_lines")
    op.drop_table("acknowledgments")
    op.drop_index("ix_supplier_transactions_po_id", table_name="supplier_transactions")
    op.drop_index("ix_supplier_transactions_workflow_id", table_name="supplier_transactions")
    op.drop_constraint(
        "fk_supplier_transactions_po_id", "supplier_transactions", type_="foreignkey"
    )
    for column in (
        "reply_address",
        "approval_id",
        "response_message_id",
        "request_message_id",
        "correlation_id",
        "po_version",
        "po_id",
        "workflow_id",
    ):
        op.drop_column("supplier_transactions", column)
