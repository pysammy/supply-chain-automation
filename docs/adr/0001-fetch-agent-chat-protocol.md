# ADR 0001: Current Fetch.ai Chat and Card Interfaces

- Status: Accepted
- Date: 2026-10-16

## Context

The implementation plan describes Agent Chat Protocol and Interactive Card metadata but does
not pin current package versions. Fetch.ai's official documentation currently identifies local
`uagents>=0.25.5`, `uagents_core.contrib.protocols.chat`, and AgentChatProtocol version `0.3.0`.
It also documents card payloads as JSON strings inside `MetadataContent`, with a 64 KB limit.

## Decision

Use `uagents>=0.25.5,<0.26`, import chat contracts from
`uagents_core.contrib.protocols.chat`, publish `chat_protocol_spec`, and validate generated card
metadata against the documented 64 KB limit. Agent seeds remain environment-only.

## Consequences

Older examples that redefine chat models or import experimental chat modules are not followed.
Live Agentverse registration and ASI:One rendering remain manual deployment checks because they
require credentials and a public/reachable agent runtime.

## Official references

- https://innovationlab.fetch.ai/resources/docs/agent-communication/agent-chat-protocol
- https://innovationlab.fetch.ai/resources/docs/next/examples/dynamic-ui-on-asi1/news-card-agent
- https://github.com/fetchai/uAgents/releases
