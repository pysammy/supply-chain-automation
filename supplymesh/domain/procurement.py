from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from supplymesh.domain.models import DomainModel


class PurchaseOrderLine(DomainModel):
    line_number: int = Field(ge=1)
    component_sku: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(gt=0)
    requested_date: date
    unit: str = "EA"


class PurchaseOrder(DomainModel):
    po_id: UUID
    po_number: str
    workflow_id: UUID
    supplier_id: str
    version: int = Field(ge=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    lines: tuple[PurchaseOrderLine, ...] = Field(min_length=1)
    total: Decimal = Field(gt=0)


class ApprovalRecord(DomainModel):
    approval_id: UUID
    workflow_id: UUID
    decision_type: Literal[
        "SELECT_PLAN", "SEND_PURCHASE_ORDER", "ACCEPT_EXCEPTION", "SEND_COUNTERPROPOSAL"
    ]
    target_type: str
    target_id: str
    target_version: int = Field(ge=1)
    decision: Literal["APPROVED", "REJECTED"]
    actor: str
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decided_at: datetime
    reason: str | None = None


class PurchaseOrderSubmission(DomainModel):
    message_id: UUID
    document_type: Literal["850"] = "850"
    po_number: str
    po_version: int = Field(ge=1)
    x12: str


class SupplierTransactionAccepted(DomainModel):
    supplier_transaction_id: str
    status: Literal["PROCESSING"] = "PROCESSING"
    poll_url: str
    estimated_ready_at: datetime


class SupplierTransactionStatus(DomainModel):
    status: Literal["PROCESSING", "ACKNOWLEDGED"]
    retry_after_seconds: int | None = Field(default=None, ge=0)
    document_type: Literal["855"] | None = None
    x12: str | None = None


class ChangeOrderSubmission(DomainModel):
    message_id: UUID
    document_type: Literal["860"] = "860"
    po_number: str
    po_version: int = Field(ge=2)
    x12: str


class ChangeTransactionStatus(DomainModel):
    status: Literal["PROCESSING", "ACKNOWLEDGED"]
    retry_after_seconds: int | None = Field(default=None, ge=0)
    document_type: Literal["865"] | None = None
    x12: str | None = None


class CounterproposalLine(DomainModel):
    line_number: int = Field(ge=1)
    component_sku: str
    quantity: Decimal = Field(gt=0)
    unit: str
    maximum_unit_price: Decimal = Field(gt=0)
    requested_date: date


class Counterproposal(DomainModel):
    workflow_id: UUID
    po_id: UUID
    po_number: str
    current_po_version: int = Field(ge=1)
    change_version: int = Field(ge=2)
    supplier_id: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    lines: tuple[CounterproposalLine, ...] = Field(min_length=1)
