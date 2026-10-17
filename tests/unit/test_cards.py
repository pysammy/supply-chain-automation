import json

import pytest

from supplymesh.cards.demand import demand_intake_card
from supplymesh.cards.renderer import parse_card_selection, render_card


def test_demand_card_uses_documented_metadata_envelope() -> None:
    card = demand_intake_card()
    payload = json.loads(card.card_payload)
    assert card.card_protocol_version == "1"
    assert card.requires_card_interaction == "true"
    assert card.card_kind == "form"
    assert payload["submit"]["action"] == "submit_demand"


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
