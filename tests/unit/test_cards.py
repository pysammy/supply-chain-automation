import json

import pytest

from supplymesh.cards.demand import DemandIntake, demand_intake_card
from supplymesh.cards.procurement import counterproposal_form
from supplymesh.cards.renderer import parse_card_selection, render_card


def test_demand_card_uses_documented_metadata_envelope() -> None:
    card = demand_intake_card()
    payload = json.loads(card.card_payload)
    assert card.card_protocol_version == "1"
    assert card.requires_card_interaction == "true"
    assert card.card_kind == "form"
    assert payload["submit_cta"]["selection"]["action"] == "submit_demand"


def test_card_payload_limit_is_enforced() -> None:
    with pytest.raises(ValueError, match="maximum"):
        render_card("custom", {"text": "x" * (64 * 1024)})


def test_direct_selection_is_strict_and_typed() -> None:
    selection = parse_card_selection(
        '{"action":"approve","target_id":"plan-1","target_version":2,"values":{}}'
    )
    assert selection is not None
    assert selection.target_version == 2
    assert parse_card_selection("approve it") is None
    assert parse_card_selection('{"action":"approve","unexpected":true}') is None


def test_platform_form_reply_merges_fields_into_emitted_cta() -> None:
    payload = json.loads(demand_intake_card().card_payload)
    # Platform wire contract: CTA selection merged with top-level field values.
    reply = {
        **payload["submit_cta"]["selection"],
        "product_sku": "MOTO-URBANX-2026",
        "quantity": 75,
        "required_by": "2026-12-01",
        "priority": "normal",
        "cost_ceiling_minor": "",
    }
    selection = parse_card_selection(json.dumps(reply))
    assert selection is not None
    assert DemandIntake.model_validate(selection.values).quantity == 75
    assert "cost_ceiling_minor" not in selection.values


def test_flat_counterproposal_keeps_approval_target_and_version() -> None:
    payload = json.loads(counterproposal_form(po_id="po-1", po_version=3).card_payload)
    selection = parse_card_selection(
        json.dumps({**payload["submit_cta"]["selection"], "quantity": 50, "note": ""})
    )
    assert selection is not None
    assert selection.target_id == "po-1"
    assert selection.target_version == 3
    assert selection.values == {"quantity": 50}


@pytest.mark.parametrize(
    "reply",
    [
        {"action": "submit_demand", "quantity": 2, "values": {"quantity": 3}},
        {"action": "submit_demand", "quantity": 2, "unexpected": True},
        {"action": [], "quantity": 2},
    ],
)
def test_ambiguous_or_unknown_flat_form_inputs_are_rejected(reply: dict) -> None:
    assert parse_card_selection(json.dumps(reply)) is None
