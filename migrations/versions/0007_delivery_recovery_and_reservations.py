"""delivery recovery leases and reservation lifecycle metadata

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("supplier_transactions", sa.Column("lease_owner", sa.String(100)))
    op.add_column(
        "supplier_transactions", sa.Column("lease_expires_at", sa.DateTime(timezone=True))
    )
    op.create_unique_constraint(
        "uq_supplier_transactions_response_message_id",
        "supplier_transactions",
        ["response_message_id"],
    )
    op.create_index(
        "ix_supplier_transactions_lease_owner", "supplier_transactions", ["lease_owner"]
    )
    op.create_index(
        "ix_supplier_transactions_lease_expires_at",
        "supplier_transactions",
        ["lease_expires_at"],
    )
    op.create_index("ix_inventory_reservations_status", "inventory_reservations", ["status"])
    op.create_unique_constraint(
        "uq_inventory_reservations_demand_component",
        "inventory_reservations",
        ["demand_order_id", "component_sku"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_inventory_reservations_demand_component",
        "inventory_reservations",
        type_="unique",
    )
    op.drop_index("ix_inventory_reservations_status", table_name="inventory_reservations")
    op.drop_index("ix_supplier_transactions_lease_expires_at", table_name="supplier_transactions")
    op.drop_index("ix_supplier_transactions_lease_owner", table_name="supplier_transactions")
    op.drop_constraint(
        "uq_supplier_transactions_response_message_id",
        "supplier_transactions",
        type_="unique",
    )
    op.drop_column("supplier_transactions", "lease_expires_at")
    op.drop_column("supplier_transactions", "lease_owner")
