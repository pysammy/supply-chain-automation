from uagents import Model
from uagents_core.contrib.protocols.chat import ChatMessage, chat_protocol_spec

from supplymesh.agents.factory import AgentSeeds, build_company_agents
from supplymesh.agents.orchestrator import build_chat_protocol, build_orchestrator_agent
from supplymesh.agents.transport import (
    AcknowledgmentComparisonTransport,
    AcknowledgmentReceivedTransport,
    ChangeResponseReceivedTransport,
    CounterproposalExecutionTransport,
    DemandPlanningTransport,
    InventoryShortageTransport,
    ProcurementPlansReadyTransport,
    PurchaseOrderExecutionTransport,
    PurchaseOrderSubmittedTransport,
    SourcingRequestTransport,
)


def test_official_chat_protocol_version_and_manifest() -> None:
    protocol = build_chat_protocol()
    assert chat_protocol_spec.version == "0.3.0"
    assert protocol.version == "0.3.0"
    assert len(protocol.models) == 2


def test_orchestrator_requires_a_stable_seed() -> None:
    try:
        build_orchestrator_agent(seed="")
    except ValueError as exc:
        assert "ORCHESTRATOR_AGENT_SEED" in str(exc)
    else:
        raise AssertionError("empty seed must be rejected")


def test_company_agents_publish_the_connectivity_slice_protocols() -> None:
    agents = build_company_agents(
        AgentSeeds(
            orchestrator="test-orchestrator-connectivity-seed",
            inventory="test-inventory-connectivity-seed",
            sourcing="test-sourcing-connectivity-seed",
            procurement="test-procurement-connectivity-seed",
            acknowledgment="test-acknowledgment-connectivity-seed",
        ),
        publish_manifests=False,
    )
    orchestrator, inventory, sourcing, procurement, acknowledgment = agents
    assert orchestrator.get_message_protocol(Model.build_schema_digest(ChatMessage)) is not None
    assert (
        orchestrator.get_message_protocol(Model.build_schema_digest(InventoryShortageTransport))
        is not None
    )
    for response_type in (
        ProcurementPlansReadyTransport,
        PurchaseOrderSubmittedTransport,
        AcknowledgmentReceivedTransport,
        AcknowledgmentComparisonTransport,
        ChangeResponseReceivedTransport,
    ):
        assert (
            orchestrator.get_message_protocol(Model.build_schema_digest(response_type)) is not None
        )
    assert (
        inventory.get_message_protocol(Model.build_schema_digest(DemandPlanningTransport))
        is not None
    )
    assert (
        sourcing.get_message_protocol(Model.build_schema_digest(SourcingRequestTransport))
        is not None
    )
    assert (
        procurement.get_message_protocol(Model.build_schema_digest(PurchaseOrderExecutionTransport))
        is not None
    )
    assert (
        procurement.get_message_protocol(
            Model.build_schema_digest(CounterproposalExecutionTransport)
        )
        is not None
    )
    assert (
        acknowledgment.get_message_protocol(
            Model.build_schema_digest(AcknowledgmentReceivedTransport)
        )
        is not None
    )
