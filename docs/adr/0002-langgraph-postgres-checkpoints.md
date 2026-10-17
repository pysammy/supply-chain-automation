# ADR 0002: LangGraph PostgreSQL Checkpoint Ownership

- Status: Accepted
- Date: 2026-10-16

## Context

Current LangGraph documentation provides production PostgreSQL persistence through the separate
`langgraph-checkpoint-postgres` package. Its `PostgresSaver.setup()` function manages the
framework's checkpoint tables and requires an autocommit connection. Reimplementing those tables
in Alembic would duplicate framework-owned migrations and couple this project to private schema.

## Decision

Use `langgraph-checkpoint-postgres>=3,<4`. Application tables remain Alembic-managed. At service
provisioning time, run `PostgresSaver.setup()` once outside an explicit transaction, then compile
the graph with that saver. Use the workflow ID as LangGraph's `thread_id`.

## Consequences

Alembic validates all SupplyMesh-owned tables; LangGraph validates and evolves its checkpoint
tables. Restart tests must close one saver/graph and resume through a newly opened saver/graph.

## Official references

- https://langchain-ai.github.io/langgraph/reference/checkpoints/
- https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres
