import json

import pytest

from supplymesh.cards.demand import DemandIntake, demand_intake_card
from supplymesh.cards.procurement import counterproposal_form
from supplymesh.cards.renderer import CardEnvelope, parse_card_selection, render_card


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


def test_informational_detail_includes_required_nonempty_ctas() -> None:
    card = render_card("detail", {"title": "Completed", "summary_rows": []}, informational=True)
    assert json.loads(card.card_payload)["ctas"] == [
        {"label": "Done", "selection": {"action": "dismiss_card"}}
    ]
    assert card.is_terminal == "true"


def test_detail_preserves_explicit_ctas() -> None:
    ctas = [{"label": "Continue", "selection": {"action": "continue"}}]
    card = render_card("detail", {"title": "Review", "summary_rows": [], "ctas": ctas})
    assert json.loads(card.card_payload)["ctas"] == ctas


@pytest.mark.parametrize(
    "card",
    [demand_intake_card(), counterproposal_form(po_id="po-1", po_version=2)],
)
def test_forms_use_only_platform_supported_top_level_keys(card: CardEnvelope) -> None:
    # Live ASI:One playground rejects extra keys, including subtitle.
    assert set(json.loads(card.card_payload)) == {"title", "fields", "submit_cta"}


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


def test_captured_asi_form_submission_is_accepted() -> None:
    raw = (
        "@agent1qtfzla78vr2uznva9twcsz5xseyzs4z9paxk3zu8p49mdh5h72g92u5r6wk "
        '{"product_sku":"MOTO-URBANX-2026","quantity":50,"required_by":"2026-12-15",'
        '"priority":"high","cost_ceiling_minor":100000000,"action":"submit_demand",'
        '"approved":true}'
    )
    selection = parse_card_selection(raw)
    assert selection is not None
    assert selection.action == "submit_demand"
    intake = DemandIntake.model_validate(selection.values)
    assert intake.quantity == 50
    assert intake.cost_ceiling_minor == 100_000_000


def test_platform_confirmation_preserves_explicit_approval_target() -> None:
    selection = parse_card_selection(
        '{"action":"approve_plan","target_id":"plan-1","target_version":3,"approved":true}'
    )
    assert selection is not None
    assert selection.action == "approve_plan"
    assert selection.target_id == "plan-1"
    assert selection.target_version == 3


@pytest.mark.parametrize(
    "raw",
    [
        '@someone {"action":"submit_demand","quantity":50}',
        'Please submit {"action":"submit_demand","quantity":50}',
        '{"action":"submit_demand","approved":"true"}',
        '{"action":"approve_plan","approved":false}',
        '{"approved":true,"target_id":"plan-1","target_version":3}',
        '{"action":"submit_demand","approved":true,"unexpected":1}',
    ],
)
def test_platform_wrapper_does_not_relax_selection_validation(raw: str) -> None:
    assert parse_card_selection(raw) is None
