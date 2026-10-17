from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from supplymesh.domain.procurement import (
    ApprovalRecord,
    ChangeOrderSubmission,
    Counterproposal,
    SupplierTransactionAccepted,
)
from supplymesh.edi.x12_860 import ChangeRequestLine, generate_860
from supplymesh.services.approval_service import approval_payload_hash, verify_approval
from supplymesh.services.procurement_service import SideEffectJournal, control_number


class ChangeOrderGateway(Protocol):
    async def submit_change_order(
        self,
        supplier_id: str,
        submission: ChangeOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted: ...


def counterproposal_idempotency_key(counter: Counterproposal) -> str:
    material = f"change:{counter.workflow_id}:{counter.po_number}:{counter.change_version}"
    return sha256(material.encode()).hexdigest()


async def execute_counterproposal(
    counter: Counterproposal,
    approval: ApprovalRecord,
    gateway: ChangeOrderGateway,
    journal: SideEffectJournal,
    *,
    correlation_id: str,
    submission_message_id: UUID | None = None,
) -> SupplierTransactionAccepted:
    verify_approval(
        approval,
        expected_decision_type="SEND_COUNTERPROPOSAL",
        target_id=str(counter.po_id),
        target_version=counter.current_po_version,
        current_payload=counter,
    )
    key = counterproposal_idempotency_key(counter)
    existing = journal.find_success(key)
    if existing is not None:
        return SupplierTransactionAccepted.model_validate(existing)
    x12 = generate_860(
        po_number=counter.po_number,
        po_version=counter.change_version,
        currency=counter.currency,
        lines=tuple(
            ChangeRequestLine(
                line_number=line.line_number,
                component_sku=line.component_sku,
                quantity=line.quantity,
                unit=line.unit,
                maximum_unit_price=line.maximum_unit_price,
                requested_date=line.requested_date,
            )
            for line in counter.lines
        ),
        control_number=control_number(key),
    )
    submission = ChangeOrderSubmission(
        message_id=submission_message_id or uuid4(),
        po_number=counter.po_number,
        po_version=counter.change_version,
        x12=x12,
    )
    journal.record_pending(key, "SUBMIT_CHANGE_ORDER", approval_payload_hash(submission))
    accepted = await gateway.submit_change_order(
        counter.supplier_id,
        submission,
        idempotency_key=key,
        correlation_id=correlation_id,
    )
    journal.record_success(key, accepted.model_dump(mode="json"))
    return accepted
