"""The evaluation gate passes on a correct setup and fails when the protection it measures is removed."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from gda.evaluation import run


@contextmanager
def admin_change(admin: str, apply: str, undo: str) -> Iterator[None]:
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(apply.encode())
        try:
            yield
        finally:
            conn.execute(undo.encode())


def test_gate_passes(dsns: tuple[str, str]) -> None:
    report = run(*dsns)
    assert not report.failures and report.leaks == 0
    assert report.summary()["accuracy"] == (33, 33)
    assert "| acc-01 | gina |" in report.markdown()


def test_gate_catches_missing_row_level_security(dsns: tuple[str, str]) -> None:
    app, admin = dsns
    with admin_change(
        admin, "ALTER TABLE marts.fct_order_lines DISABLE ROW LEVEL SECURITY", "ALTER TABLE marts.fct_order_lines ENABLE ROW LEVEL SECURITY"
    ):
        report = run(app, admin, only=lambda cid: cid in {"acl-09", "plan-05", "acc-25"})
    assert report.leaks >= 2
    assert {c.id for c in report.failures} >= {"acl-09", "plan-05", "acc-25"}


def test_gate_catches_a_pii_grant(dsns: tuple[str, str]) -> None:
    app, admin = dsns
    with admin_change(
        admin, "GRANT SELECT (email) ON marts.dim_customers TO gda_reader", "REVOKE SELECT (email) ON marts.dim_customers FROM gda_reader"
    ):
        report = run(app, admin)
    assert [c.id for c in report.failures] == ["db-pii"]
