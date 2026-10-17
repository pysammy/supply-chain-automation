import asyncio
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID, uuid5

from supplymesh.domain.sourcing import NormalizedQuoteLine, RfqLine, RfqRequest, SupplierQuote
from supplymesh.protocols.sourcing import SourcingRequest, SupplierQuotesCollected


class SupplierGateway(Protocol):
    async def submit_rfq(
        self,
        supplier_id: str,
        request: RfqRequest,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierQuote: ...


async def collect_quotes(
    request: SourcingRequest,
    supplier_ids: tuple[str, ...],
    client: SupplierGateway,
    *,
    minimum_valid_quotes: int,
    now: datetime | None = None,
) -> SupplierQuotesCollected:
    observed = now or datetime.now(UTC)

    async def call(supplier_id: str) -> SupplierQuote:
        rfq = RfqRequest(
            rfq_id=uuid5(request.workflow_id, supplier_id),
            currency=request.currency,
            valid_until=observed + timedelta(hours=1),
            lines=tuple(
                RfqLine(
                    line_number=index,
                    component_sku=sku,
                    quantity=quantity,
                    required_by=datetime.fromisoformat(request.required_by[sku]).date(),
                )
                for index, (sku, quantity) in enumerate(sorted(request.shortages.items()), start=1)
            ),
        )
        material = f"rfq:{request.workflow_id}:{supplier_id}:{rfq.rfq_id}"
        return await client.submit_rfq(
            supplier_id,
            rfq,
            idempotency_key=material,
            correlation_id=str(request.correlation_id),
        )

    results = await asyncio.gather(
        *(call(supplier_id) for supplier_id in supplier_ids), return_exceptions=True
    )
    quotes = tuple(
        result for result in results if isinstance(result, SupplierQuote) and result.lines
    )
    missing = tuple(
        supplier_id
        for supplier_id, result in zip(supplier_ids, results, strict=True)
        if not isinstance(result, SupplierQuote) or not result.lines
    )
    if len(quotes) < minimum_valid_quotes:
        raise RuntimeError(
            f"only {len(quotes)} valid supplier quotes; minimum is {minimum_valid_quotes}; "
            f"missing={','.join(missing)}"
        )
    return SupplierQuotesCollected(
        correlation_id=request.correlation_id,
        workflow_id=request.workflow_id,
        sender_agent="sourcing-and-optimization",
        reply_to=str(request.message_id),
        quotes=quotes,
        missing_suppliers=missing,
    )


def normalize_quotes(
    quotes: tuple[SupplierQuote, ...],
    required_by: dict[str, str],
    fx_to_target: dict[str, Decimal],
) -> tuple[NormalizedQuoteLine, ...]:
    normalized: list[NormalizedQuoteLine] = []
    for quote in quotes:
        if quote.currency not in fx_to_target:
            raise ValueError(f"missing FX rate for {quote.currency}")
        rate = fx_to_target[quote.currency]
        for line in quote.lines:
            if line.offered_quantity != line.offered_quantity.to_integral_value():
                raise ValueError(f"optimizer requires integral quantity for {line.component_sku}")
            normalized.append(
                NormalizedQuoteLine(
                    supplier_id=quote.supplier_id,
                    quote_ref=quote.supplier_quote_ref,
                    component_sku=line.component_sku,
                    offered_quantity=int(line.offered_quantity),
                    unit_price_minor=int(
                        (line.unit_price * rate * 100).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP
                        )
                    ),
                    promised_date=line.promised_date,
                    required_by=datetime.fromisoformat(required_by[line.component_sku]).date(),
                    reliability_basis_points=int(quote.reliability_score * 10_000),
                    minimum_order_quantity=int(line.minimum_order_quantity),
                    packaging_multiple=int(line.packaging_multiple),
                )
            )
    return tuple(normalized)


def deterministic_rfq_id(workflow_id: UUID, supplier_id: str) -> UUID:
    return uuid5(workflow_id, supplier_id)
