"""Typed cancellation messages using the existing specialist wire envelope."""

from typing import Literal

from uagents import Model

from supplymesh.agents.transport import _validate_envelope
from supplymesh.protocols.procurement import (
    PurchaseOrderCancellationRequest,
    PurchaseOrderCancelled,
)


class CancellationRequestTransport(Model):
    message_type: Literal["PurchaseOrderCancellationRequest"] = "PurchaseOrderCancellationRequest"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    payload_json: str

    @classmethod
    def from_domain(
        cls, request: PurchaseOrderCancellationRequest
    ) -> "CancellationRequestTransport":
        return cls(
            schema_version=request.schema_version,
            message_id=str(request.message_id),
            correlation_id=str(request.correlation_id),
            workflow_id=str(request.workflow_id),
            sender_agent=request.sender_agent,
            payload_json=request.model_dump_json(),
        )

    def to_domain(self) -> PurchaseOrderCancellationRequest:
        request = PurchaseOrderCancellationRequest.model_validate_json(self.payload_json)
        _validate_envelope(self, request)
        return request


class PurchaseOrderCancelledTransport(Model):
    message_type: Literal["PurchaseOrderCancelled"] = "PurchaseOrderCancelled"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, response: PurchaseOrderCancelled) -> "PurchaseOrderCancelledTransport":
        if response.reply_to is None:
            raise ValueError("cancellation response must identify its request")
        return cls(
            schema_version=response.schema_version,
            message_id=str(response.message_id),
            correlation_id=str(response.correlation_id),
            workflow_id=str(response.workflow_id),
            sender_agent=response.sender_agent,
            reply_to=response.reply_to,
            payload_json=response.model_dump_json(),
        )

    def to_domain(self) -> PurchaseOrderCancelled:
        response = PurchaseOrderCancelled.model_validate_json(self.payload_json)
        _validate_envelope(self, response, expected_reply_to=self.reply_to)
        return response
