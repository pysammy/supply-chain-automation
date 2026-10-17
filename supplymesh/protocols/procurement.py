from typing import Literal

from supplymesh.domain.procurement import Counterproposal, PurchaseOrder
from supplymesh.protocols.common import MessageEnvelope


class PlanApprovalDecision(MessageEnvelope):
    message_type: Literal["PlanApprovalDecision"] = "PlanApprovalDecision"
    plan_id: str
    plan_version: int
    decision: Literal["APPROVED", "REJECTED", "MODIFY"]
    actor: str
    payload_hash: str


class PurchaseOrderExecutionRequest(MessageEnvelope):
    message_type: Literal["PurchaseOrderExecutionRequest"] = "PurchaseOrderExecutionRequest"
    purchase_orders: tuple[PurchaseOrder, ...]
    approval_id: str
    approval_ids: tuple[str, ...] = ()


class PurchaseOrderSubmitted(MessageEnvelope):
    message_type: Literal["PurchaseOrderSubmitted"] = "PurchaseOrderSubmitted"
    po_id: str
    supplier_transaction_id: str
    poll_url: str


class AcknowledgmentReceived(MessageEnvelope):
    message_type: Literal["AcknowledgmentReceived"] = "AcknowledgmentReceived"
    po_id: str
    po_version: int
    document_type: Literal["855"] = "855"
    raw_x12: str


class CounterproposalExecutionRequest(MessageEnvelope):
    message_type: Literal["CounterproposalExecutionRequest"] = "CounterproposalExecutionRequest"
    counterproposal: Counterproposal
    approval_id: str


class ChangeOrderSubmitted(MessageEnvelope):
    message_type: Literal["ChangeOrderSubmitted"] = "ChangeOrderSubmitted"
    po_id: str
    change_version: int
    supplier_transaction_id: str


class ChangeResponseReceived(MessageEnvelope):
    message_type: Literal["ChangeResponseReceived"] = "ChangeResponseReceived"
    po_id: str
    po_version: int
    document_type: Literal["865"] = "865"
    raw_x12: str
