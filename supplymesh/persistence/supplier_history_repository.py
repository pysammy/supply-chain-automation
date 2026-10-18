"""Supplier-owned transaction history, independent of the manufacturer's workflow records."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from supplymesh.domain.procurement import (
    ChangeOrderSubmission,
    PurchaseOrderCancellation,
    PurchaseOrderSubmission,
    SupplierCancellationConfirmed,
)
from supplymesh.persistence.orm_models import SupplierOrderHistoryRecord
from supplymesh.services.approval_service import approval_payload_hash

Submission = PurchaseOrderSubmission | ChangeOrderSubmission


class SupplierHistoryRepository:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _request_hash(submission: Submission) -> str:
        # Transport retries may carry another message ID; business terms must stay identical.
        return approval_payload_hash(submission.model_dump(mode="json", exclude={"message_id"}))

    def find(
        self, supplier_id: str, submission: Submission, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self.sessions() as session:
            row = session.scalar(
                select(SupplierOrderHistoryRecord).where(
                    SupplierOrderHistoryRecord.manufacturer_id == submission.manufacturer_id,
                    SupplierOrderHistoryRecord.supplier_id == supplier_id,
                    SupplierOrderHistoryRecord.document_type == submission.document_type,
                    SupplierOrderHistoryRecord.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            if row.payload_hash != self._request_hash(submission):
                raise ValueError("idempotency key reused with changed order terms")
            return dict(row.accepted_response)

    def save(
        self,
        *,
        supplier_id: str,
        submission: Submission,
        idempotency_key: str,
        accepted: dict[str, Any],
        status: dict[str, Any],
        currency: str,
        lines: list[dict[str, Any]],
        outcome: str,
    ) -> dict[str, Any]:
        row = SupplierOrderHistoryRecord(
            transaction_id=accepted["supplier_transaction_id"],
            manufacturer_id=submission.manufacturer_id,
            manufacturer_name=submission.manufacturer_name,
            supplier_id=supplier_id,
            document_type=submission.document_type,
            idempotency_key=idempotency_key,
            payload_hash=self._request_hash(submission),
            po_number=submission.po_number,
            po_version=submission.po_version,
            currency=currency,
            total=sum(
                (Decimal(line["quantity"]) * Decimal(line["unit_price"]) for line in lines),
                Decimal("0"),
            ),
            lines=lines,
            x12=submission.x12,
            accepted_response=accepted,
            status_response=status,
            outcome=outcome,
        )
        try:
            with self.sessions() as session, session.begin():
                if submission.document_type == "860":
                    original = session.scalar(
                        select(SupplierOrderHistoryRecord)
                        .where(
                            SupplierOrderHistoryRecord.manufacturer_id
                            == submission.manufacturer_id,
                            SupplierOrderHistoryRecord.supplier_id == supplier_id,
                            SupplierOrderHistoryRecord.po_number == submission.po_number,
                            SupplierOrderHistoryRecord.document_type == "850",
                        )
                        .with_for_update()
                    )
                    if original is not None and original.cancellation_response:
                        raise ValueError("a cancelled supplier order cannot be changed")
                session.add(row)
        except IntegrityError:
            existing = self.find(supplier_id, submission, idempotency_key)
            if existing is not None:
                return existing
            raise ValueError("this manufacturer PO version has already been submitted") from None
        return accepted

    def status(self, supplier_id: str, transaction_id: str, document_type: str) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(SupplierOrderHistoryRecord, transaction_id)
            if row is None or row.supplier_id != supplier_id or row.document_type != document_type:
                raise KeyError(transaction_id)
            if row.cancellation_response:
                return {"status": "CANCELLED"}
            return dict(row.status_response)

    def cancel(
        self, supplier_id: str, transaction_id: str, request: PurchaseOrderCancellation
    ) -> SupplierCancellationConfirmed:
        with self.sessions() as session, session.begin():
            original = session.scalar(
                select(SupplierOrderHistoryRecord)
                .where(
                    SupplierOrderHistoryRecord.transaction_id == transaction_id,
                    SupplierOrderHistoryRecord.supplier_id == supplier_id,
                    SupplierOrderHistoryRecord.manufacturer_id == request.manufacturer_id,
                    SupplierOrderHistoryRecord.po_number == request.po_number,
                    SupplierOrderHistoryRecord.po_version == request.po_version,
                    SupplierOrderHistoryRecord.document_type == "850",
                )
                .with_for_update()
            )
            if original is None:
                raise ValueError("cancellation does not match an existing manufacturer PO")
            if original.cancellation_response:
                return SupplierCancellationConfirmed.model_validate(original.cancellation_response)
            result = SupplierCancellationConfirmed(
                supplier_transaction_id=transaction_id,
                po_number=request.po_number,
                po_version=request.po_version,
                cancelled_at=datetime.now(UTC),
            )
            # Cancellation is for the whole supplier PO, including accepted change versions.
            rows = session.scalars(
                select(SupplierOrderHistoryRecord).where(
                    SupplierOrderHistoryRecord.manufacturer_id == request.manufacturer_id,
                    SupplierOrderHistoryRecord.supplier_id == supplier_id,
                    SupplierOrderHistoryRecord.po_number == request.po_number,
                )
            )
            for row in rows:
                row.cancellation_response = result.model_dump(mode="json")
                if row.outcome != "CHANGE_REJECTED":
                    row.outcome = "CANCELLED"
            return result

    def history(
        self, *, manufacturer_id: str | None = None, supplier_id: str | None = None, offset: int = 0
    ) -> dict[str, Any]:
        query = select(SupplierOrderHistoryRecord)
        if manufacturer_id:
            query = query.where(SupplierOrderHistoryRecord.manufacturer_id == manufacturer_id)
        if supplier_id:
            query = query.where(SupplierOrderHistoryRecord.supplier_id == supplier_id)
        query = (
            query.order_by(
                SupplierOrderHistoryRecord.created_at.desc(),
                SupplierOrderHistoryRecord.transaction_id.desc(),
            )
            .offset(offset)
            .limit(11)
        )
        with self.sessions() as session:
            rows = list(session.scalars(query))
            items = [
                {
                    "transaction_id": row.transaction_id,
                    "manufacturer_id": row.manufacturer_id,
                    "manufacturer_name": row.manufacturer_name,
                    "supplier_id": row.supplier_id,
                    "po_number": row.po_number,
                    "po_version": row.po_version,
                    "document_type": row.document_type,
                    "created_at": row.created_at.replace(tzinfo=UTC).isoformat()
                    if row.created_at.tzinfo is None
                    else row.created_at.astimezone(UTC).isoformat(),
                    "status": row.outcome,
                    "currency": row.currency,
                    "total": str(row.total),
                    "lines": row.lines,
                }
                for row in rows[:10]
            ]
        return {"items": items, "next_offset": offset + 10 if len(rows) > 10 else None}
