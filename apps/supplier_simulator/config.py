from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class CatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_price: Decimal = Field(gt=0)
    available_capacity: Decimal = Field(ge=0)
    lead_time_days: int = Field(ge=0)
    minimum_order_quantity: Decimal = Field(gt=0)
    packaging_multiple: Decimal = Field(default=Decimal("1"), gt=0)


class SupplierProfile(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    supplier_id: str
    name: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reliability_score: Decimal = Field(ge=0, le=1)
    response_delay_ms: int = Field(default=0, ge=0)
    catalog: dict[str, CatalogItem]
    ack_behavior: dict[str, str]
    counter_behavior: dict[str, str]


def load_supplier_profiles(directory: Path | None = None) -> dict[str, SupplierProfile]:
    root = directory or Path(__file__).resolve().parents[2] / "config" / "suppliers"
    profiles: dict[str, SupplierProfile] = {}
    for path in sorted(root.glob("*.yaml")):
        profile = SupplierProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if profile.supplier_id in profiles:
            raise ValueError(f"duplicate supplier_id {profile.supplier_id}")
        profiles[profile.supplier_id] = profile
    if len(profiles) < 3:
        raise ValueError("at least three supplier profiles are required")
    return profiles
