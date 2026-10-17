import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from supplymesh.cards.completion import agent_activity_detail, completion_detail
from supplymesh.cards.inventory import inventory_assessment_detail
from supplymesh.cards.procurement import counterproposal_form, purchase_order_detail
from supplymesh.domain.models import InventoryAssessment, ShortageLine
from supplymesh.domain.procurement import PurchaseOrder, PurchaseOrderLine


def test_required_detail_and_form_cards_are_versioned_and_structured() -> None:
    assessment = InventoryAssessment(
        demand_order_id=uuid4(),
        product_sku="MOTO-URBANX-2026",
        demand_quantity=Decimal("75"),
        required_by=date(2026, 12, 1),
        lines=(
            ShortageLine(
                component_sku="ENGINE-001",
                unit="EA",
                gross_required=Decimal("75"),
                available_to_promise=Decimal("20"),
                tentative_reservation=Decimal("20"),
                shortage=Decimal("55"),
                evidence=("existing_reservations=100",),
            ),
        ),
        has_shortages=True,
        calculated_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    assert json.loads(inventory_assessment_detail(assessment).card_payload)["has_shortages"]
    po = PurchaseOrder(
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
                quantity=Decimal("55"),
                unit_price=Decimal("820"),
                requested_date=date(2026, 12, 1),
            ),
        ),
        total=Decimal("45100"),
    )
    po_card = json.loads(purchase_order_detail(po).card_payload)
    assert po_card["target"]["version"] == 2
    assert (
        json.loads(counterproposal_form(po_id=str(po.po_id), po_version=2).card_payload)["submit"][
            "action"
        ]
        == "review_counterproposal"
    )
    assert json.loads(
        completion_detail(
            workflow_id=str(po.workflow_id),
            total_cost_minor=4_510_000,
            currency="USD",
            target_feasible=True,
            commitments=[],
            audit_references=["audit-1"],
        ).card_payload
    )["target_feasible"]
    assert (
        json.loads(
            agent_activity_detail([{"agent": "inventory", "outcome": "shortage"}]).card_payload
        )["activities"][0]["agent"]
        == "inventory"
    )
