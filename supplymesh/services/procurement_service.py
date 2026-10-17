from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from supplymesh.domain.procurement import (
    ApprovalRecord,
    PurchaseOrder,
    PurchaseOrderSubmission,
    SupplierTransactionAccepted,
)
from supplymesh.edi.x12_850 import generate_850
from supplymesh.services.approval_service import approval_payload_hash, verify_approval


class PurchaseOrderGateway(Protocol):
    async def submit_purchase_order(
        self,
        supplier_id: str,
        submission: PurchaseOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted: ...


class SideEffectJournal(Protocol):
    def find_success(self, idempotency_key: str) -> dict[str, str] | None: ...

    def record_pending(self, idempotency_key: str, operation: str, payload_hash: str) -> None: ...

    def record_success(self, idempotency_key: str, result: dict[str, str]) -> None: ...


def purchase_order_idempotency_key(po: PurchaseOrder) -> str:
    return sha256(f"po:{po.workflow_id}:{po.po_number}:{po.version}".encode()).hexdigest()


def control_number(idempotency_key: str) -> str:
    return str(int(idempotency_key[:12], 16) % 1_000_000_000)


async def execute_purchase_order(
    po: PurchaseOrder,
    approval: ApprovalRecord,
    gateway: PurchaseOrderGateway,
    journal: SideEffectJournal,
    *,
    correlation_id: str,
    submission_message_id: UUID | None = None,
) -> SupplierTransactionAccepted:
    verify_approval(
        approval,
        expected_decision_type="SEND_PURCHASE_ORDER",
        target_id=str(po.po_id),
        target_version=po.version,
        current_payload=po,
    )
    key = purchase_order_idempotency_key(po)
    existing = journal.find_success(key)
    if existing is not None:
        return SupplierTransactionAccepted.model_validate(existing)
    x12 = generate_850(po, control_number(key))
    submission = PurchaseOrderSubmission(
        message_id=submission_message_id or uuid4(),
        po_number=po.po_number,
        po_version=po.version,
        x12=x12,
    )
    journal.record_pending(key, "SUBMIT_PURCHASE_ORDER", approval_payload_hash(submission))
    accepted = await gateway.submit_purchase_order(
        po.supplier_id,
        submission,
        idempotency_key=key,
        correlation_id=correlation_id,
    )
    journal.record_success(key, accepted.model_dump(mode="json"))
    return accepted
