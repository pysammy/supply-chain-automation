from dataclasses import dataclass

from sqlalchemy.orm import Session
from uagents import Agent

from supplymesh.agents.acknowledgment import (
    AcknowledgmentAgentService,
    build_acknowledgment_protocol,
)
from supplymesh.agents.inventory import build_inventory_protocol
from supplymesh.agents.orchestration import (
    OrchestrationCoordinator,
    build_specialist_response_protocol,
)
from supplymesh.agents.orchestrator import build_orchestrator_agent
from supplymesh.agents.procurement import ProcurementAgentService, build_procurement_protocol
from supplymesh.agents.sourcing import LiveFxResolver, SourcingAgentService, build_sourcing_protocol
from supplymesh.clients.supplier_client import SupplierClient
from supplymesh.persistence.audit_repository import AuditRepository
from supplymesh.persistence.database import get_engine
from supplymesh.persistence.fx_repository import SqlAlchemyFxRepository
from supplymesh.persistence.planning_repository import PlanningRequestRepository
from supplymesh.persistence.procurement_repository import ProcurementRepository
from supplymesh.persistence.side_effect_journal import SqlAlchemySideEffectJournal
from supplymesh.persistence.workflow_repository import WorkflowRepository
from supplymesh.services.fx_service import FrankfurterFxProvider
from supplymesh.workflows.procurement_graph import build_event_transition_graph


@dataclass(frozen=True)
class AgentSeeds:
    orchestrator: str
    inventory: str
    sourcing: str
    procurement: str
    acknowledgment: str

    def validate(self) -> None:
        values = tuple(vars(self).values())
        if any(not value.strip() for value in values):
            raise ValueError("all five agent seeds must be configured")
        if len(set(values)) != 5:
            raise ValueError("each company agent must have a unique seed")


def _specialist(
    name: str, seed: str, description: str, publish: bool, *, mailbox: bool = False
) -> Agent:
    return Agent(
        name=name,
        seed=seed,
        mailbox=mailbox,
        publish_agent_details=publish,
        description=description,
    )


def _session_factory() -> Session:
    return Session(get_engine())


def build_company_agents(
    seeds: AgentSeeds,
    *,
    publish_agent_details: bool = False,
    mailbox: bool = False,
    publish_manifests: bool = True,
    supplier_base_url: str = "http://localhost:8001",
    supplier_api_key: str = "",
    supplier_ids: tuple[str, ...] = (
        "supplier-alpha",
        "supplier-beta",
        "supplier-gamma",
    ),
    http_connect_timeout_seconds: float = 3,
    http_read_timeout_seconds: float = 10,
    http_max_retries: int = 3,
    minimum_valid_quotes: int = 2,
    fx_provider_url: str = "https://api.frankfurter.dev",
    fx_cache_ttl_seconds: int = 3600,
    ack_poll_interval_seconds: float = 2,
    ack_poll_timeout_seconds: float = 45,
) -> tuple[Agent, ...]:
    seeds.validate()
    inventory = _specialist(
        "supplymesh-inventory-bom",
        seeds.inventory,
        "Deterministically expands BOMs, protects reservations, and calculates shortages.",
        publish_agent_details,
        mailbox=mailbox,
    )
    inventory.include(build_inventory_protocol(), publish_manifest=publish_manifests)
    supplier_client = SupplierClient(
        base_url=supplier_base_url,
        api_key=supplier_api_key,
        connect_timeout=http_connect_timeout_seconds,
        read_timeout=http_read_timeout_seconds,
        max_retries=http_max_retries,
    )
    sourcing = _specialist(
        "supplymesh-sourcing-optimization",
        seeds.sourcing,
        "Collects supplier REST quotes and creates constrained OR-Tools procurement plans.",
        publish_agent_details,
        mailbox=mailbox,
    )
    sourcing.include(
        build_sourcing_protocol(
            SourcingAgentService(
                supplier_client,
                supplier_ids,
                minimum_valid_quotes=minimum_valid_quotes,
            ),
            LiveFxResolver(
                FrankfurterFxProvider(fx_provider_url, fx_cache_ttl_seconds),
                SqlAlchemyFxRepository(_session_factory),
            ),
        ),
        publish_manifest=publish_manifests,
    )
    procurement_repository = ProcurementRepository(_session_factory)
    procurement = _specialist(
        "supplymesh-procurement-execution",
        seeds.procurement,
        "Executes approved, version-matched, idempotent X12 purchase and change orders.",
        publish_agent_details,
        mailbox=mailbox,
    )
    procurement.include(
        build_procurement_protocol(
            ProcurementAgentService(
                supplier_client,
                SqlAlchemySideEffectJournal(_session_factory),
                procurement_repository,
                poll_interval_seconds=ack_poll_interval_seconds,
                poll_timeout_seconds=ack_poll_timeout_seconds,
            ),
            recovery_interval_seconds=ack_poll_interval_seconds,
        ),
        publish_manifest=publish_manifests,
    )
    acknowledgment = _specialist(
        "supplymesh-acknowledgment-exception",
        seeds.acknowledgment,
        "Parses X12 acknowledgments and deterministically classifies supplier discrepancies.",
        publish_agent_details,
        mailbox=mailbox,
    )
    acknowledgment.include(
        build_acknowledgment_protocol(
            AcknowledgmentAgentService(procurement_repository, procurement_repository)
        ),
        publish_manifest=publish_manifests,
    )
    audit_repository = AuditRepository(_session_factory)
    planning_repository = PlanningRequestRepository(_session_factory)
    coordinator = OrchestrationCoordinator(
        inventory_address=inventory.address,
        requests=planning_repository,
        workflows=WorkflowRepository(_session_factory, audit_repository),
        sourcing_address=sourcing.address,
        procurement_address=procurement.address,
        acknowledgment_address=acknowledgment.address,
        transition_graph=build_event_transition_graph(),
        audit=audit_repository,
        reservation_store=planning_repository,
    )
    orchestrator = build_orchestrator_agent(
        seed=seeds.orchestrator,
        port=None,
        mailbox=mailbox,
        publish_agent_details=publish_agent_details,
        coordinator=coordinator,
        publish_manifests=publish_manifests,
    )
    orchestrator.include(
        build_specialist_response_protocol(coordinator),
        publish_manifest=publish_manifests,
    )
    return (
        orchestrator,
        inventory,
        sourcing,
        procurement,
        acknowledgment,
    )
