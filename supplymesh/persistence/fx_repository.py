from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymesh.domain.fx import FxRate
from supplymesh.persistence.orm_models import FxRateRecord


class SqlAlchemyFxRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def save(self, rate: FxRate) -> None:
        with self.session_factory() as session, session.begin():
            session.merge(
                FxRateRecord(
                    base_currency=rate.base_currency,
                    quote_currency=rate.quote_currency,
                    rate=rate.rate,
                    provider=rate.provider,
                    observed_at=rate.observed_at,
                    expires_at=rate.expires_at,
                )
            )

    def latest(self, base_currency: str, quote_currency: str) -> FxRate | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(FxRateRecord)
                .where(
                    FxRateRecord.base_currency == base_currency,
                    FxRateRecord.quote_currency == quote_currency,
                )
                .order_by(FxRateRecord.observed_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            return FxRate(
                base_currency=row.base_currency,
                quote_currency=row.quote_currency,
                rate=row.rate,
                provider=row.provider,
                provider_date=row.observed_at.date(),
                observed_at=row.observed_at,
                expires_at=row.expires_at,
                source="cached",
            )
