from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import httpx

from supplymesh.domain.fx import FxRate


class FxProvider(Protocol):
    async def fetch(self, base_currency: str, quote_currency: str, *, now: datetime) -> FxRate: ...


class FxRepository(Protocol):
    def save(self, rate: FxRate) -> None: ...

    def latest(self, base_currency: str, quote_currency: str) -> FxRate | None: ...


class FrankfurterFxProvider:
    def __init__(self, base_url: str, ttl_seconds: int = 3600) -> None:
        self.base_url = base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    async def fetch(self, base_currency: str, quote_currency: str, *, now: datetime) -> FxRate:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=5) as client:
            response = await client.get(
                f"/v2/rate/{base_currency.lower()}/{quote_currency.lower()}"
            )
            response.raise_for_status()
            payload = response.json()
        return FxRate(
            base_currency=base_currency.upper(),
            quote_currency=quote_currency.upper(),
            rate=Decimal(str(payload["rate"])),
            provider="frankfurter-v2",
            provider_date=date.fromisoformat(payload["date"]),
            observed_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            source="live",
        )


async def get_fx_rate(
    base_currency: str,
    quote_currency: str,
    provider: FxProvider,
    repository: FxRepository,
    *,
    now: datetime | None = None,
) -> FxRate:
    observed = now or datetime.now(UTC)
    if base_currency == quote_currency:
        return FxRate(
            base_currency=base_currency,
            quote_currency=quote_currency,
            rate=Decimal("1"),
            provider="identity",
            provider_date=observed.date(),
            observed_at=observed,
            expires_at=observed + timedelta(days=1),
            source="live",
        )
    try:
        rate = await provider.fetch(base_currency, quote_currency, now=observed)
        repository.save(rate)
        return rate
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        cached = repository.latest(base_currency, quote_currency)
        if cached is None or cached.expires_at < observed:
            raise RuntimeError(
                "live FX failed and no non-expired cached rate is available"
            ) from None
        return cached.model_copy(update={"source": "cached"})
