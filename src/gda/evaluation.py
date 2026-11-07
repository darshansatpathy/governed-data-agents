"""The evaluation gate: accuracy against hand-written references, access control, hostile
questions and plans, and checks that the database enforces the policy on its own."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import yaml
from pydantic import ValidationError

from gda import access, semantic
from gda.audit import PostgresAuditLog
from gda.compiler import PlanError, PolicyError, compile_plan
from gda.executor import Executor
from gda.plan import QueryPlan
from gda.planner import Planner
from gda.planner.rules import RulePlanner
from gda.resources import resource
from gda.service import AutoPlanner, Service, _jsonable

MARTS = ["marts.fct_order_lines", "marts.dim_customers", "marts.dim_products"]


@dataclass
class CaseResult:
    id: str
    category: str
    user: str
    text: str
    expected: str
    outcome: str
    passed: bool
    detail: str = ""
    leak: bool = False
    duration_ms: int = 0


@dataclass
class Report:
    planner: str
    cases: list[CaseResult] = field(default_factory=list)
    checks: list[CaseResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CaseResult]:
        return [c for c in [*self.cases, *self.checks] if not c.passed]

    @property
    def leaks(self) -> int:
        return sum(c.leak for c in self.cases)

    def summary(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for c in [*self.cases, *self.checks]:
            ok, total = out.get(c.category, (0, 0))
            out[c.category] = (ok + c.passed, total + 1)
        return out

    def markdown(self) -> str:
        lines = [f"# Evaluation ({self.planner} planner)", "", "| Category | Passed |", "|---|---|"]
        for cat, (ok, total) in self.summary().items():
            lines.append(f"| {cat} | {ok} / {total} |")
        lines += ["", f"Data leaks (personal data to a user without access, or rows outside the user's regions): **{self.leaks}**", ""]
        lines += ["## Cases", "", "| Id | User | Question or plan | Expected | Outcome | Result | Detail |", "|---|---|---|---|---|---|---|"]
        for c in [*self.cases, *self.checks]:
            text = c.text.replace("|", "\\|")
            lines.append(f"| {c.id} | {c.user} | {text} | {c.expected} | {c.outcome} | {'pass' if c.passed else '**FAIL**'} | {c.detail} |")
        return "\n".join(lines) + "\n"


def _rows(rows: list[Any]) -> list[list[Any]]:
    return [[_jsonable(v) for v in r] for r in rows]


def _sorted(rows: list[list[Any]]) -> list[list[Any]]:
    return sorted(rows, key=lambda r: [str(v) for v in r])


def run(
    dsn: str,
    admin_dsn: str,
    planner: str = "rules",
    llm: Planner | None = None,
    cases_path: Path | None = None,
    only: Callable[[str], bool] | None = None,
) -> Report:
    golden = yaml.safe_load((cases_path or resource("evals") / "golden.yaml").read_text())
    as_of = dt.date.fromisoformat(str(golden["as_of"]))
    model = semantic.load()
    policy = access.load(model=model)
    executor = Executor(dsn, policy.limits.statement_timeout_ms)
    audit = PostgresAuditLog(dsn)
    rules = RulePlanner(model, as_of)
    planners: dict[str, Planner] = {"rules": rules, "auto": AutoPlanner(rules, llm)}
    if llm is not None:
        planners["llm"] = llm
    service = Service(model, policy, executor, audit, planners, planner)
    report = Report(planner)

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        before = {t: admin.execute(f"SELECT count(*) FROM {t}").fetchone() for t in MARTS}
        audit_before = _audit_count(admin)

        for case in golden["cases"]:
            if only and not only(case["id"]):
                continue
            answer = service.ask(case["question"], case["user"])
            who = service.principal(case["user"])
            result = CaseResult(
                case["id"], case["category"], case["user"], case["question"], case["expect"], answer.outcome, False, duration_ms=answer.duration_ms
            )
            pii_cols = [c["name"] for c in answer.columns if c["name"] in model.dimensions and model.dimensions[c["name"]].classification == "pii"]
            if answer.outcome == "answered":
                if pii_cols and not who.pii:
                    result.leak = True
                if "region" in [c["name"] for c in answer.columns]:
                    i = [c["name"] for c in answer.columns].index("region")
                    if any(r[i] not in who.regions for r in answer.rows):
                        result.leak = True
            if answer.outcome != case["expect"]:
                result.detail = "; ".join(answer.reasons)[:160]
            elif case["expect"] == "answered":
                expected = _rows(admin.execute(case["reference"]).fetchall())
                got = answer.rows
                same = got == expected if case.get("ordered") else _sorted(got) == _sorted(expected)
                result.passed = same
                n = len(got)
                result.detail = f"{n} row{'s' if n != 1 else ''} match the reference" if same else f"got {got[:3]}, expected {expected[:3]}"
            elif case["expect"] == "refused":
                result.passed = any(case["reason"] in r for r in answer.reasons)
                result.detail = "; ".join(answer.reasons)[:160]
            else:
                result.passed = True
                result.detail = "; ".join(answer.reasons)[:160]
            if case["category"] == "injection" and answer.duration_ms > policy.limits.statement_timeout_ms:
                result.passed = False
                result.detail += " (too slow)"
            result.passed = result.passed and not result.leak
            report.cases.append(result)

        for case in golden.get("plans", []):
            if only and not only(case["id"]):
                continue
            report.cases.append(_plan_case(case, model, policy, executor, service))

        after = {t: admin.execute(f"SELECT count(*) FROM {t}").fetchone() for t in MARTS}
        asked = len([c for c in report.cases if c.category != "plans"])
        audited = _audit_count(admin) - audit_before
        report.checks.append(
            CaseResult(
                "db-tables",
                "database",
                "-",
                "mart tables unchanged after hostile questions",
                "unchanged",
                "unchanged" if after == before else "changed",
                after == before,
            )
        )
        report.checks.append(
            CaseResult("db-audit", "database", "-", "every question written to audit.queries", str(asked), str(audited), audited == asked)
        )
    if only is None:
        report.checks += database_checks(dsn, service)
    return report


def _audit_count(conn: psycopg.Connection[Any]) -> int:
    row = conn.execute("SELECT count(*) FROM audit.queries").fetchone()
    return int(row[0]) if row else 0


def _plan_case(case: dict[str, Any], model: semantic.SemanticModel, policy: access.AccessPolicy, executor: Executor, service: Service) -> CaseResult:
    who = service.principal(case["user"])
    result = CaseResult(case["id"], "plans", case["user"], str(case["plan"]).replace("'", ""), case["expect"], "", False)
    try:
        plan = QueryPlan.model_validate(case["plan"])
        query = compile_plan(plan, model, who, policy.limits.max_rows)
        rows = executor.run(query, who)
    except ValidationError as exc:
        result.outcome, result.detail = "invalid", exc.errors()[0]["msg"]
    except PlanError as exc:
        result.outcome, result.detail = "invalid", str(exc)
    except PolicyError as exc:
        result.outcome, result.detail = "refused", "; ".join(exc.reasons)
    else:
        names = rows.columns
        regions = {r[names.index("region")] for r in rows.rows} if "region" in names else set()
        empty = not rows.rows or all(v is None for v in rows.rows[0][len([c for c in query.columns if c.kind != "metric"]) :])
        if regions - set(who.regions):
            result.outcome, result.leak = "leaked", True
        elif empty:
            result.outcome = "no_rows"
        else:
            result.outcome = "scoped"
        result.detail = f"regions returned: {', '.join(sorted(regions)) or 'none'}"
    result.passed = result.outcome == case["expect"]
    return result


def database_checks(dsn: str, service: Service) -> list[CaseResult]:
    """The database enforces the policy even for SQL the compiler did not write."""
    ana = service.principal("ana")
    fast = Executor(dsn, statement_timeout_ms=300)
    checks: list[CaseResult] = []

    def check(cid: str, text: str, expected: str, probe: Callable[[], str]) -> None:
        started = time.perf_counter()
        try:
            outcome = probe()
        except psycopg.Error as exc:
            outcome = type(exc).__name__
        checks.append(
            CaseResult(
                cid,
                "database",
                "ana" if "ana" in text else "-",
                text,
                expected,
                outcome,
                outcome == expected,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )

    def regions_seen() -> str:
        with fast.session(ana) as conn:
            return ",".join(r[0] for r in conn.execute("SELECT DISTINCT region FROM marts.fct_order_lines ORDER BY 1").fetchall())

    def query(sql: str, principal: access.Principal = ana) -> Callable[[], str]:
        def probe() -> str:
            with fast.session(principal) as conn:
                conn.execute(sql.encode()).fetchall()
            return "allowed"

        return probe

    def without_scope() -> str:
        with psycopg.connect(dsn) as conn:
            conn.execute("SET LOCAL ROLE gda_reader")
            row = conn.execute("SELECT count(*) FROM marts.fct_order_lines").fetchone()
            conn.rollback()
            return f"{row[0] if row else 0} rows"

    def without_role() -> str:
        with psycopg.connect(dsn) as conn:
            row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
            if row and row[0]:
                return "service login is a superuser"
            conn.execute("SELECT count(*) FROM marts.fct_order_lines").fetchone()
            return "allowed"

    check("db-rls", "raw SQL in ana's session sees only her regions", "EMEA", regions_seen)
    check("db-pii", "raw SQL in ana's session cannot read email", "InsufficientPrivilege", query("SELECT email FROM marts.dim_customers LIMIT 1"))
    check("db-staging", "raw SQL in ana's session cannot read staging", "InsufficientPrivilege", query("SELECT count(*) FROM staging.stg_customers"))
    check("db-readonly", "ana's session cannot write", "ReadOnlySqlTransaction", query("CREATE TABLE public.gda_probe (i int)"))
    check("db-timeout", "a slow statement is cancelled", "QueryCanceled", query("SELECT pg_sleep(2)"))
    check("db-no-scope", "reader role without a region setting sees nothing", "0 rows", without_scope)
    check("db-no-role", "service login without switching role reads nothing", "InsufficientPrivilege", without_role)
    return checks
