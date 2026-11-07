# ADR-0004: The rule planner first, the LLM as a fallback

**Status:** accepted

## Context

Most questions asked of a sales warehouse follow a small number of shapes. A deterministic
planner answers them predictably, with no model cost and no variance, and its failures are easy
to read. Open-ended phrasing needs a model.

## Decision

`auto` tries the rule planner and calls the LLM planner only when the rules cannot plan the
question. The rule planner declines what it does not understand instead of guessing, and
reports words it did not use.

## Consequences

- CI evaluates the rule planner exactly. The LLM planner is evaluated with the same command when
  a model is configured, and its safety does not depend on its accuracy: every plan goes through
  the same check, compiler and database session.
