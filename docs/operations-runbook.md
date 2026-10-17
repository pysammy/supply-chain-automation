# Operations runbook

## Startup verification

1. Load secrets from the deployment secret manager; never write agent seeds or API keys into the
   repository or image.
2. Run `uv run alembic upgrade head` and `uv run python scripts/setup_checkpoints.py`.
3. Start the supplier simulator, then the five-agent host.
4. Require HTTP 200 from the supplier `/ready` endpoint and the diagnostic agent-host `/ready`
   endpoint before routing traffic.
5. Run one synthetic happy-path demand and confirm that all five stable agent addresses match the
   deployment inventory.

## Supplier transaction recovery

`supplier_transactions.status` has the following operational meanings:

- `SUBMITTING`: intent is durable; the supplier command may need idempotent reconciliation.
- `PROCESSING`: supplier accepted the command; acknowledgment polling is incomplete.
- `READY_TO_DELIVER`: the 855/865 is persisted but its typed agent messages are not fully delivered.
- `DELIVERED`: both submission and acknowledgment/response messages were sent successfully.

The Procurement Agent periodically claims incomplete rows using an expiring database lease.
Restarting an instance is safe: stable supplier idempotency keys and stable agent message IDs make
replay non-duplicating. Investigate rows whose lease remains expired for more than two polling
intervals; do not manually resubmit a PO with a new key.

## Reservation recovery and compensation

New demand reserves locally available inventory as `TENTATIVE`. Successful supply confirmation
changes those rows to `COMMITTED`. Cancellation changes only tentative rows to `RELEASED`, leaving
pre-existing committed reservations untouched. Re-running either transition is idempotent.

## Incident checks

- Correlate logs by `workflow_id`, `correlation_id`, `message_id`, supplier, and PO version.
- Inspect `agent_message_log`, `audit_events`, `edi_documents`, and `supplier_transactions` before
  retrying or compensating anything.
- Treat malformed or stale EDI as input evidence; never edit the raw stored document.
- Use the non-production scenario controls only outside production. They return 404 in production.
- If all suppliers fail, leave the workflow in operator-review state and retry with the same
  workflow and idempotency identities after the dependency recovers.

## Shutdown and rollback

Stop new ingress before stopping agents. Allow active handlers to finish, then stop the agent host,
supplier simulator, and database in that order. Application rollback must retain the database and
audit records. Validate every migration downgrade on a disposable database; do not downgrade a
production schema without a backup and a reviewed data-compatibility plan.
