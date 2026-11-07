# ADR-0001: Planners produce plans, never SQL

**Status:** accepted

## Context

The common design for natural-language analytics asks a language model to write SQL. Its SQL
uses whatever definitions it infers, can reach any table the connection can reach, and can be
changed by text in the question. Reviewing it before execution means parsing arbitrary SQL.

## Decision

Planners, rule-based or LLM, produce a `QueryPlan`: names from the semantic layer and values. The
plan schema forbids unknown keys. A compiler that knows the semantic layer is the only thing that
writes SQL, and it writes one parameterized SELECT.

## Consequences

- Business definitions live once, in the semantic layer, and every answer uses them.
- The access check works on names, before SQL exists, and gives readable reasons.
- Questions the semantic layer cannot express are declined. Adding a measure or a dimension is a
  reviewed change to `semantic/model.yaml`, not something a model improvises.
