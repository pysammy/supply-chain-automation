from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from supplymesh.domain.procurement import (
    ApprovalRecord,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierTransactionAccepted,
)
from supplymesh.services.approval_service import approval_payload_hash
from supplymesh.services.procurement_service import execute_purchase_order


class FakeGateway:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def submit_purchase_order(
        self, supplier_id, submission, *, idempotency_key, correlation_id
    ):
        del supplier_id, submission, idempotency_key, correlation_id
        self.events.append("external_call")
        self.calls += 1
        return SupplierTransactionAccepted(
            supplier_transaction_id="TXN-1",
            poll_url="/status/TXN-1",
            estimated_ready_at=datetime(2026, 10, 16, tzinfo=UTC),
        )


class FakeJournal:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.results: dict[str, dict[str, str]] = {}

    def find_success(self, idempotency_key):
        return self.results.get(idempotency_key)

    def record_pending(self, idempotency_key, operation, payload_hash):
        del idempotency_key, operation, payload_hash
        self.events.append("persist_pending")

    def record_success(self, idempotency_key, result):
        self.events.append("persist_success")
        self.results[idempotency_key] = result


def po_and_approval() -> tuple[PurchaseOrder, ApprovalRecord]:
    po = PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-1",
        workflow_id=uuid4(),
        supplier_id="supplier-alpha",
        version=1,
        currency="USD",
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("5"),
                unit_price=Decimal("10"),
                requested_date=date(2026, 12, 1),
            ),
        ),
        total=Decimal("50"),
    )
    approval = ApprovalRecord(
        approval_id=uuid4(),
        workflow_id=po.workflow_id,
        decision_type="SEND_PURCHASE_ORDER",
        target_type="purchase_order",
        target_id=str(po.po_id),
        target_version=1,
        decision="APPROVED",
        actor="manager",
        payload_hash=approval_payload_hash(po),
        decided_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    return po, approval


@pytest.mark.asyncio
async def test_side_effect_is_persisted_before_and_after_and_not_duplicated() -> None:
    events: list[str] = []
    gateway = FakeGateway(events)
    journal = FakeJournal(events)
    po, approval = po_and_approval()
    first = await execute_purchase_order(
        po, approval, gateway, journal, correlation_id=str(uuid4())
    )
    second = await execute_purchase_order(
        po, approval, gateway, journal, correlation_id=str(uuid4())
    )
    assert first == second
    assert gateway.calls == 1
    assert events == ["persist_pending", "external_call", "persist_success"]
