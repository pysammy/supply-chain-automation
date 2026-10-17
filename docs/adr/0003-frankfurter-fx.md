# ADR 0003: Frankfurter v2 for Optional Live FX

- Status: Accepted
- Date: 2026-10-16

## Context

One demo supplier quotes EUR, so deterministic plan comparison needs a replaceable FX source. The
current Frankfurter API provides keyless v2 currency-pair rates from central-bank sources at
`https://api.frankfurter.dev/v2/rate/{base}/{quote}`. Its rates update on a daily rather than
real-time trading cadence.

## Decision

Use Frankfurter v2 as the default configurable provider. Convert the returned decimal rate in
code, store successful observations and expiry in PostgreSQL, and fall back only to a non-expired
cache record. Always disclose whether a rate is live or cached. Never fabricate a rate.

## Consequences

This is suitable for procurement-plan comparison demonstrations, not execution-time treasury or
regulated valuation. Provider failure with no valid cache pauses sourcing with an actionable error.

## Official reference

- https://frankfurter.dev/
