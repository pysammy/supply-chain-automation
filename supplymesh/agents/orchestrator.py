from datetime import UTC, datetime

from pydantic import ValidationError
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)

from supplymesh.agents.connectivity import ConnectivityError, FetchConnectivityCoordinator
from supplymesh.agents.transport import InventoryShortageTransport, PlanningFailureTransport
from supplymesh.cards.demand import DemandIntake, demand_intake_card
from supplymesh.cards.renderer import parse_card_selection
from supplymesh.persistence.planning_repository import PlanningInputError


def _chat_message(text: str, metadata: dict[str, str] | None = None) -> ChatMessage:
    content: list[TextContent | MetadataContent] = [TextContent(text=text)]
    if metadata is not None:
        content.append(MetadataContent(metadata=metadata))
    return ChatMessage(timestamp=datetime.now(UTC), content=content)


def _text_values(message: ChatMessage) -> tuple[str, ...]:
    return tuple(item.text for item in message.content if isinstance(item, TextContent))


def build_chat_protocol(coordinator: FetchConnectivityCoordinator | None = None) -> Protocol:
    protocol = Protocol(spec=chat_protocol_spec)

    @protocol.on_message(ChatMessage)
    async def handle_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        card = demand_intake_card()
        selections = tuple(
            selection
            for text in _text_values(msg)
            if (selection := parse_card_selection(text)) is not None
        )
        selection = selections[-1] if selections else None
        if (
            coordinator is not None
            and selection is not None
            and selection.action != "submit_demand"
            and hasattr(coordinator, "handle_selection")
        ):
            try:
                handled = await coordinator.handle_selection(
                    ctx, conversation_id=sender, selection=selection
                )
                if handled:
                    return
            except (ValidationError, ValueError) as exc:
                await ctx.send(
                    sender,
                    _chat_message(f"I could not apply that workflow decision: {exc}"),
                )
                return
            except Exception:
                ctx.logger.exception("failed to apply workflow decision")
                await ctx.send(
                    sender,
                    _chat_message(
                        "I could not apply that workflow decision safely. Please retry or ask an "
                        "operator for help."
                    ),
                )
                return
        if (
            coordinator is not None
            and selection is not None
            and selection.action == "submit_demand"
        ):
            try:
                intake = DemandIntake.model_validate(selection.values)
            except ValidationError:
                await ctx.send(
                    sender,
                    _chat_message(
                        "I could not start inventory planning because one or more demand fields "
                        "were invalid. Please review the form and submit it again.",
                        card.metadata(),
                    ),
                )
                return
            try:
                await coordinator.start_inventory(
                    ctx,
                    conversation_id=sender,
                    chat_message_id=msg.msg_id,
                    intake=intake,
                )
            except (PlanningInputError, ConnectivityError) as exc:
                await ctx.send(
                    sender,
                    _chat_message(f"I could not start inventory planning: {exc}", card.metadata()),
                )
            except Exception:
                ctx.logger.exception("failed to start inventory planning")
                await ctx.send(
                    sender,
                    _chat_message(
                        "I could not start inventory planning because the planning data service "
                        "is unavailable. Please try again or ask an operator for help.",
                        card.metadata(),
                    ),
                )
            return
        await ctx.send(
            sender,
            _chat_message(
                "I can protect existing reservations, calculate shortages, source parts, and "
                "prepare purchase orders. Confirm the demand to begin.",
                card.metadata(),
            ),
        )

    @protocol.on_message(ChatAcknowledgement)
    async def handle_acknowledgement(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        ctx.logger.info(
            "chat response acknowledged", extra={"message_id": str(msg.acknowledged_msg_id)}
        )

    return protocol


def build_planning_response_protocol(coordinator: FetchConnectivityCoordinator) -> Protocol:
    protocol = Protocol(name="SupplyMeshOrchestratorPlanning", version="1.0.0")

    @protocol.on_message(InventoryShortageTransport)
    async def handle_inventory_report(
        ctx: Context, sender: str, msg: InventoryShortageTransport
    ) -> None:
        del sender
        try:
            await coordinator.complete_inventory(ctx, msg.to_domain())
        except (ValidationError, ValueError):
            ctx.logger.warning("rejected invalid or stale inventory planning response")

    @protocol.on_message(PlanningFailureTransport)
    async def handle_inventory_failure(
        ctx: Context, sender: str, msg: PlanningFailureTransport
    ) -> None:
        del sender
        try:
            await coordinator.fail_inventory(ctx, msg)
        except ValueError:
            ctx.logger.warning("rejected invalid or stale inventory planning failure")

    return protocol


def build_orchestrator_agent(
    *,
    seed: str,
    port: int | None = 8000,
    mailbox: bool = True,
    publish_agent_details: bool = False,
    coordinator: FetchConnectivityCoordinator | None = None,
    publish_manifests: bool = True,
) -> Agent:
    if not seed.strip():
        raise ValueError("ORCHESTRATOR_AGENT_SEED must be configured")
    agent = Agent(
        name="supplymesh-manufacturing-orchestrator",
        seed=seed,
        port=port,
        mailbox=mailbox,
        publish_agent_details=publish_agent_details,
        description=(
            "Manufacturing procurement orchestration for BOM, inventory shortages, supplier RFQs, "
            "human-approved purchase orders, and X12 acknowledgments."
        ),
    )
    agent.include(build_chat_protocol(coordinator), publish_manifest=publish_manifests)
    if coordinator is not None:
        agent.include(
            build_planning_response_protocol(coordinator), publish_manifest=publish_manifests
        )
    return agent
