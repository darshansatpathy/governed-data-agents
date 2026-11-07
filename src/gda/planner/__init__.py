"""Planners turn a question into a QueryPlan. They never produce SQL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from gda.access import Principal
from gda.plan import QueryPlan


@dataclass
class PlanResult:
    planner: str
    plan: QueryPlan | None = None
    unsupported: str = ""
    notes: list[str] = field(default_factory=list)


class Planner(Protocol):
    name: str

    def plan(self, question: str, principal: Principal) -> PlanResult: ...
