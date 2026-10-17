from collections.abc import Callable
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from supplymesh.cards.demand import DemandIntake
from supplymesh.domain.enums import Priority
from supplymesh.domain.models import BomComponent, Demand, InventoryPosition, TentativeReservation
from supplymesh.persistence.orm_models import (
    Bom,
    BomItem,
    DemandOrder,
    InboundSupply,
    InventoryBalance,
    InventoryReservation,
    Product,
)
from supplymesh.protocols.planning import DemandPlanningRequest


class PlanningInputError(ValueError):
    """A bounded, user-correctable planning input failure."""


class PlanningRequestRepository:
    """Build and persist deterministic planning input from seeded PostgreSQL data."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def create_request(
        self,
        intake: DemandIntake,
        *,
        workflow_id: UUID,
        correlation_id: UUID,
        as_of: datetime | None = None,
    ) -> DemandPlanningRequest:
        effective_at = as_of or datetime.now(UTC)
        with self.session_factory() as session, session.begin():
            product = session.scalar(
                select(Product)
                .where(Product.sku == intake.product_sku, Product.active.is_(True))
                .order_by(Product.version.desc())
                .limit(1)
            )
            if product is None:
                raise PlanningInputError(f"unknown active product: {intake.product_sku}")

            bom = session.scalar(
                select(Bom)
                .where(
                    Bom.product_id == product.id,
                    Bom.status == "ACTIVE",
                    Bom.effective_from <= effective_at,
                    or_(Bom.effective_to.is_(None), Bom.effective_to > effective_at),
                )
                .order_by(Bom.version.desc())
                .limit(1)
            )
            if bom is None:
                raise PlanningInputError(f"no effective BOM for product: {intake.product_sku}")

            items = tuple(
                session.scalars(
                    select(BomItem).where(BomItem.bom_id == bom.id).order_by(BomItem.component_sku)
                )
            )
            if not items:
                raise PlanningInputError(f"effective BOM has no components: {intake.product_sku}")

            external_ref = f"ASI-{workflow_id}"
            demand = DemandOrder(
                id=workflow_id,
                external_ref=external_ref,
                product_id=product.id,
                quantity=Decimal(intake.quantity),
                required_by=intake.required_by,
                priority=intake.priority,
                status="INVENTORY_PLANNING",
            )
            session.add(demand)

            component_skus = tuple(item.component_sku for item in items)
            balance_rows = session.execute(
                select(
                    InventoryBalance.component_sku,
                    func.sum(InventoryBalance.on_hand),
                    func.sum(InventoryBalance.safety_stock),
                )
                .where(InventoryBalance.component_sku.in_(component_skus))
                .group_by(InventoryBalance.component_sku)
            ).all()
            reservation_rows = session.execute(
                select(
                    InventoryReservation.component_sku,
                    func.sum(InventoryReservation.quantity),
                )
                .where(
                    InventoryReservation.component_sku.in_(component_skus),
                    InventoryReservation.status.in_(("COMMITTED", "TENTATIVE")),
                )
                .group_by(InventoryReservation.component_sku)
            ).all()
            required_by_end = datetime.combine(intake.required_by, time.max, tzinfo=UTC)
            inbound_rows = session.execute(
                select(InboundSupply.component_sku, func.sum(InboundSupply.quantity))
                .where(
                    InboundSupply.component_sku.in_(component_skus),
                    InboundSupply.status.in_(("CONFIRMED", "ACCEPTED")),
                    InboundSupply.expected_at <= required_by_end,
                )
                .group_by(InboundSupply.component_sku)
            ).all()

            balances = {sku: (on_hand, safety_stock) for sku, on_hand, safety_stock in balance_rows}
            reservations = {sku: quantity for sku, quantity in reservation_rows}
            inbound = {sku: quantity for sku, quantity in inbound_rows}

            components = tuple(
                BomComponent(
                    component_sku=item.component_sku,
                    quantity_per=item.quantity_per,
                    scrap_factor=item.scrap_factor,
                    unit=item.unit,
                )
                for item in items
            )
            positions = tuple(
                self._position(item, balances, reservations, inbound) for item in items
            )

        return DemandPlanningRequest(
            correlation_id=correlation_id,
            workflow_id=workflow_id,
            sender_agent="manufacturing-orchestrator",
            demand=Demand(
                demand_order_id=workflow_id,
                product_sku=intake.product_sku,
                quantity=Decimal(intake.quantity),
                required_by=intake.required_by,
                priority=Priority(intake.priority),
            ),
            bom=components,
            inventory_positions=positions,
        )

    def save_tentative_reservations(
        self,
        workflow_id: UUID,
        reservations: tuple[TentativeReservation, ...],
    ) -> None:
        with self.session_factory() as session, session.begin():
            demand = session.get(DemandOrder, workflow_id)
            if demand is None:
                raise ValueError("tentative reservations reference an unknown demand")
            for reservation in reservations:
                reservation_id = uuid5(workflow_id, f"reservation:{reservation.component_sku}")
                existing = session.get(InventoryReservation, reservation_id)
                if existing is not None:
                    if (
                        existing.component_sku != reservation.component_sku
                        or existing.quantity != reservation.quantity
                        or existing.priority != reservation.priority.value
                    ):
                        raise ValueError("reservation identity already has different content")
                    continue
                session.add(
                    InventoryReservation(
                        id=reservation_id,
                        demand_order_id=workflow_id,
                        component_sku=reservation.component_sku,
                        quantity=reservation.quantity,
                        priority=reservation.priority.value,
                        status="TENTATIVE",
                    )
                )
            if demand.status not in {"COMPLETED", "CANCELLED"}:
                demand.status = "INVENTORY_RESERVED"

    def transition_reservations(
        self, workflow_id: UUID, *, status: Literal["COMMITTED", "RELEASED"]
    ) -> int:
        with self.session_factory() as session, session.begin():
            demand = session.get(DemandOrder, workflow_id)
            if demand is None:
                raise ValueError("reservation transition references an unknown demand")
            reservations = tuple(
                session.scalars(
                    select(InventoryReservation).where(
                        InventoryReservation.demand_order_id == workflow_id,
                        InventoryReservation.status == "TENTATIVE",
                    )
                )
            )
            for reservation in reservations:
                reservation.status = status
            demand.status = "COMPLETED" if status == "COMMITTED" else "CANCELLED"
            return len(reservations)

    @staticmethod
    def _position(
        item: BomItem,
        balances: dict[str, tuple[Decimal, Decimal]],
        reservations: dict[str, Decimal],
        inbound: dict[str, Decimal],
    ) -> InventoryPosition:
        on_hand, safety_stock = balances.get(item.component_sku, (Decimal("0"), Decimal("0")))
        return InventoryPosition(
            component_sku=item.component_sku,
            on_hand=on_hand,
            confirmed_inbound=inbound.get(item.component_sku, Decimal("0")),
            existing_reservations=reservations.get(item.component_sku, Decimal("0")),
            safety_stock=safety_stock,
            unit=item.unit,
        )
