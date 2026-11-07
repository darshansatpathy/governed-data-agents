"""Command line: ask, plan, catalog, audit, eval, serve."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from gda import __version__, access, semantic
from gda.catalog import visible_catalog
from gda.compiler import PlanError, PolicyError, compile_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gda", description="Governed natural-language analytics.")
    parser.add_argument("--version", action="version", version=f"gda {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ask", help="answer a question as a user")
    p.add_argument("question")
    p.add_argument("--user", required=True)
    p.add_argument("--planner", choices=["rules", "llm", "auto"])
    p.add_argument("--dsn")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("plan", help="show the plan and SQL for a question without running it")
    p.add_argument("question")
    p.add_argument("--user", required=True)
    p.add_argument("--as-of", type=dt.date.fromisoformat)

    p = sub.add_parser("catalog", help="what a user may ask about")
    p.add_argument("--user", required=True)

    p = sub.add_parser("audit", help="recent questions from the audit log")
    p.add_argument("--user")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dsn")

    p = sub.add_parser("eval", help="run the evaluation gate")
    p.add_argument("--dsn", default=os.environ.get("GDA_DSN"), help="the service's login (gda_app)")
    p.add_argument("--admin-dsn", default=os.environ.get("GDA_ADMIN_DSN"), help="the warehouse owner, for reference queries")
    p.add_argument("--planner", choices=["rules", "llm", "auto"], default="rules")
    p.add_argument("--report", type=Path)
    p.add_argument("--check", action="store_true", help="exit 1 on any failure or leak")

    p = sub.add_parser("serve", help="run the HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    return int(COMMANDS[args.command](args))


def _ask(args: argparse.Namespace) -> int:
    from gda.app import build

    answer = build(args.dsn).ask(args.question, args.user, args.planner)
    if args.json:
        print(json.dumps(answer.to_dict(), indent=2, default=str))
    else:
        print(answer.text)
        if answer.rows:
            print()
            print(" | ".join(c["name"] for c in answer.columns))
            for row in answer.rows[:50]:
                print(" | ".join("" if v is None else str(v) for v in row))
    return 0 if answer.outcome == "answered" else 2


def _plan(args: argparse.Namespace) -> int:
    from gda.planner.rules import RulePlanner

    model = semantic.load()
    policy = access.load(model=model)
    who = access.principal(policy, model, args.user)
    result = RulePlanner(model, args.as_of).plan(args.question, who)
    if result.plan is None:
        print(f"unsupported: {result.unsupported}")
        return 2
    print("plan:", result.plan.describe())
    for note in result.notes:
        print("note:", note)
    try:
        query = compile_plan(result.plan, model, who, policy.limits.max_rows)
    except (PolicyError, PlanError) as exc:
        print(f"{'refused' if isinstance(exc, PolicyError) else 'invalid'}: {exc}")
        return 2
    print("sql:", query.sql)
    print("params:", json.dumps(query.params, default=str))
    return 0


def _catalog(args: argparse.Namespace) -> int:
    model = semantic.load()
    who = access.principal(access.load(model=model), model, args.user)
    print(json.dumps(visible_catalog(model, who), indent=2))
    return 0


def _audit(args: argparse.Namespace) -> int:
    from gda.audit import PostgresAuditLog

    dsn = args.dsn or os.environ.get("GDA_DSN")
    if not dsn:
        print("set GDA_DSN or pass --dsn", file=sys.stderr)
        return 2
    for row in PostgresAuditLog(dsn).recent(args.limit, args.user):
        print(json.dumps(row, default=str))
    return 0


def _eval(args: argparse.Namespace) -> int:
    from gda.evaluation import run
    from gda.planner.llm import from_env

    if not args.dsn or not args.admin_dsn:
        print("set --dsn and --admin-dsn (or GDA_DSN and GDA_ADMIN_DSN)", file=sys.stderr)
        return 2
    llm = None
    if args.planner != "rules":
        llm = from_env(semantic.load(), dict(os.environ), None)
        if llm is None:
            print("the llm planner needs GDA_LLM_BASE_URL and GDA_LLM_MODEL", file=sys.stderr)
            return 2
    report = run(args.dsn, args.admin_dsn, args.planner, llm)
    text = report.markdown()
    if args.report:
        args.report.write_text(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(text)
    for cat, (ok, total) in report.summary().items():
        print(f"{cat:12} {ok}/{total}")
    print(f"leaks        {report.leaks}")
    for c in report.failures:
        print(f"FAIL {c.id} {c.user}: {c.text} -> {c.outcome} ({c.detail})")
    return 1 if args.check and (report.failures or report.leaks) else 0


def _serve(args: argparse.Namespace) -> int:  # pragma: no cover - runs a server
    import uvicorn

    uvicorn.run("gda.api:main_app", factory=True, host=args.host, port=args.port)
    return 0


COMMANDS = {"ask": _ask, "plan": _plan, "catalog": _catalog, "audit": _audit, "eval": _eval, "serve": _serve}
