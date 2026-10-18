import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from uagents_core.identity import is_valid_address

MAX_CARD_PAYLOAD_BYTES = 64 * 1024
CardKind = Literal["form", "carousel", "detail", "review", "custom"]


class CardEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    card_protocol_version: Literal["1"] = "1"
    requires_card_interaction: Literal["true", "false"] = "true"
    card_kind: CardKind
    card_payload: str
    is_terminal: Literal["true", "false"] = "false"

    def metadata(self) -> dict[str, str]:
        return self.model_dump()


class CardSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=64)
    target_id: str | None = Field(default=None, max_length=255)
    target_version: int | None = Field(default=None, ge=1)
    values: dict[str, Any] = Field(default_factory=dict)


def render_card(
    kind: CardKind, payload: dict[str, Any], *, informational: bool = False
) -> CardEnvelope:
    if kind == "detail" and not payload.get("ctas"):
        payload = {
            **payload,
            "ctas": [{"label": "Done", "selection": {"action": "dismiss_card"}}],
        }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    size = len(serialized.encode("utf-8"))
    if size > MAX_CARD_PAYLOAD_BYTES:
        raise ValueError(f"card payload is {size} bytes; maximum is {MAX_CARD_PAYLOAD_BYTES}")
    return CardEnvelope(
        card_kind=kind, card_payload=serialized, is_terminal="true" if informational else "false"
    )


def parse_card_selection(raw: str) -> CardSelection | None:
    """Strictly parse a direct card reply; ambiguity is represented by None."""
    raw = raw.strip()
    # Direct ASI:One replies can retain one leading recipient mention.
    if raw.startswith("@"):
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            return None
        mention = parts[0][1:]
        if not (
            is_valid_address(mention) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,19}", mention)
        ):
            return None
        # The prefix is presentation only; sender/workflow authorization happens separately.
        raw = parts[1]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    # Native ASI:One Stripe checkout returns a chat callback rather than a CTA selection.
    if set(value) == {"stripe_checkout_session_id"}:
        value = {"action": "confirm_payment", "target_id": value["stripe_checkout_session_id"]}
    # This UI flag is not a workflow approval; explicit actions still require
    # the existing target/version and workflow-state checks in the coordinator.
    if "approved" in value:
        approved = value.pop("approved")
        if not isinstance(approved, bool):
            return None
        action = value.get("action")
        if approved is False and isinstance(action, str) and action.startswith("approve"):
            return None
    # ASI:One merges form inputs into the CTA selection at the top level.
    # Keep the existing nested format for saved messages and the CLI harness.
    fields = (
        {
            "submit_demand": {
                "product_sku",
                "quantity",
                "required_by",
                "priority",
                "cost_ceiling_minor",
                "currency",
            },
            "review_counterproposal": {"quantity", "maximum_price", "required_date", "note"},
            "supplier_inquiry": {
                "operation",
                "supplier_id",
                "component_sku",
                "quantity",
                "required_by",
            },
            "supplier_rfq": {
                "operation",
                "supplier_id",
                "component_sku",
                "quantity",
                "required_by",
            },
            "supplier_capacity": {
                "operation",
                "supplier_id",
                "component_sku",
                "quantity",
                "required_by",
            },
        }.get(value.get("action"))
        if isinstance(value.get("action"), str)
        else None
    )
    if fields is not None:
        flat = {key: value[key] for key in fields if key in value}
        if flat and "values" in value:
            return None  # Two sources for form inputs are ambiguous.
        if flat:
            value = {key: item for key, item in value.items() if key not in fields}
            value["values"] = {key: item for key, item in flat.items() if item not in (None, "")}
    try:
        return CardSelection.model_validate(value)
    except ValidationError:
        return None
