"""product BOM demand and inventory domain

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku", "version"),
    )
    op.create_index("ix_products_sku", "products", ["sku"])
    op.create_table(
        "boms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "version"),
    )
    op.create_table(
        "bom_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bom_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_sku", sa.String(100), nullable=False),
        sa.Column("quantity_per", sa.Numeric(18, 6), nullable=False),
        sa.Column("scrap_factor", sa.Numeric(9, 6), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(["bom_id"], ["boms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bom_id", "component_sku"),
    )
    op.create_index("ix_bom_items_component_sku", "bom_items", ["component_sku"])
    op.create_table(
        "demand_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_ref", sa.String(100), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("required_by", sa.Date(), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_ref"),
    )
    op.create_table(
        "inventory_balances",
        sa.Column("component_sku", sa.String(100), nullable=False),
        sa.Column("location", sa.String(100), nullable=False),
        sa.Column("on_hand", sa.Numeric(18, 6), nullable=False),
        sa.Column("safety_stock", sa.Numeric(18, 6), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("component_sku", "location"),
    )
    op.create_table(
        "inventory_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("demand_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_sku", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["demand_order_id"], ["demand_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_reservations_component_sku", "inventory_reservations", ["component_sku"]
    )
    op.create_table(
        "inbound_supply",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_sku", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("expected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("source_po_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inbound_supply_component_sku", "inbound_supply", ["component_sku"])


def downgrade() -> None:
    op.drop_table("inbound_supply")
    op.drop_table("inventory_reservations")
    op.drop_table("inventory_balances")
    op.drop_table("demand_orders")
    op.drop_table("bom_items")
    op.drop_table("boms")
    op.drop_table("products")
