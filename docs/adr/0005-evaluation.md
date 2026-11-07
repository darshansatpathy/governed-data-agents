# ADR-0005: Evaluate against hand-written references and planted faults

**Status:** accepted

## Context

Comparing an answer with the output of the same compiler proves nothing. A leakage test that
only checks refusals misses rows that should not be there.

## Decision

Every answered case has reference SQL written by hand and run as the warehouse owner, with the
user's scope written out explicitly. Answers must match it exactly. Separately, every answered
case is checked for personal-data columns and for region values outside the user's scope. Hostile
plans are fed to the compiler directly, and raw SQL is run in users' sessions to test the
database on its own. The test suite then removes protections (row-level security, a column
grant) and checks that the gate fails.

## Consequences

- Adding a question means writing its reference query.
- The committed report is regenerated and compared in CI.
