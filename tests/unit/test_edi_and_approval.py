from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from supplymesh.domain.procurement import ApprovalRecord, PurchaseOrder, PurchaseOrderLine
from supplymesh.edi.x12_850 import generate_850, parse_850
from supplymesh.edi.x12_855 import AcknowledgmentLine, generate_855, parse_855
from supplymesh.services.approval_service import approval_payload_hash, verify_approval


def purchase_order() -> PurchaseOrder:
    workflow_id = uuid4()
    lines = (
        PurchaseOrderLine(
            line_number=1,
            component_sku="ENGINE-001",
            quantity=Decimal("75"),
            unit_price=Decimal("820.00"),
            requested_date=date(2026, 12, 1),
        ),
    )
    return PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-2026-0001",
        workflow_id=workflow_id,
        supplier_id="supplier-alpha",
        version=1,
        currency="USD",
        lines=lines,
        total=Decimal("61500.00"),
    )


def approval_for(po: PurchaseOrder) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=uuid4(),
        workflow_id=po.workflow_id,
        decision_type="SEND_PURCHASE_ORDER",
        target_type="purchase_order",
        target_id=str(po.po_id),
        target_version=po.version,
        decision="APPROVED",
        actor="manager@example.com",
        payload_hash=approval_payload_hash(po),
        decided_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


def test_850_round_trip_validates_envelope_and_required_segments() -> None:
    raw = generate_850(purchase_order(), "123")
    parsed = parse_850(raw)
    assert parsed.transaction_type == "850"
    assert any(segment[0] == "PO1" and segment[7] == "ENGINE-001" for segment in parsed.segments)


def test_855_round_trip_preserves_structured_acknowledgment() -> None:
    raw = generate_855(
        po_number="PO-2026-0001",
        po_version=1,
        currency="USD",
        lines=(
            AcknowledgmentLine(
                line_number=1,
                component_sku="ENGINE-001",
                status_code="IA",
                accepted_quantity=Decimal("75"),
                unit="EA",
                unit_price=Decimal("820.00"),
                promised_date=date(2026, 12, 1),
            ),
        ),
        control_number="456",
    )
    parsed = parse_855(raw)
    assert parsed.po_number == "PO-2026-0001"
    assert parsed.lines[0].accepted_quantity == Decimal("75")
    assert parsed.lines[0].unit_price == Decimal("820.00")


def test_approval_hash_rejects_stale_or_modified_purchase_order() -> None:
    po = purchase_order()
    approval = approval_for(po)
    verify_approval(
        approval,
        expected_decision_type="SEND_PURCHASE_ORDER",
        target_id=str(po.po_id),
        target_version=1,
        current_payload=po,
    )
    changed = po.model_copy(update={"total": Decimal("999.00")})
    with pytest.raises(ValueError, match="payload changed"):
        verify_approval(
            approval,
            expected_decision_type="SEND_PURCHASE_ORDER",
            target_id=str(po.po_id),
            target_version=1,
            current_payload=changed,
        )


def test_approval_hash_treats_equivalent_decimal_scales_as_equal() -> None:
    po = purchase_order()
    scaled = po.model_copy(
        update={
            "lines": (
                po.lines[0].model_copy(
                    update={
                        "quantity": Decimal("75.000000"),
                        "unit_price": Decimal("820.0000"),
                    }
                ),
            ),
            "total": Decimal("61500.00"),
        }
    )

    assert approval_payload_hash(po) == approval_payload_hash(scaled)
