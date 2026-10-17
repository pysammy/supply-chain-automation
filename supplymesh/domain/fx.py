from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from supplymesh.domain.models import DomainModel


class FxRate(DomainModel):
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: Decimal = Field(gt=0)
    provider: str
    provider_date: date
    observed_at: datetime
    expires_at: datetime
    source: Literal["live", "cached"]
