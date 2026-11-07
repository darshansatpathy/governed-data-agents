"""Run compiled queries inside a governed PostgreSQL session.

Each query runs in its own read-only transaction that:

1. switches to gda_reader, or gda_reader_pii for users entitled to personal data. The login
   role can switch to them but does not inherit their privileges, so a path that forgets to
   switch reads nothing;
2. sets gda.regions, which the row-level security policies on the marts read. When it is not
   set they return no rows;
3. sets a statement timeout.

The settings are transaction-local and end with the transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

from gda.access import Principal
from gda.compiler import CompiledQuery

READER = "gda_reader"
READER_PII = "gda_reader_pii"


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool


class Executor:
    def __init__(self, dsn: str, statement_timeout_ms: int = 5000) -> None:
        self.dsn = dsn
        self.statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def session(self, principal: Principal) -> Iterator[psycopg.Connection[Any]]:
        """A governed transaction for one principal. Always rolled back."""
        with psycopg.connect(self.dsn, autocommit=False) as conn:
            try:
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(READER_PII if principal.pii else READER)))
                conn.execute("SELECT set_config('gda.regions', %s, true)", [",".join(principal.regions)])
                conn.execute("SELECT set_config('statement_timeout', %s, true)", [str(self.statement_timeout_ms)])
                yield conn
            finally:
                conn.rollback()

    def run(self, query: CompiledQuery, principal: Principal) -> Result:
        with self.session(principal) as conn:
            cur = conn.execute(query.sql.encode(), query.params)
            rows = cur.fetchall()
            names = [d.name for d in cur.description or []]
        return Result(names, rows[: query.row_cap], len(rows) > query.row_cap)
