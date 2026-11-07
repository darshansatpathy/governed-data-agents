from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from gda import access
from gda.api import create_app
from gda.app import build
from gda.audit import MemoryAuditLog, PostgresAuditLog
from gda.cli import main
from gda.compiler import compile_plan
from gda.executor import Executor
from gda.plan import QueryPlan
from gda.planner.rules import RulePlanner
from gda.semantic import SemanticModel
from gda.service import Service
from tests.conftest import AS_OF

ENV = {"GDA_AS_OF": AS_OF.isoformat(), "GDA_PLANNER": "rules"}


def test_service_outcomes_and_audit(dsns: tuple[str, str], model: SemanticModel, policy: access.AccessPolicy) -> None:
    app, _ = dsns
    log = MemoryAuditLog()
    svc = Service(model, policy, Executor(app), log, {"rules": RulePlanner(model, AS_OF)})
    answered = svc.ask("revenue by region in 2025", "ana")
    assert answered.outcome == "answered" and answered.rows[0][0] == "EMEA" and "row-level security" in answered.text
    assert answered.sql and answered.params == [["cancelled"], "2025-01-01", "2026-01-01"]
    assert svc.ask("revenue by customer", "ana").outcome == "refused"
    assert svc.ask("gross margin", "ana").outcome == "unsupported"
    assert svc.ask("how many orders were cancelled", "gina").text.endswith("used cancelled_orders")
    assert svc.ask(" ", "ana").outcome == "unsupported"
    assert svc.ask("revenue", "ana", planner="psychic").outcome == "error"
    total = svc.ask("total revenue in 2025", "gina")
    assert total.outcome == "answered" and total.text.startswith("revenue; 2025-01-01 to 2025-12-31: revenue ")
    assert "Scope" not in total.text
    assert [r.outcome for r in log.records] == ["answered", "refused", "unsupported", "answered", "unsupported", "error", "answered"]
    assert log.records[0].row_count == 1 and log.records[1].row_count is None


def test_truncation_and_database_errors(dsns: tuple[str, str], model: SemanticModel, policy: access.AccessPolicy) -> None:
    app, _ = dsns
    gina = access.principal(policy, model, "gina")
    query = compile_plan(QueryPlan(metrics=["revenue"], dimensions=["product"]), model, gina, max_rows=5)
    result = Executor(app).run(query, gina)
    assert len(result.rows) == 5 and result.truncated
    broken = Service(
        model,
        policy,
        Executor(make_conninfo(app, dbname="gda_missing_database")),
        MemoryAuditLog(),
        {"rules": RulePlanner(model, AS_OF)},
    )
    assert broken.ask("revenue", "gina").outcome == "error"


def test_api(dsns: tuple[str, str]) -> None:
    app_dsn, _ = dsns
    env = {**ENV, "GDA_API_TOKENS": "t-ana=ana,t-x=mallory", "GDA_TRUSTED_USER_HEADER": "X-Forwarded-User"}
    client = TestClient(create_app(build(app_dsn, env), env))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/ask", json={"question": "revenue"}).status_code == 401
    assert client.post("/ask", json={"question": "revenue"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/ask", json={"question": "revenue"}, headers={"Authorization": "Bearer t-x"}).status_code == 403
    r = client.post("/ask", json={"question": "revenue by region"}, headers={"Authorization": "Bearer t-ana"})
    assert r.status_code == 200 and r.json()["rows"][0][0] == "EMEA"
    r = client.post("/ask", json={"question": "revenue", "planner": "llm"}, headers={"X-Forwarded-User": "raj"})
    assert r.status_code == 400
    assert "customer_email" in client.get("/catalog", headers={"X-Forwarded-User": "raj"}).json()["dimensions"]
    audit = client.get("/audit?limit=5", headers={"Authorization": "Bearer t-ana"}).json()
    assert audit and {row["user_id"] for row in audit} == {"ana"}
    no_readback = TestClient(create_app(build(app_dsn, env, audit=MemoryAuditLog()), env))
    assert no_readback.get("/audit", headers={"Authorization": "Bearer t-ana"}).status_code == 501


def test_build_requires_a_dsn() -> None:
    with pytest.raises(SystemExit):
        build(None, {})


def test_cli(dsns: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    app, admin = dsns
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    assert main(["ask", "top 2 products by revenue in 2025", "--user", "ana", "--dsn", app]) == 0
    assert "product | revenue" in capsys.readouterr().out
    assert main(["ask", "revenue by customer", "--user", "ana", "--dsn", app, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused"
    assert main(["plan", "revenue by segment in 2025", "--user", "gina", "--as-of", "2026-01-15"]) == 0
    assert "sql: SELECT" in capsys.readouterr().out
    assert main(["plan", "revenue by customer", "--user", "gina"]) == 2
    assert main(["plan", "gross margin", "--user", "gina"]) == 2
    capsys.readouterr()
    assert main(["catalog", "--user", "pat"]) == 0
    assert "discounts" not in json.loads(capsys.readouterr().out)["measures"]
    assert main(["audit", "--dsn", app, "--user", "ana", "--limit", "1"]) == 0
    monkeypatch.delenv("GDA_DSN", raising=False)
    assert main(["audit"]) == 2
    assert main(["eval", "--dsn", app]) == 2
    assert main(["eval", "--dsn", app, "--admin-dsn", admin, "--planner", "llm"]) == 2
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["eval", "--dsn", app, "--admin-dsn", admin, "--report", str(tmp_path / "r.md"), "--check"]) == 0
    assert "Data leaks" in summary.read_text() and (tmp_path / "r.md").exists()


def test_postgres_audit_log(dsns: tuple[str, str]) -> None:
    app, _ = dsns
    log = PostgresAuditLog(app)
    rows = log.recent(3)
    assert len(rows) <= 3
    with psycopg.connect(app) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM audit.queries")
