from typing import Literal

from supplymesh.domain.exceptions import AcknowledgmentComparison
from supplymesh.protocols.common import MessageEnvelope


class AcknowledgmentComparisonResult(MessageEnvelope):
    message_type: Literal["AcknowledgmentComparisonResult"] = "AcknowledgmentComparisonResult"
    po_id: str
    po_version: int
    comparison: AcknowledgmentComparison


class ExceptionDecision(MessageEnvelope):
    message_type: Literal["ExceptionDecision"] = "ExceptionDecision"
    po_id: str
    po_version: int
    action: Literal["accept", "counter", "switch", "split", "cancel"]
    actor: str
    reason: str | None = None
