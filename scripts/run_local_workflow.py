"""Run an approval-driven SupplyMesh workflow through the local uAgents Bureau.

This is a CLI verification harness, not a production manager or user interface. It
answers the Interactive Cards deterministically so both the happy path and the
counterproposal path can be exercised locally.
"""

import argparse
import asyncio
import json
import secrets
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from pydantic import SecretStr
from uagents import Agent, Bureau, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)

from apps.agent_host.settings import get_settings
from supplymesh.agents.factory import AgentSeeds, build_company_agents


def _required_secret(value: SecretStr | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"{name} must be configured in .env")
    secret = value.get_secret_value()
    if not secret:
        raise RuntimeError(f"{name} must be configured in .env")
    return secret


def _selection_message(value: dict[str, object]) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(UTC),
        content=[TextContent(text=json.dumps(value, separators=(",", ":"), sort_keys=True))],
    )


def _action(payload: dict[str, Any], action: str) -> dict[str, object]:
    for candidate in payload.get("actions", []):
        value = candidate.get("value", {})
        if value.get("action") == action:
            return cast(dict[str, object], value)
    raise RuntimeError(f"card does not offer expected action {action!r}")


def _reply_for_card(payload: dict[str, Any]) -> dict[str, object] | None:
    title = payload.get("title")
    if title == "Manufacturing demand":
        return {
            "action": "submit_demand",
            "values": {
                "product_sku": "MOTO-URBANX-2026",
                "quantity": 50,
                "required_by": "2026-12-15",
                "priority": "high",
                "currency": "USD",
            },
        }
    if title == "Procurement plans":
        split = next(
            (item for item in payload["items"] if item["title"].lower() == "split"),
            payload["items"][0],
        )
        return cast(dict[str, object], split["action"]["value"])
    if isinstance(title, str) and title.startswith("Approve ") and title.endswith(" plan"):
        return _action(payload, "approve_plan")
    if title == "Approve and send purchase orders":
        return _action(payload, "approve_and_send_purchase_orders")
    if title == "Supplier acknowledgment exception":
        return _action(payload, "counter")
    if title == "Supplier counterproposal":
        target = payload["target"]
        return {
            "action": "review_counterproposal",
            "target_id": target["id"],
            "target_version": target["version"],
            "values": {},
        }
    if title == "Approve supplier counterproposal":
        return _action(payload, "approve_counterproposal")
    return None


def _configure_scenario(base_url: str, admin_key: str, scenario: str) -> None:
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/v1/demo/scenario/{scenario}",
        headers={"X-API-Key": admin_key},
        timeout=10,
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=("happy_path", "multi_conflict_counter_accepted"),
        default="happy_path",
    )
    args = parser.parse_args()
    settings = get_settings()
    supplier_api_key = _required_secret(settings.supplier_api_key, "SUPPLIER_API_KEY")
    supplier_admin_key = _required_secret(settings.supplier_admin_api_key, "SUPPLIER_ADMIN_API_KEY")
    _configure_scenario(settings.supplier_simulator_base_url, supplier_admin_key, args.scenario)

    company_agents = build_company_agents(
        AgentSeeds(
            _required_secret(settings.orchestrator_agent_seed, "ORCHESTRATOR_AGENT_SEED"),
            _required_secret(settings.inventory_agent_seed, "INVENTORY_AGENT_SEED"),
            _required_secret(settings.sourcing_agent_seed, "SOURCING_AGENT_SEED"),
            _required_secret(settings.procurement_agent_seed, "PROCUREMENT_AGENT_SEED"),
            _required_secret(settings.acknowledgment_agent_seed, "ACKNOWLEDGMENT_AGENT_SEED"),
        ),
        mailbox=False,
        publish_agent_details=False,
        publish_manifests=False,
        supplier_base_url=settings.supplier_simulator_base_url,
        supplier_api_key=supplier_api_key,
        http_connect_timeout_seconds=settings.http_connect_timeout_seconds,
        http_read_timeout_seconds=settings.http_read_timeout_seconds,
        http_max_retries=settings.http_max_retries,
        minimum_valid_quotes=settings.min_valid_quotes,
        fx_provider_url=settings.fx_provider_url,
        fx_cache_ttl_seconds=settings.fx_cache_ttl_seconds,
        ack_poll_interval_seconds=settings.ack_poll_interval_seconds,
        ack_poll_timeout_seconds=settings.ack_poll_timeout_seconds,
    )
    orchestrator = company_agents[0]
    manager = Agent(
        name="supplymesh-local-verification-manager",
        seed=secrets.token_urlsafe(32),
        mailbox=False,
        publish_agent_details=False,
    )
    protocol = Protocol(spec=chat_protocol_spec)
    bureau: Bureau | None = None

    def stop_bureau() -> None:
        if bureau is not None and bureau._server.server is not None:
            bureau._server.server.should_exit = True

    @manager.on_event("startup")  # type: ignore[untyped-decorator]
    async def start(ctx: Context) -> None:
        await ctx.send(
            orchestrator.address,
            ChatMessage(
                timestamp=datetime.now(UTC),
                content=[TextContent(text="Start the local SupplyMesh verification workflow.")],
            ),
        )

    @protocol.on_message(ChatMessage)  # type: ignore[untyped-decorator]
    async def handle_card(ctx: Context, sender: str, message: ChatMessage) -> None:
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=message.msg_id))
        for content in message.content:
            if not isinstance(content, MetadataContent):
                continue
            if "card_payload" not in content.metadata:
                continue
            payload = json.loads(content.metadata["card_payload"])
            title = payload.get("title", "unknown")
            print(f"LOCAL_WORKFLOW_CARD={title}", flush=True)
            if title == "Procurement complete":
                print(
                    f"LOCAL_WORKFLOW_COMPLETE={payload['workflow_id']} scenario={args.scenario}",
                    flush=True,
                )
                if bureau is not None:
                    asyncio.get_running_loop().call_later(1.0, stop_bureau)
                continue
            reply = _reply_for_card(payload)
            if reply is not None:
                await ctx.send(sender, _selection_message(reply))

    @protocol.on_message(ChatAcknowledgement)  # type: ignore[untyped-decorator]
    async def handle_acknowledgement(
        ctx: Context, sender: str, message: ChatAcknowledgement
    ) -> None:
        del ctx, sender, message

    manager.include(protocol, publish_manifest=False)
    bureau = Bureau(
        agents=[*company_agents, manager],
        port=settings.agent_host_port,
        network="testnet",
    )
    bureau.run()


if __name__ == "__main__":
    main()
