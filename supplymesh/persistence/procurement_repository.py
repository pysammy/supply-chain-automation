from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from supplymesh.domain.exceptions import AcknowledgmentComparison
from supplymesh.domain.procurement import (
    ApprovalRecord,
    Counterproposal,
    PurchaseOrder,
    PurchaseOrderLine,
)
from supplymesh.edi.common import parse_segments
from supplymesh.edi.x12_850 import parse_850
from supplymesh.edi.x12_855 import Parsed855, parse_855
from supplymesh.edi.x12_860 import parse_860
from supplymesh.edi.x12_865 import parse_865
from supplymesh.persistence.orm_models import (
    AcknowledgmentLineRecord,
    AcknowledgmentRecord,
    ApprovalDecisionRecord,
    ChangeOrderRecord,
    EdiDocumentRecord,
    ExceptionRecord,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    SupplierTransactionRecord,
)
from supplymesh.services.counterproposal_service import counterproposal_idempotency_key
from supplymesh.services.procurement_service import purchase_order_idempotency_key


@dataclass(frozen=True)
class PendingSupplierTransaction:
    transaction_type: Literal["PURCHASE_ORDER", "CHANGE_ORDER"]
    status: Literal["SUBMITTING", "PROCESSING", "READY_TO_DELIVER"]
    supplier_id: str
    idempotency_key: str
    workflow_id: UUID
    correlation_id: UUID
    request_message_id: UUID
    response_message_id: UUID
    approval_id: UUID
    reply_address: str
    external_transaction_id: str | None
    poll_url: str | None
    response_x12: str | None
    lease_owner: str | None
    purchase_order: PurchaseOrder | None = None
    counterproposal: Counterproposal | None = None


class ProcurementRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self.session_factory() as session:
            row = session.get(ApprovalDecisionRecord, UUID(approval_id))
            if row is None:
                raise ValueError("approval record was not found")
            return ApprovalRecord(
                approval_id=row.id,
                workflow_id=row.workflow_id,
                decision_type=row.decision_type,
                target_type=row.target_type,
                target_id=row.target_id,
                target_version=row.target_version,
                decision=row.decision,
                actor=row.actor,
                reason=row.reason,
                payload_hash=row.payload_hash,
                decided_at=row.decided_at,
            )

    def save_approved_purchase_order(self, po: PurchaseOrder) -> None:
        key = purchase_order_idempotency_key(po)
        with self.session_factory() as session, session.begin():
            existing = session.get(PurchaseOrderRecord, po.po_id)
            if existing is not None:
                if self._to_domain(session, existing) != po:
                    raise ValueError("purchase order ID already exists with different content")
                return
            session.add(
                PurchaseOrderRecord(
                    id=po.po_id,
                    po_number=po.po_number,
                    workflow_id=po.workflow_id,
                    supplier_id=po.supplier_id,
                    version=po.version,
                    currency=po.currency,
                    status="APPROVED",
                    total=po.total,
                    idempotency_key=key,
                )
            )
            session.flush()
            session.add_all(
                PurchaseOrderLineRecord(
                    po_id=po.po_id,
                    line_number=line.line_number,
                    component_sku=line.component_sku,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    requested_date=line.requested_date,
                    unit=line.unit,
                )
                for line in po.lines
            )

    def record_submission(
        self,
        po: PurchaseOrder,
        *,
        external_transaction_id: str,
        poll_url: str,
    ) -> None:
        key = purchase_order_idempotency_key(po)
        with self.session_factory() as session, session.begin():
            record = session.get(PurchaseOrderRecord, po.po_id)
            if record is None:
                raise ValueError("purchase order must be persisted before supplier submission")
            record.status = "SUBMITTED"
            transaction = session.scalar(
                select(SupplierTransactionRecord).where(
                    SupplierTransactionRecord.supplier_id == po.supplier_id,
                    SupplierTransactionRecord.idempotency_key == key,
                )
            )
            if transaction is None:
                session.add(
                    SupplierTransactionRecord(
                        workflow_id=po.workflow_id,
                        po_id=po.po_id,
                        po_version=po.version,
                        supplier_id=po.supplier_id,
                        transaction_type="PURCHASE_ORDER",
                        idempotency_key=key,
                        external_transaction_id=external_transaction_id,
                        status="PROCESSING",
                        poll_url=poll_url,
                        attempts=1,
                    )
                )
            elif transaction.external_transaction_id != external_transaction_id:
                if transaction.external_transaction_id is not None:
                    raise ValueError("idempotent PO submission returned a different transaction")
                transaction.external_transaction_id = external_transaction_id
                transaction.poll_url = poll_url
                transaction.status = "PROCESSING"
                transaction.attempts += 1

    def record_purchase_order_intent(
        self,
        po: PurchaseOrder,
        *,
        correlation_id: UUID,
        request_message_id: UUID,
        response_message_id: UUID,
        approval_id: UUID,
        reply_address: str,
    ) -> None:
        self._record_intent(
            workflow_id=po.workflow_id,
            po_id=po.po_id,
            po_version=po.version,
            supplier_id=po.supplier_id,
            transaction_type="PURCHASE_ORDER",
            idempotency_key=purchase_order_idempotency_key(po),
            correlation_id=correlation_id,
            request_message_id=request_message_id,
            response_message_id=response_message_id,
            approval_id=approval_id,
            reply_address=reply_address,
        )

    def record_change_order_intent(
        self,
        counterproposal: Counterproposal,
        *,
        correlation_id: UUID,
        request_message_id: UUID,
        response_message_id: UUID,
        approval_id: UUID,
        reply_address: str,
    ) -> None:
        self._record_intent(
            workflow_id=counterproposal.workflow_id,
            po_id=counterproposal.po_id,
            po_version=counterproposal.change_version,
            supplier_id=counterproposal.supplier_id,
            transaction_type="CHANGE_ORDER",
            idempotency_key=counterproposal_idempotency_key(counterproposal),
            correlation_id=correlation_id,
            request_message_id=request_message_id,
            response_message_id=response_message_id,
            approval_id=approval_id,
            reply_address=reply_address,
        )

    def record_change_submission(
        self,
        counterproposal: Counterproposal,
        *,
        external_transaction_id: str,
        poll_url: str,
    ) -> None:
        key = counterproposal_idempotency_key(counterproposal)
        with self.session_factory() as session, session.begin():
            transaction = session.scalar(
                select(SupplierTransactionRecord).where(
                    SupplierTransactionRecord.supplier_id == counterproposal.supplier_id,
                    SupplierTransactionRecord.idempotency_key == key,
                )
            )
            if transaction is None:
                raise ValueError("change-order intent must be persisted before supplier submission")
            if (
                transaction.external_transaction_id is not None
                and transaction.external_transaction_id != external_transaction_id
            ):
                raise ValueError("idempotent change submission returned a different transaction")
            transaction.external_transaction_id = external_transaction_id
            transaction.poll_url = poll_url
            transaction.status = "PROCESSING"
            transaction.attempts += 1

    def mark_transaction_ready(self, supplier_id: str, idempotency_key: str) -> None:
        with self.session_factory() as session, session.begin():
            transaction = session.scalar(
                select(SupplierTransactionRecord).where(
                    SupplierTransactionRecord.supplier_id == supplier_id,
                    SupplierTransactionRecord.idempotency_key == idempotency_key,
                )
            )
            if transaction is None:
                raise ValueError("supplier transaction was not found")
            transaction.status = "READY_TO_DELIVER"

    def mark_response_delivered(self, response_message_id: UUID) -> None:
        with self.session_factory() as session, session.begin():
            transaction = session.scalar(
                select(SupplierTransactionRecord).where(
                    SupplierTransactionRecord.response_message_id == response_message_id
                )
            )
            if transaction is None:
                raise ValueError("supplier response transaction was not found")
            transaction.status = "DELIVERED"
            transaction.lease_owner = None
            transaction.lease_expires_at = None

    def claim_pending_transactions(
        self, owner: str, *, lease_seconds: float = 30
    ) -> tuple[PendingSupplierTransaction, ...]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        with self.session_factory() as session, session.begin():
            candidate_ids = tuple(
                session.scalars(
                    select(SupplierTransactionRecord.id)
                    .where(
                        SupplierTransactionRecord.status.in_(
                            ("SUBMITTING", "PROCESSING", "READY_TO_DELIVER")
                        ),
                        or_(
                            SupplierTransactionRecord.lease_owner == owner,
                            SupplierTransactionRecord.lease_owner.is_(None),
                            SupplierTransactionRecord.lease_expires_at < now,
                        ),
                    )
                    .order_by(SupplierTransactionRecord.created_at)
                )
            )
            for transaction_id in candidate_ids:
                session.execute(
                    update(SupplierTransactionRecord)
                    .where(
                        SupplierTransactionRecord.id == transaction_id,
                        or_(
                            SupplierTransactionRecord.lease_owner == owner,
                            SupplierTransactionRecord.lease_owner.is_(None),
                            SupplierTransactionRecord.lease_expires_at < now,
                        ),
                    )
                    .values(lease_owner=owner, lease_expires_at=expires_at)
                )
        return tuple(
            pending for pending in self.pending_transactions() if pending.lease_owner == owner
        )

    def release_claim(self, response_message_id: UUID, owner: str) -> None:
        with self.session_factory() as session, session.begin():
            session.execute(
                update(SupplierTransactionRecord)
                .where(
                    SupplierTransactionRecord.response_message_id == response_message_id,
                    SupplierTransactionRecord.lease_owner == owner,
                )
                .values(lease_owner=None, lease_expires_at=None)
            )

    def pending_transactions(self) -> tuple[PendingSupplierTransaction, ...]:
        with self.session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(SupplierTransactionRecord)
                    .where(
                        SupplierTransactionRecord.status.in_(
                            ("SUBMITTING", "PROCESSING", "READY_TO_DELIVER")
                        )
                    )
                    .order_by(SupplierTransactionRecord.created_at, SupplierTransactionRecord.id)
                )
            )
            pending: list[PendingSupplierTransaction] = []
            for row in rows:
                workflow_id = row.workflow_id
                po_id = row.po_id
                po_version = row.po_version
                correlation_id = row.correlation_id
                request_message_id = row.request_message_id
                response_message_id = row.response_message_id
                approval_id = row.approval_id
                reply_address = row.reply_address
                if (
                    workflow_id is None
                    or po_id is None
                    or po_version is None
                    or correlation_id is None
                    or request_message_id is None
                    or response_message_id is None
                    or approval_id is None
                    or reply_address is None
                ):
                    continue
                po: PurchaseOrder | None = None
                counter: Counterproposal | None = None
                if row.transaction_type == "PURCHASE_ORDER":
                    transaction_type: Literal["PURCHASE_ORDER", "CHANGE_ORDER"] = "PURCHASE_ORDER"
                    po_record = session.get(PurchaseOrderRecord, po_id)
                    if po_record is None:
                        continue
                    po = self._to_domain(session, po_record)
                elif row.transaction_type == "CHANGE_ORDER":
                    transaction_type = "CHANGE_ORDER"
                    change_id = uuid5(po_id, f"change:{po_version}")
                    change = session.get(ChangeOrderRecord, change_id)
                    if change is None:
                        continue
                    counter = Counterproposal.model_validate(change.payload_json)
                else:
                    continue
                if row.status == "SUBMITTING":
                    status: Literal["SUBMITTING", "PROCESSING", "READY_TO_DELIVER"] = "SUBMITTING"
                elif row.status == "PROCESSING":
                    status = "PROCESSING"
                elif row.status == "READY_TO_DELIVER":
                    status = "READY_TO_DELIVER"
                else:
                    continue
                response_document_type = "855" if transaction_type == "PURCHASE_ORDER" else "865"
                response_document = session.scalar(
                    select(EdiDocumentRecord)
                    .where(
                        EdiDocumentRecord.related_document_id == po_id,
                        EdiDocumentRecord.document_type == response_document_type,
                        EdiDocumentRecord.direction == "INBOUND",
                    )
                    .order_by(EdiDocumentRecord.created_at.desc())
                    .limit(1)
                )
                pending.append(
                    PendingSupplierTransaction(
                        transaction_type=transaction_type,
                        status=status,
                        supplier_id=row.supplier_id,
                        idempotency_key=row.idempotency_key,
                        workflow_id=workflow_id,
                        correlation_id=correlation_id,
                        request_message_id=request_message_id,
                        response_message_id=response_message_id,
                        approval_id=approval_id,
                        reply_address=reply_address,
                        external_transaction_id=row.external_transaction_id,
                        poll_url=row.poll_url,
                        response_x12=(
                            response_document.raw_text if response_document is not None else None
                        ),
                        lease_owner=row.lease_owner,
                        purchase_order=po,
                        counterproposal=counter,
                    )
                )
            return tuple(pending)

    def get_purchase_order(self, po_id: str) -> PurchaseOrder:
        with self.session_factory() as session:
            row = session.get(PurchaseOrderRecord, UUID(po_id))
            if row is None:
                raise ValueError("purchase order was not found")
            return self._to_domain(session, row)

    def record_edi_document(
        self,
        *,
        workflow_id: UUID,
        supplier_id: str,
        document_type: Literal["850", "855", "860", "865"],
        direction: Literal["OUTBOUND", "INBOUND"],
        raw_text: str,
        related_document_id: UUID,
    ) -> UUID:
        try:
            control_number = self._control_number(raw_text)
            parsed = self._parse_edi(document_type, raw_text)
            validation_status = "VALID"
        except ValueError as exc:
            control_number = f"INVALID-{sha256(raw_text.encode()).hexdigest()[:12]}"
            parsed = {"error": str(exc)}
            validation_status = "INVALID"
        document_id = uuid5(
            workflow_id,
            f"edi:{supplier_id}:{document_type}:{control_number}",
        )
        with self.session_factory() as session, session.begin():
            existing = session.get(EdiDocumentRecord, document_id)
            if existing is not None:
                if (
                    existing.raw_text != raw_text
                    or existing.direction != direction
                    or existing.related_document_id != related_document_id
                ):
                    raise ValueError("EDI document identity already exists with different content")
                return document_id
            session.add(
                EdiDocumentRecord(
                    id=document_id,
                    workflow_id=workflow_id,
                    supplier_id=supplier_id,
                    document_type=document_type,
                    direction=direction,
                    control_number=control_number,
                    related_document_id=related_document_id,
                    raw_text=raw_text,
                    parsed_json=parsed,
                    validation_status=validation_status,
                )
            )
        return document_id

    def record_acknowledgment_comparison(
        self,
        *,
        po: PurchaseOrder,
        parsed: Parsed855,
        comparison: AcknowledgmentComparison,
    ) -> UUID:
        control_number = self._control_number(parsed.raw_text)
        acknowledgment_id = uuid5(po.po_id, f"ack:{po.version}:{control_number}")
        with self.session_factory() as session, session.begin():
            existing = session.get(AcknowledgmentRecord, acknowledgment_id)
            if existing is not None:
                if existing.status != comparison.status:
                    raise ValueError("acknowledgment identity already has a different comparison")
                return acknowledgment_id
            session.add(
                AcknowledgmentRecord(
                    id=acknowledgment_id,
                    po_id=po.po_id,
                    po_version=po.version,
                    supplier_ack_ref=control_number,
                    acknowledgment_type="855",
                    status=comparison.status,
                )
            )
            session.flush()
            session.add_all(
                AcknowledgmentLineRecord(
                    id=uuid5(acknowledgment_id, f"line:{line.line_number}"),
                    acknowledgment_id=acknowledgment_id,
                    po_line_number=line.line_number,
                    status_code=line.status_code,
                    accepted_quantity=line.accepted_quantity,
                    unit_price=line.unit_price,
                    promised_date=line.promised_date,
                )
                for line in parsed.lines
            )
            session.add_all(
                ExceptionRecord(
                    id=uuid5(
                        acknowledgment_id,
                        f"exception:{index}:{item.category}:{item.po_line_number}",
                    ),
                    workflow_id=po.workflow_id,
                    po_id=po.po_id,
                    acknowledgment_id=acknowledgment_id,
                    severity=item.severity.value,
                    category=item.category,
                    expected_value=item.expected_value,
                    actual_value=item.actual_value,
                    cost_impact=item.cost_impact,
                    schedule_impact_days=item.schedule_impact_days,
                    status="OPEN",
                )
                for index, item in enumerate(comparison.discrepancies, start=1)
            )
        return acknowledgment_id

    def record_change_order(self, counterproposal: Counterproposal, *, approval_id: UUID) -> UUID:
        change_id = uuid5(
            counterproposal.po_id,
            f"change:{counterproposal.change_version}",
        )
        key = counterproposal_idempotency_key(counterproposal)
        with self.session_factory() as session, session.begin():
            existing = session.get(ChangeOrderRecord, change_id)
            if existing is not None:
                if existing.payload_json != counterproposal.model_dump(mode="json"):
                    raise ValueError("change order identity already exists with different content")
                return change_id
            session.add(
                ChangeOrderRecord(
                    id=change_id,
                    po_id=counterproposal.po_id,
                    workflow_id=counterproposal.workflow_id,
                    version=counterproposal.change_version,
                    status="APPROVED",
                    idempotency_key=key,
                    payload_json=counterproposal.model_dump(mode="json"),
                    approval_id=approval_id,
                )
            )
        return change_id

    def record_change_result(self, po_id: UUID, version: int, *, accepted: bool) -> None:
        change_id = uuid5(po_id, f"change:{version}")
        with self.session_factory() as session, session.begin():
            record = session.get(ChangeOrderRecord, change_id)
            if record is None:
                raise ValueError("change response has no persisted change order")
            record.status = "ACCEPTED" if accepted else "REJECTED"

    def _record_intent(
        self,
        *,
        workflow_id: UUID,
        po_id: UUID,
        po_version: int,
        supplier_id: str,
        transaction_type: Literal["PURCHASE_ORDER", "CHANGE_ORDER"],
        idempotency_key: str,
        correlation_id: UUID,
        request_message_id: UUID,
        response_message_id: UUID,
        approval_id: UUID,
        reply_address: str,
    ) -> None:
        with self.session_factory() as session, session.begin():
            existing = session.scalar(
                select(SupplierTransactionRecord).where(
                    SupplierTransactionRecord.supplier_id == supplier_id,
                    SupplierTransactionRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                identity = (
                    existing.workflow_id,
                    existing.po_id,
                    existing.po_version,
                    existing.transaction_type,
                    existing.correlation_id,
                    existing.request_message_id,
                    existing.response_message_id,
                    existing.approval_id,
                    existing.reply_address,
                )
                incoming = (
                    workflow_id,
                    po_id,
                    po_version,
                    transaction_type,
                    correlation_id,
                    request_message_id,
                    response_message_id,
                    approval_id,
                    reply_address,
                )
                if identity != incoming:
                    raise ValueError("supplier transaction intent already has different content")
                return
            session.add(
                SupplierTransactionRecord(
                    workflow_id=workflow_id,
                    po_id=po_id,
                    po_version=po_version,
                    correlation_id=correlation_id,
                    request_message_id=request_message_id,
                    response_message_id=response_message_id,
                    approval_id=approval_id,
                    reply_address=reply_address,
                    supplier_id=supplier_id,
                    transaction_type=transaction_type,
                    idempotency_key=idempotency_key,
                    status="SUBMITTING",
                    attempts=0,
                )
            )

    @staticmethod
    def _to_domain(session: Session, row: PurchaseOrderRecord) -> PurchaseOrder:
        lines = tuple(
            PurchaseOrderLine(
                line_number=line.line_number,
                component_sku=line.component_sku,
                quantity=line.quantity,
                unit_price=line.unit_price,
                requested_date=line.requested_date,
                unit=line.unit,
            )
            for line in session.scalars(
                select(PurchaseOrderLineRecord)
                .where(PurchaseOrderLineRecord.po_id == row.id)
                .order_by(PurchaseOrderLineRecord.line_number)
            )
        )
        return PurchaseOrder(
            po_id=row.id,
            po_number=row.po_number,
            workflow_id=row.workflow_id,
            supplier_id=row.supplier_id,
            version=row.version,
            currency=row.currency,
            lines=lines,
            total=row.total,
        )

    @staticmethod
    def _control_number(raw_text: str) -> str:
        segments = parse_segments(raw_text)
        st = next((segment for segment in segments if segment[0] == "ST"), None)
        if st is None or len(st) < 3:
            raise ValueError("EDI document is missing its transaction control number")
        return st[2]

    @staticmethod
    def _parse_edi(
        document_type: Literal["850", "855", "860", "865"], raw_text: str
    ) -> dict[str, Any]:
        if document_type == "850":
            return asdict(parse_850(raw_text))
        if document_type == "855":
            return parse_855(raw_text).model_dump(mode="json")
        if document_type == "860":
            return parse_860(raw_text).model_dump(mode="json")
        return parse_865(raw_text).model_dump(mode="json")
