from typing import Literal

from supplymesh.domain.models import (
    BomComponent,
    Demand,
    InventoryAssessment,
    InventoryPosition,
    TentativeReservation,
)
from supplymesh.protocols.common import MessageEnvelope


class DemandPlanningRequest(MessageEnvelope):
    message_type: Literal["DemandPlanningRequest"] = "DemandPlanningRequest"
    demand: Demand
    bom: tuple[BomComponent, ...]
    inventory_positions: tuple[InventoryPosition, ...]


class InventoryShortageReport(MessageEnvelope):
    message_type: Literal["InventoryShortageReport"] = "InventoryShortageReport"
    assessment: InventoryAssessment
    tentative_reservations: tuple[TentativeReservation, ...]
