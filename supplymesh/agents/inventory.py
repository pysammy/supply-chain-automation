from pydantic import ValidationError
from uagents import Context, Protocol

from supplymesh.agents.transport import (
    DemandPlanningTransport,
    InventoryShortageTransport,
    PlanningFailureTransport,
)
from supplymesh.protocols.planning import DemandPlanningRequest, InventoryShortageReport
from supplymesh.services.inventory_service import assess_inventory, tentative_reservations


class InventoryAgentService:
    """Pure typed handler used by the Inventory uAgent transport adapter."""

    name = "inventory-and-bom"

    def handle(self, request: DemandPlanningRequest) -> InventoryShortageReport:
        assessment = assess_inventory(request.demand, request.bom, request.inventory_positions)
        reservations = tentative_reservations(request.demand, assessment)
        return InventoryShortageReport(
            correlation_id=request.correlation_id,
            workflow_id=request.workflow_id,
            sender_agent=self.name,
            reply_to=str(request.message_id),
            assessment=assessment,
            tentative_reservations=reservations,
        )


def build_inventory_protocol(service: InventoryAgentService | None = None) -> Protocol:
    handler = service or InventoryAgentService()
    protocol = Protocol(name="SupplyMeshInventoryPlanning", version="1.0.0")

    @protocol.on_message(
        DemandPlanningTransport,
        replies={InventoryShortageTransport, PlanningFailureTransport},
    )
    async def handle_planning_request(
        ctx: Context, sender: str, msg: DemandPlanningTransport
    ) -> None:
        try:
            request = msg.to_domain()
            report = handler.handle(request)
        except (ValidationError, ValueError):
            await ctx.send(
                sender,
                PlanningFailureTransport(
                    correlation_id=msg.correlation_id,
                    workflow_id=msg.workflow_id,
                    reply_to=msg.message_id,
                    code="INVALID_PLANNING_REQUEST",
                    message="The inventory planning request was invalid.",
                ),
            )
            return
        await ctx.send(sender, InventoryShortageTransport.from_domain(report))

    return protocol
