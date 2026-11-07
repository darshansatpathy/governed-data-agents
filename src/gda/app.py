"""Wiring: build a Service from configuration and the environment."""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Mapping

from gda import access, semantic
from gda.audit import AuditLog, PostgresAuditLog
from gda.executor import Executor
from gda.planner import Planner
from gda.planner.llm import from_env
from gda.planner.rules import RulePlanner
from gda.service import AutoPlanner, Service


def build(dsn: str | None = None, env: Mapping[str, str] | None = None, audit: AuditLog | None = None) -> Service:
    env = dict(os.environ if env is None else env)
    dsn = dsn or env.get("GDA_DSN")
    if not dsn:
        raise SystemExit("set GDA_DSN (or pass --dsn) to the service's PostgreSQL login")
    as_of = dt.date.fromisoformat(env["GDA_AS_OF"]) if env.get("GDA_AS_OF") else None
    model = semantic.load()
    policy = access.load(model=model)
    rules = RulePlanner(model, as_of)
    llm = from_env(model, env, as_of)
    planners: dict[str, Planner] = {"rules": rules, "auto": AutoPlanner(rules, llm)}
    if llm is not None:
        planners["llm"] = llm
    return Service(
        model,
        policy,
        Executor(dsn, policy.limits.statement_timeout_ms),
        audit or PostgresAuditLog(dsn),
        planners,
        env.get("GDA_PLANNER", "auto"),
    )
