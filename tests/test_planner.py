from __future__ import annotations

import datetime as dt

import pytest

from gda.planner.rules import RulePlanner, plan_question
from gda.semantic import SemanticModel
from tests.conftest import AS_OF

CASES = [
    ("What was revenue by region in 2025?", "revenue; by region; 2025-01-01 to 2025-12-31"),
    ("Top 5 products by revenue in Q4 2025", "revenue; by product; 2025-10-01 to 2025-12-31; ordered by revenue desc; first 5"),
    ("revenue from enterprise customers by channel", "revenue; by channel; segment = Enterprise"),
    ("Which category had the highest revenue last year?", "revenue; by category; 2025-01-01 to 2025-12-31; ordered by revenue desc; first 1"),
    ("monthly orders for hardware in 2025", "orders; by month; category = Hardware; 2025-01-01 to 2025-12-31"),
    ("average order value by segment and channel", "average order value; by segment, channel"),
    ("revenue excluding hardware and software by region", "revenue; by region; category not in Hardware, Software"),
    ('revenue for customer "Asha Patel"', "revenue; customer = Asha Patel"),
    ("list customer emails", "distinct values; by customer email"),
    ("Show me sales in EMEA and APAC by quarter", "revenue; by quarter; region in EMEA, APAC"),
    ("top customers by revenue", "revenue; by customer; ordered by revenue desc; first 10"),
    ("bottom 3 countries by units", "units; by country; ordered by units asc; first 3"),
    ("How many orders were cancelled in 2025?", "cancelled orders; 2025-01-01 to 2025-12-31"),
    ("revenue trend in the first half of 2025", "revenue; by month; 2025-01-01 to 2025-06-30"),
    ("weekly revenue in dec 2025", "revenue; by week; 2025-12-01 to 2025-12-31"),
    ("revenue between 2025-03-01 and 2025-03-31", "revenue; 2025-03-01 to 2025-03-31"),
    ("orders since 2025-12-01", "orders; 2025-12-01 to 2026-01-15"),
    ("revenue last quarter", "revenue; 2025-10-01 to 2025-12-31"),
    ("revenue this quarter", "revenue; 2026-01-01 to 2026-01-15"),
    ("revenue last month", "revenue; 2025-12-01 to 2025-12-31"),
    ("revenue this month", "revenue; 2026-01-01 to 2026-01-15"),
    ("revenue year to date", "revenue; 2026-01-01 to 2026-01-15"),
    ("revenue in the last 30 days", "revenue; 2025-12-17 to 2026-01-15"),
    ("revenue in H2 2025 by region", "revenue; by region; 2025-07-01 to 2025-12-31"),
    ("discount rate by segment yearly", "discount rate; by segment, year"),
    ("revenue by region and month", "revenue; by region, month"),
]


@pytest.mark.parametrize(("question", "expected"), CASES)
def test_rule_planner(model: SemanticModel, question: str, expected: str) -> None:
    result = RulePlanner(model, AS_OF).plan(question)
    assert result.plan is not None, result.unsupported
    assert result.plan.describe() == expected


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("gross margin by category", "no measure"),
        ("revenue growth by region", "growth"),
        ("revenue per employee", "no dimension called 'employee'"),
        ("revenue in 2024 and 2025", "more than one time period"),
        ('revenue "Asha Patel"', "does not follow a field name"),
        ("top products", "no measure"),
        ("revenue between 2025-03-01 and 2025-02-01", "end before start"),
        ("revenue between 2025-02-01 and 2025-02-30", "valid period"),
    ],
)
def test_rule_planner_says_what_it_cannot_do(model: SemanticModel, question: str, reason: str) -> None:
    result = plan_question(model, question, as_of=AS_OF)
    assert result.plan is None and reason in result.unsupported


def test_notes_explain_interpretation(model: SemanticModel) -> None:
    result = plan_question(model, "revenue by region; DROP TABLE marts.fct_order_lines", as_of=AS_OF)
    assert result.plan is not None and "not used: drop, table, marts.fct_order_lines" in result.notes
    result = plan_question(model, "units sold to enterprise customers", as_of=AS_OF)
    assert any("read as context" in n for n in result.notes)
    result = plan_question(model, "how many orders were cancelled", as_of=AS_OF)
    assert result.plan is not None and result.plan.metrics == ["cancelled_orders"]


def test_relative_dates_wrap_years(model: SemanticModel) -> None:
    planner = RulePlanner(model, dt.date(2026, 1, 10))
    plan = planner.plan("revenue last month").plan
    assert plan is not None and plan.time_range is not None and plan.time_range.start == dt.date(2025, 12, 1)
    planner = RulePlanner(model, dt.date(2026, 2, 10))
    plan = planner.plan("revenue last quarter").plan
    assert plan is not None and plan.time_range is not None
    assert (plan.time_range.start, plan.time_range.end) == (dt.date(2025, 10, 1), dt.date(2026, 1, 1))
    plan = planner.plan("orders this year").plan
    assert plan is not None and plan.time_range is not None and plan.time_range.start == dt.date(2026, 1, 1)
    assert RulePlanner(model).as_of == dt.date.today()
