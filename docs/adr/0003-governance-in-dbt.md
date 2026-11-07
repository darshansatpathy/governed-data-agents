# ADR-0003: Grants and policies are part of each dbt model

**Status:** accepted

## Context

dbt rebuilds tables on every run. Grants and row-level security policies applied by a separate
script disappear on the next build, silently.

## Decision

Apply grants and policies in a post-hook macro, `govern()`, declared on each mart with its region
column and PII columns. Add a dbt test that fails the build if a mart with a region column has no
row-level security or the non-PII role can read a PII column.

## Consequences

- Governance travels with the model definition and is reviewed with it.
- A new mart without `govern()` fails `dbt build` if it has a region column.
