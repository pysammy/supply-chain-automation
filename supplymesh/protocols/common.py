from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    message_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID
    workflow_id: UUID
    sender_agent: str = Field(min_length=1, max_length=100)
    reply_to: str | None = Field(default=None, max_length=255)
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class WorkflowFailure(MessageEnvelope):
    error: ErrorDetail


class WorkflowStatusRequest(MessageEnvelope):
    pass


class WorkflowStatusResponse(MessageEnvelope):
    status: str
    checkpoint_version: int = Field(ge=1)
    summary: dict[str, Any] = Field(default_factory=dict)
