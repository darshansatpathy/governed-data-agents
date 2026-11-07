"""Every question is recorded, answered or not, before any data is returned."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg


@dataclass
class AuditRecord:
    user_id: str
    question: str
    planner: str
    outcome: str
    reasons: list[str] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    sql: str | None = None
    params: list[Any] | None = None
    row_count: int | None = None
    duration_ms: int | None = None


class AuditLog(Protocol):
    def write(self, record: AuditRecord) -> None: ...


class PostgresAuditLog:
    """Appends to audit.queries with the service's own login, outside the governed session."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def write(self, record: AuditRecord) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO audit.queries (user_id, question, planner, outcome, reasons, plan, sql, params, row_count, duration_ms)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    record.user_id,
                    record.question,
                    record.planner,
                    record.outcome,
                    json.dumps(record.reasons),
                    json.dumps(record.plan) if record.plan is not None else None,
                    record.sql,
                    json.dumps(record.params, default=str) if record.params is not None else None,
                    record.row_count,
                    record.duration_ms,
                ],
            )

    def recent(self, limit: int = 20, user_id: str | None = None) -> list[dict[str, Any]]:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cur = conn.execute(
                "SELECT at, user_id, question, planner, outcome, reasons, row_count, duration_ms FROM audit.queries"
                " WHERE %s::text IS NULL OR user_id = %s ORDER BY id DESC LIMIT %s",
                [user_id, user_id, limit],
            )
            names = [d.name for d in cur.description or []]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


class MemoryAuditLog:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self.records.append(record)
