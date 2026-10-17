"""uAgents wire models for the Pydantic 2 specialist contracts.

uAgents 0.25.x builds protocol schemas from its Pydantic-1-compatible ``Model``
base.  The application contracts remain Pydantic 2 models and are serialized at
this narrow boundary, then strictly revalidated by the receiver.
"""

from typing import Literal, Protocol, cast

from uagents import Model

from supplymesh.protocols.common import WorkflowStatusResponse
from supplymesh.protocols.exceptions import AcknowledgmentComparisonResult
from supplymesh.protocols.planning import DemandPlanningRequest, InventoryShortageReport
from supplymesh.protocols.procurement import (
    AcknowledgmentReceived,
    ChangeOrderSubmitted,
    ChangeResponseReceived,
    CounterproposalExecutionRequest,
    PurchaseOrderExecutionRequest,
    PurchaseOrderSubmitted,
)
from supplymesh.protocols.sourcing import (
    ProcurementPlansReady,
    SourcingRequest,
    SupplierQuotesCollected,
)


class DemandPlanningTransport(Model):
    message_type: Literal["DemandPlanningRequest"] = "DemandPlanningRequest"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    payload_json: str

    @classmethod
    def from_domain(cls, request: DemandPlanningRequest) -> "DemandPlanningTransport":
        return cls(
            schema_version=request.schema_version,
            message_id=str(request.message_id),
            correlation_id=str(request.correlation_id),
            workflow_id=str(request.workflow_id),
            sender_agent=request.sender_agent,
            payload_json=request.model_dump_json(),
        )

    def to_domain(self) -> DemandPlanningRequest:
        request = DemandPlanningRequest.model_validate_json(self.payload_json)
        if (
            request.schema_version != self.schema_version
            or str(request.message_id) != self.message_id
            or str(request.correlation_id) != self.correlation_id
            or str(request.workflow_id) != self.workflow_id
            or request.sender_agent != self.sender_agent
        ):
            raise ValueError("planning transport envelope does not match its validated payload")
        return request


class InventoryShortageTransport(Model):
    message_type: Literal["InventoryShortageReport"] = "InventoryShortageReport"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, report: InventoryShortageReport) -> "InventoryShortageTransport":
        if report.reply_to is None:
            raise ValueError("inventory report must identify the planning request it answers")
        return cls(
            schema_version=report.schema_version,
            message_id=str(report.message_id),
            correlation_id=str(report.correlation_id),
            workflow_id=str(report.workflow_id),
            sender_agent=report.sender_agent,
            reply_to=report.reply_to,
            payload_json=report.model_dump_json(),
        )

    def to_domain(self) -> InventoryShortageReport:
        report = InventoryShortageReport.model_validate_json(self.payload_json)
        if (
            report.schema_version != self.schema_version
            or str(report.message_id) != self.message_id
            or str(report.correlation_id) != self.correlation_id
            or str(report.workflow_id) != self.workflow_id
            or report.sender_agent != self.sender_agent
            or report.reply_to != self.reply_to
        ):
            raise ValueError("inventory transport envelope does not match its validated payload")
        return report


class PlanningFailureTransport(Model):
    message_type: Literal["PlanningFailure"] = "PlanningFailure"
    schema_version: str = "1.0"
    correlation_id: str
    workflow_id: str
    reply_to: str
    code: str
    message: str
    retryable: bool = False


class SourcingRequestTransport(Model):
    message_type: Literal["SourcingRequest"] = "SourcingRequest"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    payload_json: str

    @classmethod
    def from_domain(cls, request: SourcingRequest) -> "SourcingRequestTransport":
        return cls(
            schema_version=request.schema_version,
            message_id=str(request.message_id),
            correlation_id=str(request.correlation_id),
            workflow_id=str(request.workflow_id),
            sender_agent=request.sender_agent,
            payload_json=request.model_dump_json(),
        )

    def to_domain(self) -> SourcingRequest:
        request = SourcingRequest.model_validate_json(self.payload_json)
        _validate_envelope(self, request)
        return request


class SupplierQuotesCollectedTransport(Model):
    message_type: Literal["SupplierQuotesCollected"] = "SupplierQuotesCollected"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, collected: SupplierQuotesCollected) -> "SupplierQuotesCollectedTransport":
        if collected.reply_to is None:
            raise ValueError("quote collection must identify its sourcing request")
        return cls(
            schema_version=collected.schema_version,
            message_id=str(collected.message_id),
            correlation_id=str(collected.correlation_id),
            workflow_id=str(collected.workflow_id),
            sender_agent=collected.sender_agent,
            reply_to=collected.reply_to,
            payload_json=collected.model_dump_json(),
        )

    def to_domain(self) -> SupplierQuotesCollected:
        collected = SupplierQuotesCollected.model_validate_json(self.payload_json)
        _validate_envelope(self, collected, expected_reply_to=self.reply_to)
        return collected


class ProcurementPlansReadyTransport(Model):
    message_type: Literal["ProcurementPlansReady"] = "ProcurementPlansReady"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, plans: ProcurementPlansReady) -> "ProcurementPlansReadyTransport":
        if plans.reply_to is None:
            raise ValueError("procurement plans must identify their quote collection")
        return cls(
            schema_version=plans.schema_version,
            message_id=str(plans.message_id),
            correlation_id=str(plans.correlation_id),
            workflow_id=str(plans.workflow_id),
            sender_agent=plans.sender_agent,
            reply_to=plans.reply_to,
            payload_json=plans.model_dump_json(),
        )

    def to_domain(self) -> ProcurementPlansReady:
        plans = ProcurementPlansReady.model_validate_json(self.payload_json)
        _validate_envelope(self, plans, expected_reply_to=self.reply_to)
        return plans


class SpecialistFailureTransport(Model):
    message_type: Literal["SpecialistFailure"] = "SpecialistFailure"
    schema_version: str = "1.0"
    correlation_id: str
    workflow_id: str
    reply_to: str
    sender_agent: str
    operation: str
    code: str
    message: str
    retryable: bool = False


class PurchaseOrderExecutionTransport(Model):
    message_type: Literal["PurchaseOrderExecutionRequest"] = "PurchaseOrderExecutionRequest"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    payload_json: str

    @classmethod
    def from_domain(
        cls, request: PurchaseOrderExecutionRequest
    ) -> "PurchaseOrderExecutionTransport":
        return cls(
            schema_version=request.schema_version,
            message_id=str(request.message_id),
            correlation_id=str(request.correlation_id),
            workflow_id=str(request.workflow_id),
            sender_agent=request.sender_agent,
            payload_json=request.model_dump_json(),
        )

    def to_domain(self) -> PurchaseOrderExecutionRequest:
        request = PurchaseOrderExecutionRequest.model_validate_json(self.payload_json)
        _validate_envelope(self, request)
        return request


class PurchaseOrderSubmittedTransport(Model):
    message_type: Literal["PurchaseOrderSubmitted"] = "PurchaseOrderSubmitted"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, result: PurchaseOrderSubmitted) -> "PurchaseOrderSubmittedTransport":
        if result.reply_to is None:
            raise ValueError("PO submission must identify its execution request")
        return cls(
            schema_version=result.schema_version,
            message_id=str(result.message_id),
            correlation_id=str(result.correlation_id),
            workflow_id=str(result.workflow_id),
            sender_agent=result.sender_agent,
            reply_to=result.reply_to,
            payload_json=result.model_dump_json(),
        )

    def to_domain(self) -> PurchaseOrderSubmitted:
        result = PurchaseOrderSubmitted.model_validate_json(self.payload_json)
        _validate_envelope(self, result, expected_reply_to=self.reply_to)
        return result


class AcknowledgmentReceivedTransport(Model):
    message_type: Literal["AcknowledgmentReceived"] = "AcknowledgmentReceived"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, result: AcknowledgmentReceived) -> "AcknowledgmentReceivedTransport":
        if result.reply_to is None:
            raise ValueError("acknowledgment must identify its execution request")
        return cls(
            schema_version=result.schema_version,
            message_id=str(result.message_id),
            correlation_id=str(result.correlation_id),
            workflow_id=str(result.workflow_id),
            sender_agent=result.sender_agent,
            reply_to=result.reply_to,
            payload_json=result.model_dump_json(),
        )

    def to_domain(self) -> AcknowledgmentReceived:
        result = AcknowledgmentReceived.model_validate_json(self.payload_json)
        _validate_envelope(self, result, expected_reply_to=self.reply_to)
        return result


class CounterproposalExecutionTransport(Model):
    message_type: Literal["CounterproposalExecutionRequest"] = "CounterproposalExecutionRequest"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    payload_json: str

    @classmethod
    def from_domain(
        cls, request: CounterproposalExecutionRequest
    ) -> "CounterproposalExecutionTransport":
        return cls(
            schema_version=request.schema_version,
            message_id=str(request.message_id),
            correlation_id=str(request.correlation_id),
            workflow_id=str(request.workflow_id),
            sender_agent=request.sender_agent,
            payload_json=request.model_dump_json(),
        )

    def to_domain(self) -> CounterproposalExecutionRequest:
        request = CounterproposalExecutionRequest.model_validate_json(self.payload_json)
        _validate_envelope(self, request)
        return request


class ChangeOrderSubmittedTransport(Model):
    message_type: Literal["ChangeOrderSubmitted"] = "ChangeOrderSubmitted"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, result: ChangeOrderSubmitted) -> "ChangeOrderSubmittedTransport":
        if result.reply_to is None:
            raise ValueError("change submission must identify its execution request")
        return cls(
            schema_version=result.schema_version,
            message_id=str(result.message_id),
            correlation_id=str(result.correlation_id),
            workflow_id=str(result.workflow_id),
            sender_agent=result.sender_agent,
            reply_to=result.reply_to,
            payload_json=result.model_dump_json(),
        )

    def to_domain(self) -> ChangeOrderSubmitted:
        result = ChangeOrderSubmitted.model_validate_json(self.payload_json)
        _validate_envelope(self, result, expected_reply_to=self.reply_to)
        return result


class ChangeResponseReceivedTransport(Model):
    message_type: Literal["ChangeResponseReceived"] = "ChangeResponseReceived"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, result: ChangeResponseReceived) -> "ChangeResponseReceivedTransport":
        if result.reply_to is None:
            raise ValueError("change response must identify its execution request")
        return cls(
            schema_version=result.schema_version,
            message_id=str(result.message_id),
            correlation_id=str(result.correlation_id),
            workflow_id=str(result.workflow_id),
            sender_agent=result.sender_agent,
            reply_to=result.reply_to,
            payload_json=result.model_dump_json(),
        )

    def to_domain(self) -> ChangeResponseReceived:
        result = ChangeResponseReceived.model_validate_json(self.payload_json)
        _validate_envelope(self, result, expected_reply_to=self.reply_to)
        return result


class AcknowledgmentComparisonTransport(Model):
    message_type: Literal["AcknowledgmentComparisonResult"] = "AcknowledgmentComparisonResult"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(
        cls, result: AcknowledgmentComparisonResult
    ) -> "AcknowledgmentComparisonTransport":
        if result.reply_to is None:
            raise ValueError("comparison must identify its acknowledgment")
        return cls(
            schema_version=result.schema_version,
            message_id=str(result.message_id),
            correlation_id=str(result.correlation_id),
            workflow_id=str(result.workflow_id),
            sender_agent=result.sender_agent,
            reply_to=result.reply_to,
            payload_json=result.model_dump_json(),
        )

    def to_domain(self) -> AcknowledgmentComparisonResult:
        result = AcknowledgmentComparisonResult.model_validate_json(self.payload_json)
        _validate_envelope(self, result, expected_reply_to=self.reply_to)
        return result


class WorkflowStatusResponseTransport(Model):
    message_type: Literal["WorkflowStatusResponse"] = "WorkflowStatusResponse"
    schema_version: str
    message_id: str
    correlation_id: str
    workflow_id: str
    sender_agent: str
    reply_to: str
    payload_json: str

    @classmethod
    def from_domain(cls, result: WorkflowStatusResponse) -> "WorkflowStatusResponseTransport":
        if result.reply_to is None:
            raise ValueError("workflow status must identify its source message")
        return cls(
            schema_version=result.schema_version,
            message_id=str(result.message_id),
            correlation_id=str(result.correlation_id),
            workflow_id=str(result.workflow_id),
            sender_agent=result.sender_agent,
            reply_to=result.reply_to,
            payload_json=result.model_dump_json(),
        )

    def to_domain(self) -> WorkflowStatusResponse:
        result = WorkflowStatusResponse.model_validate_json(self.payload_json)
        _validate_envelope(self, result, expected_reply_to=self.reply_to)
        return result


class EnvelopeLike(Protocol):
    @property
    def schema_version(self) -> str: ...

    @property
    def message_id(self) -> object: ...

    @property
    def correlation_id(self) -> object: ...

    @property
    def workflow_id(self) -> object: ...

    @property
    def sender_agent(self) -> str: ...


class ReplyEnvelopeLike(EnvelopeLike, Protocol):
    @property
    def reply_to(self) -> str | None: ...


def _validate_envelope(
    wire: EnvelopeLike, domain: EnvelopeLike, *, expected_reply_to: str | None = None
) -> None:
    fields = ("schema_version", "message_id", "correlation_id", "workflow_id", "sender_agent")
    for field in fields:
        wire_value = getattr(wire, field)
        domain_value = getattr(domain, field)
        if str(wire_value) != str(domain_value):
            raise ValueError("specialist transport envelope does not match its validated payload")
    if (
        expected_reply_to is not None
        and cast(ReplyEnvelopeLike, domain).reply_to != expected_reply_to
    ):
        raise ValueError("specialist transport reply reference does not match its payload")
