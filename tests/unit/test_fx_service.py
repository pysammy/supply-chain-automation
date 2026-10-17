from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from supplymesh.domain.fx import FxRate
from supplymesh.services.fx_service import get_fx_rate


class Repository:
    def __init__(self, cached: FxRate | None = None) -> None:
        self.cached = cached

    def save(self, rate: FxRate) -> None:
        self.cached = rate

    def latest(self, base_currency: str, quote_currency: str) -> FxRate | None:
        del base_currency, quote_currency
        return self.cached


class Provider:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def fetch(self, base_currency: str, quote_currency: str, *, now: datetime) -> FxRate:
        if self.fail:
            raise httpx.ConnectError("offline")
        return FxRate(
            base_currency=base_currency,
            quote_currency=quote_currency,
            rate=Decimal("1.1"),
            provider="test",
            provider_date=date(2026, 10, 16),
            observed_at=now,
            expires_at=now + timedelta(hours=1),
            source="live",
        )


@pytest.mark.asyncio
async def test_fx_live_value_is_cached_and_nonexpired_cache_is_fallback() -> None:
    now = datetime(2026, 10, 16, tzinfo=UTC)
    repository = Repository()
    live = await get_fx_rate("EUR", "USD", Provider(), repository, now=now)
    assert live.source == "live"
    cached = await get_fx_rate(
        "EUR", "USD", Provider(fail=True), repository, now=now + timedelta(minutes=5)
    )
    assert cached.source == "cached"
    assert cached.rate == Decimal("1.1")


@pytest.mark.asyncio
async def test_expired_fx_cache_fails_instead_of_fabricating_rate() -> None:
    now = datetime(2026, 10, 16, tzinfo=UTC)
    expired = FxRate(
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.1"),
        provider="test",
        provider_date=date(2026, 10, 15),
        observed_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
        source="cached",
    )
    with pytest.raises(RuntimeError, match="no non-expired cached rate"):
        await get_fx_rate("EUR", "USD", Provider(fail=True), Repository(expired), now=now)
