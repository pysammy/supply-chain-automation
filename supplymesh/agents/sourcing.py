from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import ValidationError
from uagents import Context
from uagents import Protocol as AgentProtocol

from supplymesh.agents.transport import (
    ProcurementPlansReadyTransport,
    SourcingRequestTransport,
    SpecialistFailureTransport,
    SupplierQuotesCollectedTransport,
)
from supplymesh.protocols.sourcing import (
    ProcurementPlansReady,
    SourcingRequest,
    SupplierQuotesCollected,
)
from supplymesh.services.fx_service import FxProvider, FxRepository, get_fx_rate
from supplymesh.services.optimizer import optimize_plans
from supplymesh.services.sourcing_service import (
    SupplierGateway,
    collect_quotes,
    normalize_quotes,
)


class SourcingAgentService:
    name = "sourcing-and-optimization"

    def __init__(
        self,
        gateway: SupplierGateway,
        supplier_ids: tuple[str, ...],
        *,
        minimum_valid_quotes: int = 2,
    ) -> None:
        self.gateway = gateway
        self.supplier_ids = supplier_ids
        self.minimum_valid_quotes = minimum_valid_quotes

    async def collect(
        self, request: SourcingRequest, *, now: datetime | None = None
    ) -> SupplierQuotesCollected:
        return await collect_quotes(
            request,
            self.supplier_ids,
            self.gateway,
            minimum_valid_quotes=self.minimum_valid_quotes,
            now=now,
        )

    def plan(
        self,
        request: SourcingRequest,
        collected: SupplierQuotesCollected,
        *,
        fx_to_target: dict[str, Decimal],
    ) -> ProcurementPlansReady:
        lines = normalize_quotes(collected.quotes, request.required_by, fx_to_target)
        result = optimize_plans(request.shortages, lines, currency=request.currency)
        return ProcurementPlansReady(
            correlation_id=request.correlation_id,
            workflow_id=request.workflow_id,
            sender_agent=self.name,
            reply_to=str(collected.message_id),
            result=result,
        )


class FxResolver(Protocol):
    async def resolve(
        self, source_currencies: set[str], target_currency: str
    ) -> dict[str, Decimal]: ...


class LiveFxResolver:
    def __init__(self, provider: FxProvider, repository: FxRepository) -> None:
        self.provider = provider
        self.repository = repository

    async def resolve(
        self, source_currencies: set[str], target_currency: str
    ) -> dict[str, Decimal]:
        rates: dict[str, Decimal] = {}
        for currency in sorted(source_currencies):
            rate = await get_fx_rate(currency, target_currency, self.provider, self.repository)
            rates[currency] = rate.rate
        return rates


def build_sourcing_protocol(
    service: SourcingAgentService, fx_resolver: FxResolver
) -> AgentProtocol:
    protocol = AgentProtocol(name="SupplyMeshSourcing", version="1.0.0")

    @protocol.on_message(
        SourcingRequestTransport,
        replies={
            SupplierQuotesCollectedTransport,
            ProcurementPlansReadyTransport,
            SpecialistFailureTransport,
        },
    )
    async def handle_sourcing(ctx: Context, sender: str, msg: SourcingRequestTransport) -> None:
        try:
            request = msg.to_domain()
            collected = await service.collect(request)
            await ctx.send(sender, SupplierQuotesCollectedTransport.from_domain(collected))
            rates = await fx_resolver.resolve(
                {quote.currency for quote in collected.quotes}, request.currency
            )
            plans = service.plan(request, collected, fx_to_target=rates)
            await ctx.send(sender, ProcurementPlansReadyTransport.from_domain(plans))
        except (ValidationError, ValueError, RuntimeError):
            await ctx.send(
                sender,
                SpecialistFailureTransport(
                    correlation_id=msg.correlation_id,
                    workflow_id=msg.workflow_id,
                    reply_to=msg.message_id,
                    sender_agent=service.name,
                    operation="SOURCING",
                    code="SOURCING_FAILED",
                    message="Supplier sourcing could not produce valid procurement plans.",
                    retryable=True,
                ),
            )

    return protocol
