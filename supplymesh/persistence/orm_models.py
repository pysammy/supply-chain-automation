import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from supplymesh.persistence.database import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[str] = mapped_column(String(255), index=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(64), index=True)
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("sku", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    unit: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Bom(Base):
    __tablename__ = "boms"
    __table_args__ = (UniqueConstraint("product_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))


class BomItem(Base):
    __tablename__ = "bom_items"
    __table_args__ = (UniqueConstraint("bom_id", "component_sku"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    bom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boms.id", ondelete="CASCADE"))
    component_sku: Mapped[str] = mapped_column(String(100), index=True)
    quantity_per: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    scrap_factor: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=Decimal("0"))
    unit: Mapped[str] = mapped_column(String(20))


class DemandOrder(Base):
    __tablename__ = "demand_orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    external_ref: Mapped[str] = mapped_column(String(100), unique=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    required_by: Mapped[date] = mapped_column(Date)
    priority: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32))


class InventoryBalance(Base):
    __tablename__ = "inventory_balances"

    component_sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    location: Mapped[str] = mapped_column(String(100), primary_key=True)
    on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    safety_stock: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    version: Mapped[int] = mapped_column(Integer, default=1)


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (UniqueConstraint("demand_order_id", "component_sku"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    demand_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("demand_orders.id"))
    component_sku: Mapped[str] = mapped_column(String(100), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    priority: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32), index=True)


class InboundSupply(Base):
    __tablename__ = "inbound_supply"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    component_sku: Mapped[str] = mapped_column(String(100), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    expected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    source_po_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(String(32))


class ApprovalDecisionRecord(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(index=True)
    decision_type: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(255))
    target_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(String(1000))
    payload_hash: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PurchaseOrderRecord(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("po_number", "version"),
        UniqueConstraint("supplier_id", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    po_number: Mapped[str] = mapped_column(String(100))
    workflow_id: Mapped[uuid.UUID] = mapped_column(index=True)
    supplier_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PurchaseOrderLineRecord(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (UniqueConstraint("po_id", "line_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    line_number: Mapped[int] = mapped_column(Integer)
    component_sku: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    requested_date: Mapped[date] = mapped_column(Date)
    unit: Mapped[str] = mapped_column(String(20))


class EdiDocumentRecord(Base):
    __tablename__ = "edi_documents"
    __table_args__ = (UniqueConstraint("supplier_id", "control_number", "document_type"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(index=True)
    supplier_id: Mapped[str] = mapped_column(String(100))
    document_type: Mapped[str] = mapped_column(String(3))
    direction: Mapped[str] = mapped_column(String(16))
    control_number: Mapped[str] = mapped_column(String(20))
    related_document_id: Mapped[uuid.UUID | None]
    raw_text: Mapped[str] = mapped_column(Text)
    parsed_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupplierTransactionRecord(Base):
    __tablename__ = "supplier_transactions"
    __table_args__ = (
        UniqueConstraint("supplier_id", "idempotency_key"),
        UniqueConstraint("external_transaction_id"),
        UniqueConstraint(
            "response_message_id", name="uq_supplier_transactions_response_message_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    po_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    po_version: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[uuid.UUID | None]
    request_message_id: Mapped[uuid.UUID | None]
    response_message_id: Mapped[uuid.UUID | None]
    approval_id: Mapped[uuid.UUID | None]
    reply_address: Mapped[str | None] = mapped_column(String(255))
    lease_owner: Mapped[str | None] = mapped_column(String(100), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    supplier_id: Mapped[str] = mapped_column(String(100))
    transaction_type: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    external_transaction_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    poll_url: Mapped[str | None] = mapped_column(String(1000))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AcknowledgmentRecord(Base):
    __tablename__ = "acknowledgments"
    __table_args__ = (UniqueConstraint("po_id", "po_version", "supplier_ack_ref"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    po_version: Mapped[int] = mapped_column(Integer)
    supplier_ack_ref: Mapped[str] = mapped_column(String(100))
    acknowledgment_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AcknowledgmentLineRecord(Base):
    __tablename__ = "acknowledgment_lines"
    __table_args__ = (UniqueConstraint("acknowledgment_id", "po_line_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    acknowledgment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acknowledgments.id", ondelete="CASCADE"), index=True
    )
    po_line_number: Mapped[int] = mapped_column(Integer)
    status_code: Mapped[str] = mapped_column(String(16))
    accepted_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    promised_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(1000))


class ExceptionRecord(Base):
    __tablename__ = "exceptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(index=True)
    po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    acknowledgment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acknowledgments.id", ondelete="CASCADE"), index=True
    )
    severity: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(64))
    expected_value: Mapped[str | None] = mapped_column(String(1000))
    actual_value: Mapped[str | None] = mapped_column(String(1000))
    cost_impact: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    schedule_impact_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeOrderRecord(Base):
    __tablename__ = "change_orders"
    __table_args__ = (UniqueConstraint("po_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    approval_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExternalSideEffectRecord(Base):
    __tablename__ = "external_side_effects"

    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FxRateRecord(Base):
    __tablename__ = "fx_rates"

    base_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    quote_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    provider: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AgentMessageLogRecord(Base):
    __tablename__ = "agent_message_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    sender: Mapped[str] = mapped_column(String(100))
    recipient: Mapped[str] = mapped_column(String(100))
    message_type: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(index=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(255))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
