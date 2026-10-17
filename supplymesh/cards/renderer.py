import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_CARD_PAYLOAD_BYTES = 64 * 1024


class CardEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    card_protocol_version: Literal["1"] = "1"
    requires_card_interaction: Literal["true", "false"] = "true"
    card_kind: Literal["form", "carousel", "review", "custom"]
    card_payload: str

    def metadata(self) -> dict[str, str]:
        return self.model_dump()


class CardSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=64)
    target_id: str | None = Field(default=None, max_length=255)
    target_version: int | None = Field(default=None, ge=1)
    values: dict[str, Any] = Field(default_factory=dict)


def render_card(
    kind: Literal["form", "carousel", "review", "custom"], payload: dict[str, Any]
) -> CardEnvelope:
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    size = len(serialized.encode("utf-8"))
    if size > MAX_CARD_PAYLOAD_BYTES:
        raise ValueError(f"card payload is {size} bytes; maximum is {MAX_CARD_PAYLOAD_BYTES}")
    return CardEnvelope(card_kind=kind, card_payload=serialized)


def parse_card_selection(raw: str) -> CardSelection | None:
    """Strictly parse a direct card reply; ambiguity is represented by None."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    try:
        return CardSelection.model_validate(value)
    except ValidationError:
        return None
