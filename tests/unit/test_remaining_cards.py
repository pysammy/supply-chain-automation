import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from supplymesh.cards.completion import agent_activity_detail, completion_detail
from supplymesh.cards.exception import exception_review
from supplymesh.cards.inventory import inventory_assessment_detail
from supplymesh.cards.procurement import counterproposal_form, purchase_order_detail
from supplymesh.domain.exceptions import AcknowledgmentComparison, Discrepancy, Severity
from supplymesh.domain.models import InventoryAssessment, ShortageLine
from supplymesh.domain.procurement import PurchaseOrder, PurchaseOrderLine


def test_exception_card_identifies_supplier_and_matches_components_by_po_line_number() -> None:
    po = PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-DELAY-1",
        workflow_id=uuid4(),
        supplier_id="supplier-gamma",
        version=2,
        currency="EUR",
        lines=tuple(
            PurchaseOrderLine(
                line_number=index,
                component_sku=sku,
                quantity=Decimal("10"),
                unit_price=Decimal("1"),
                requested_date=date(2026, 11, 3),
            )
            for index, sku in enumerate(("ENGINE-001", "FRAME-001", "ECU-001"), start=1)
        ),
        total=Decimal("30"),
    )
    comparison = AcknowledgmentComparison(
        status="EXCEPTION",
        discrepancies=tuple(
            Discrepancy(
                category="DELIVERY_DELAYED",
                severity=Severity.HIGH,
                po_line_number=line,
                expected_value="2026-11-03",
                actual_value=actual,
            )
            for line, actual in ((3, "2026-11-24"), (1, "2026-11-10"), (2, "2026-11-17"))
        ),
        absolute_cost_delta=Decimal("0E-10"),
        percentage_cost_delta=Decimal("0"),
        unfulfilled_quantity=Decimal("0"),
        production_units_affected=Decimal("0"),
        estimated_schedule_delay_days=21,
        recommended_action="switch",
        allowed_actions=("accept", "counter", "switch", "cancel"),
    )
    nodes = json.loads(exception_review(comparison, po=po).card_payload)["root"]["children"]
    texts = [node["value"] for node in nodes if node["type"] == "text"]
    assert "supplier-gamma" in texts[0] and "PO-DELAY-1" in texts[0]
    assert "Cost change: EUR 0.00" in texts[2]
    assert "ECU-001 (PO line 3)" in texts[3] and "2026-11-24" in texts[3]
    assert "ENGINE-001 (PO line 1)" in texts[4] and "2026-11-10" in texts[4]
    assert "FRAME-001 (PO line 2)" in texts[5] and "2026-11-17" in texts[5]
    for node in nodes:
        if node["type"] == "button":
            assert node["action"]["selection"]["target_id"] == str(po.po_id)
            assert node["action"]["selection"]["target_version"] == 2


def test_activity_card_compacts_repeated_events_and_long_approver_addresses() -> None:
    activities = [
        {
            "agent": "manufacturing-orchestrator",
            "action": "WORKFLOW_UPDATED",
            "outcome": "COMPLETED" if index == 49 else "SOURCING",
        }
        for index in range(50)
    ]
    activities.append(
        {"agent": "agent1" + "x" * 60, "action": "SEND_COUNTERPROPOSAL", "outcome": "RECORDED"}
    )
    card = agent_activity_detail(activities)
    rows = json.loads(card.card_payload)["summary_rows"]
    assert len(rows) == 2
    assert rows[0] == {
        "label": "Orchestrator",
        "value": "50 recent event(s); latest WORKFLOW_UPDATED: COMPLETED",
    }
    assert rows[1]["label"] == "Human approvals"
    assert "agent1" not in card.card_payload
    assert len(activities) == 51


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
    assert {
        row["label"]: row["value"]
        for row in json.loads(inventory_assessment_detail(assessment).card_payload)["summary_rows"]
    }["Shortages"] == "Yes"
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
    assert po_card["approve_cta"]["selection"]["target_version"] == 2
    assert (
        json.loads(counterproposal_form(po_id=str(po.po_id), po_version=2).card_payload)[
            "submit_cta"
        ]["selection"]["action"]
        == "review_counterproposal"
    )
    completion = json.loads(
        completion_detail(
            workflow_id=str(po.workflow_id),
            total_cost_minor=4_510_000,
            currency="USD",
            target_feasible=True,
            commitments=[],
            audit_references=["audit-1"],
        ).card_payload
    )
    assert {row["label"]: row["value"] for row in completion["summary_rows"]}[
        "Manufacturing target feasible"
    ] == "Yes"
    assert (
        json.loads(
            agent_activity_detail([{"agent": "inventory", "outcome": "shortage"}]).card_payload
        )["summary_rows"][0]["label"]
        == "inventory"
    )
