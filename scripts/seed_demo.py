import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from supplymesh.persistence.database import session_scope
from supplymesh.persistence.orm_models import (
    Bom,
    BomItem,
    DemandOrder,
    InventoryBalance,
    InventoryReservation,
    Product,
)

ROOT = Path(__file__).resolve().parents[1]


def load_json(name: str) -> dict[str, Any]:
    with (ROOT / "data" / name).open(encoding="utf-8") as handle:
        value: dict[str, Any] = json.load(handle)
        return value


def seed() -> None:
    bom_data = load_json("motorcycle_bom.json")
    inventory_data = load_json("initial_inventory.json")
    order_data = load_json("existing_orders.json")
    with session_scope() as session:
        product_data = bom_data["product"]
        product = session.scalar(
            select(Product).where(
                Product.sku == product_data["sku"], Product.version == product_data["version"]
            )
        )
        if product is None:
            product = Product(**product_data, active=True)
            session.add(product)
            session.flush()

        bom = session.scalar(
            select(Bom).where(Bom.product_id == product.id, Bom.version == bom_data["bom_version"])
        )
        if bom is None:
            bom = Bom(
                product_id=product.id,
                version=bom_data["bom_version"],
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                status="ACTIVE",
            )
            session.add(bom)
            session.flush()
            session.add_all(
                BomItem(
                    bom_id=bom.id,
                    component_sku=item["component_sku"],
                    quantity_per=Decimal(item["quantity_per"]),
                    scrap_factor=Decimal(item["scrap_factor"]),
                    unit=item["unit"],
                )
                for item in bom_data["components"]
            )

        for item in inventory_data["balances"]:
            key = {"component_sku": item["component_sku"], "location": inventory_data["location"]}
            if session.get(InventoryBalance, tuple(key.values())) is None:
                session.add(
                    InventoryBalance(
                        **key,
                        on_hand=Decimal(item["on_hand"]),
                        safety_stock=Decimal(item["safety_stock"]),
                        version=1,
                    )
                )

        orders: dict[str, DemandOrder] = {}
        for item in order_data["orders"]:
            order = session.scalar(
                select(DemandOrder).where(DemandOrder.external_ref == item["external_ref"])
            )
            if order is None:
                order = DemandOrder(
                    external_ref=item["external_ref"],
                    product_id=product.id,
                    quantity=Decimal(item["quantity"]),
                    required_by=date.fromisoformat(item["required_by"]),
                    priority=item["priority"],
                    status=item["status"],
                )
                session.add(order)
                session.flush()
            orders[item["external_ref"]] = order

        for item in order_data["reservations"]:
            order = orders[item["external_ref"]]
            existing = session.scalar(
                select(InventoryReservation).where(
                    InventoryReservation.demand_order_id == order.id,
                    InventoryReservation.component_sku == item["component_sku"],
                )
            )
            if existing is None:
                session.add(
                    InventoryReservation(
                        demand_order_id=order.id,
                        component_sku=item["component_sku"],
                        quantity=Decimal(item["quantity"]),
                        priority=order.priority,
                        status="COMMITTED",
                    )
                )


if __name__ == "__main__":
    seed()
