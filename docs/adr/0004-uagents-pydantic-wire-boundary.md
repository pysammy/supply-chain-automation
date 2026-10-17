# ADR 0004: uAgents Wire Boundary for Pydantic 2 Domain Contracts

- Status: Accepted
- Date: 2026-10-16

## Context

SupplyMesh uses strict Pydantic 2 models for domain and inter-agent application contracts. The
installed official uAgents 0.25.5 runtime builds protocol schema digests from `uagents.Model`,
which is Pydantic-1-compatible. Passing the Pydantic 2 application models directly to
`Protocol.on_message` is therefore not supported.

## Decision

Keep Pydantic 2 as the source of truth for numeric and business validation. At the uAgents
boundary, use small, message-specific `uagents.Model` envelopes containing the versioned message
identity fields and the canonical Pydantic 2 JSON payload. Receivers immediately deserialize and
strictly validate the payload, then verify that its envelope fields exactly match the outer wire
fields before invoking a domain service.

The boundary now covers inventory planning, sourcing and plans, purchase-order execution,
acknowledgments, counterproposals, change responses, workflow status, and bounded specialist
failures. Protocol handlers never calculate inventory, money, optimization, or discrepancy values
themselves.

## Consequences

- Official uAgents protocol manifests and schema digests can be published without weakening the
  Pydantic 2 domain contracts.
- Tampering or disagreement between routing metadata and the validated payload is rejected.
- The boundary adds a small JSON serialization step and must have round-trip tests for every new
  message type.
- Future uAgents releases can replace this adapter if they natively accept the project's Pydantic
  2 contracts; such a change requires another compatibility check and ADR update.

## Official references

- https://innovationlab.fetch.ai/resources/docs/agent-communication/agent-chat-protocol
- https://github.com/fetchai/uAgents
