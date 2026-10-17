from datetime import date
from decimal import Decimal
from uuid import uuid4

from supplymesh.domain.exceptions import Severity
from supplymesh.domain.procurement import PurchaseOrder, PurchaseOrderLine
from supplymesh.edi.x12_855 import AcknowledgmentLine, Parsed855
from supplymesh.services.comparison_service import compare_acknowledgment


def po() -> PurchaseOrder:
    return PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-1",
        workflow_id=uuid4(),
        supplier_id="supplier-alpha",
        version=2,
        currency="USD",
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit_price=Decimal("820"),
                requested_date=date(2026, 12, 1),
            ),
        ),
        total=Decimal("61500"),
    )


def acknowledgment(
    *,
    quantity: str = "75",
    price: str = "820",
    promised: date = date(2026, 12, 1),
    version: int = 2,
) -> Parsed855:
    return Parsed855(
        po_number="PO-1",
        po_version=version,
        currency="USD",
        lines=(
            AcknowledgmentLine(
                line_number=1,
                component_sku="ENGINE-001",
                status_code="IA",
                accepted_quantity=Decimal(quantity),
                unit="EA",
                unit_price=Decimal(price),
                promised_date=promised,
            ),
        ),
        raw_text="x12",
        unknown_segments=(),
    )


def test_exact_acknowledgment_matches_and_completes() -> None:
    result = compare_acknowledgment(po(), acknowledgment())
    assert result.status == "MATCHED"
    assert result.discrepancies == ()
    assert result.recommended_action == "complete"


def test_price_quantity_and_delivery_conflicts_have_deterministic_impact() -> None:
    result = compare_acknowledgment(
        po(),
        acknowledgment(quantity="65", price="844.60", promised=date(2026, 12, 8)),
        component_quantity_per_product={"ENGINE-001": Decimal("1")},
    )
    categories = {item.category for item in result.discrepancies}
    assert categories == {"QUANTITY_REDUCED", "PRICE_CHANGED", "DELIVERY_DELAYED"}
    assert all(item.severity == Severity.HIGH for item in result.discrepancies)
    assert result.unfulfilled_quantity == Decimal("10")
    assert result.production_units_affected == Decimal("10")
    assert result.estimated_schedule_delay_days == 7
    assert result.recommended_action == "split"


def test_small_price_increase_requires_medium_exception_and_stale_ack_is_quarantined() -> None:
    medium = compare_acknowledgment(po(), acknowledgment(price="828.20"))
    assert medium.status == "EXCEPTION"
    assert medium.discrepancies[0].severity == Severity.MEDIUM
    stale = compare_acknowledgment(po(), acknowledgment(version=1))
    assert stale.status == "QUARANTINED"
    assert stale.recommended_action == "cancel"
    future = compare_acknowledgment(po(), acknowledgment(version=3))
    assert future.status == "QUARANTINED"
    assert future.recommended_action == "cancel"
