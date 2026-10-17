from uagents import Bureau

from apps.agent_host.settings import get_settings
from supplymesh.agents.factory import AgentSeeds, build_company_agents
from supplymesh.observability.logging import configure_logging
from supplymesh.observability.sentry import initialize_sentry


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    initialize_sentry(
        dsn=settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
    )
    configured = (
        settings.orchestrator_agent_seed,
        settings.inventory_agent_seed,
        settings.sourcing_agent_seed,
        settings.procurement_agent_seed,
        settings.acknowledgment_agent_seed,
    )
    if any(value is None for value in configured):
        raise RuntimeError("all five company agent seeds are required to start the Agentverse host")
    values = [value.get_secret_value() for value in configured if value is not None]
    agents = build_company_agents(
        AgentSeeds(*values),
        publish_agent_details=settings.publish_agent_details,
        mailbox=settings.agent_mailbox,
        supplier_base_url=settings.supplier_simulator_base_url,
        supplier_api_key=(
            settings.supplier_api_key.get_secret_value() if settings.supplier_api_key else ""
        ),
        http_connect_timeout_seconds=settings.http_connect_timeout_seconds,
        http_read_timeout_seconds=settings.http_read_timeout_seconds,
        http_max_retries=settings.http_max_retries,
        minimum_valid_quotes=settings.min_valid_quotes,
        fx_provider_url=settings.fx_provider_url,
        fx_cache_ttl_seconds=settings.fx_cache_ttl_seconds,
        ack_poll_interval_seconds=settings.ack_poll_interval_seconds,
        ack_poll_timeout_seconds=settings.ack_poll_timeout_seconds,
    )
    Bureau(agents=list(agents), port=settings.agent_host_port, network="testnet").run()


if __name__ == "__main__":
    main()
