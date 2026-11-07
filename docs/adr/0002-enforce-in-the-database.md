# ADR-0002: Enforce the policy in the database as well

**Status:** accepted

## Context

A grants check in the application is only as good as the code around it. A compiler bug, a new
code path or a mistake in a join can read more than intended.

## Decision

Enforce regions with PostgreSQL row-level security and personal data with column privileges, on
reader roles the service switches to per transaction. Grant those roles to the service's login
without inheritance, so not switching reads nothing, and make the policy return no rows when the
scope setting is missing.

## Consequences

- The evaluation can test the database on its own, with raw SQL in a user's session.
- The compiler adds no region filters; the scope cannot be widened by a plan.
- Requires PostgreSQL 16 or later for `GRANT ... WITH INHERIT FALSE, SET TRUE`.
