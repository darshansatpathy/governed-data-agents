"""The question pipeline: plan, authorize, compile, execute in a governed session, explain, audit."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import psycopg

from gda.access import AccessPolicy, Principal, principal
from gda.answer import summarize
from gda.audit import AuditLog, AuditRecord
from gda.compiler import PlanError, PolicyError, compile_plan
from gda.executor import Executor
from gda.planner import Planner, PlanResult
from gda.semantic import SemanticModel

Outcome = Literal["answered", "refused", "unsupported", "error"]
MAX_QUESTION = 1000


@dataclass
class Answer:
    outcome: Outcome
    user_id: str
    question: str
    planner: str
    text: str
    columns: list[dict[str, str]] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    plan: dict[str, Any] | None = None
    sql: str | None = None
    params: list[Any] | None = None
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AutoPlanner:
    """The rule planner first; the LLM planner only for questions the rules do not understand."""

    name = "auto"

    def __init__(self, rules: Planner, llm: Planner | None) -> None:
        self.rules = rules
        self.llm = llm

    def plan(self, question: str, principal: Principal) -> PlanResult:
        first = self.rules.plan(question, principal)
        if first.plan is not None or self.llm is None:
            return first
        second = self.llm.plan(question, principal)
        second.notes.insert(0, f"rule planner: {first.unsupported}")
        return second


class Service:
    def __init__(
        self,
        model: SemanticModel,
        policy: AccessPolicy,
        executor: Executor,
        audit: AuditLog,
        planners: dict[str, Planner],
        default_planner: str = "rules",
    ) -> None:
        self.model = model
        self.policy = policy
        self.executor = executor
        self.audit = audit
        self.planners = planners
        self.default_planner = default_planner

    def principal(self, user_id: str) -> Principal:
        return principal(self.policy, self.model, user_id)

    def ask(self, question: str, user_id: str, planner: str | None = None) -> Answer:
        started = time.perf_counter()
        who = self.principal(user_id)
        name = planner or self.default_planner
        answer = Answer("unsupported", user_id, question, name, "")
        if name not in self.planners:
            answer.outcome, answer.text = "error", f"unknown planner {name}"
        elif not question.strip() or len(question) > MAX_QUESTION:
            answer.text = f"questions must be between 1 and {MAX_QUESTION} characters"
        else:
            self._answer(answer, question, who, self.planners[name])
        answer.duration_ms = int((time.perf_counter() - started) * 1000)
        self.audit.write(
            AuditRecord(
                user_id,
                question[:MAX_QUESTION],
                answer.planner,
                answer.outcome,
                answer.reasons,
                answer.plan,
                answer.sql,
                answer.params,
                len(answer.rows) if answer.outcome == "answered" else None,
                answer.duration_ms,
            )
        )
        return answer

    def _answer(self, answer: Answer, question: str, who: Principal, planner: Planner) -> None:
        planned = planner.plan(question, who)
        answer.planner = planned.planner
        answer.notes = planned.notes
        if planned.plan is None:
            answer.text = f"I can't answer that: {planned.unsupported}."
            answer.reasons = [planned.unsupported]
            return
        plan = planned.plan
        answer.plan = plan.model_dump(mode="json", exclude_none=True)
        try:
            query = compile_plan(plan, self.model, who, self.policy.limits.max_rows)
        except PolicyError as exc:
            answer.outcome = "refused"
            answer.reasons = exc.reasons
            answer.text = "I can't answer that for you: " + "; ".join(exc.reasons) + "."
            return
        except PlanError as exc:
            answer.reasons = [str(exc)]
            answer.text = f"I can't answer that: {exc}."
            return
        answer.sql, answer.params = query.sql, [_jsonable(p) for p in query.params]
        try:
            result = self.executor.run(query, who)
        except psycopg.Error as exc:
            answer.outcome = "error"
            answer.reasons = [f"database error: {type(exc).__name__}"]
            answer.text = "The query failed in the database."
            return
        answer.outcome = "answered"
        answer.columns = [{"name": c.name, "kind": c.kind, "format": c.format} for c in query.columns]
        answer.rows = [[_jsonable(v) for v in row] for row in result.rows]
        answer.truncated = result.truncated
        answer.text = summarize(plan, query, result, self.model, who)
        if planned.notes:
            answer.text += "\nNotes: " + "; ".join(planned.notes)


def _jsonable(value: Any) -> Any:
    from datetime import date
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value
