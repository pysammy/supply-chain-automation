from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from supplymesh.domain.sourcing import QuoteLine, RfqRequest, SupplierQuote
from supplymesh.protocols.sourcing import SourcingRequest
from supplymesh.services.sourcing_service import collect_quotes


class FakeGateway:
    def __init__(self, failures: set[str]) -> None:
        self.failures = failures
        self.called: list[str] = []

    async def submit_rfq(
        self,
        supplier_id: str,
        request: RfqRequest,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierQuote:
        del idempotency_key, correlation_id
        self.called.append(supplier_id)
        if supplier_id in self.failures:
            raise httpx.ReadTimeout("simulated timeout")
        line = request.lines[0]
        return SupplierQuote(
            supplier_id=supplier_id,
            supplier_quote_ref=f"Q-{supplier_id}",
            currency="USD",
            valid_until=request.valid_until,
            reliability_score=Decimal("0.9"),
            lines=(
                QuoteLine(
                    line_number=line.line_number,
                    component_sku=line.component_sku,
                    offered_quantity=line.quantity,
                    unit_price=Decimal("10"),
                    promised_date=date(2026, 11, 20),
                    lead_time_days=10,
                    minimum_order_quantity=Decimal("1"),
                ),
            ),
        )


def sourcing_request() -> SourcingRequest:
    return SourcingRequest(
        correlation_id=uuid4(),
        workflow_id=uuid4(),
        sender_agent="orchestrator",
        shortages={"ENGINE-001": 75},
        required_by={"ENGINE-001": "2026-12-01"},
    )


@pytest.mark.asyncio
async def test_partial_supplier_results_proceed_at_threshold() -> None:
    gateway = FakeGateway({"gamma"})
    result = await collect_quotes(
        sourcing_request(),
        ("alpha", "beta", "gamma"),
        gateway,
        minimum_valid_quotes=2,
        now=datetime(2026, 10, 16, tzinfo=UTC),
    )
    assert len(result.quotes) == 2
    assert result.missing_suppliers == ("gamma",)
    assert set(gateway.called) == {"alpha", "beta", "gamma"}


@pytest.mark.asyncio
async def test_all_supplier_failures_pause_safely() -> None:
    with pytest.raises(RuntimeError, match="only 0 valid supplier quotes"):
        await collect_quotes(
            sourcing_request(),
            ("alpha", "beta", "gamma"),
            FakeGateway({"alpha", "beta", "gamma"}),
            minimum_valid_quotes=2,
            now=datetime(2026, 10, 16, tzinfo=UTC),
        )
