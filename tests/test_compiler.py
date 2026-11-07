from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from gda import access
from gda.compiler import PlanError, PolicyError, compile_plan
from gda.plan import QueryPlan
from gda.semantic import SemanticModel


def compile_for(model: SemanticModel, policy: access.AccessPolicy, user: str, plan: dict[str, Any], max_rows: int = 500) -> Any:
    return compile_plan(QueryPlan.model_validate(plan), model, access.principal(policy, model, user), max_rows)


def test_sql_is_parameterized_and_joins_only_what_it_needs(model: SemanticModel, policy: access.AccessPolicy) -> None:
    q = compile_for(
        model,
        policy,
        "gina",
        {"metrics": ["revenue"], "dimensions": ["region"], "filters": [{"field": "channel", "op": "eq", "values": ["x' OR 1=1"]}]},
    )
    assert "x' OR 1=1" not in q.sql and q.params[-1] == ["x' OR 1=1"]
    assert "dim_customers" not in q.sql and "dim_products" not in q.sql
    assert q.sql.endswith("LIMIT 501") and q.row_cap == 500
    q = compile_for(model, policy, "gina", {"metrics": ["revenue"], "dimensions": ["category", "segment"], "limit": 3})
    assert q.entities == ["order_lines", "products", "customers"] and q.sql.endswith("LIMIT 3")


def test_dimension_only_plans_are_distinct(model: SemanticModel, policy: access.AccessPolicy) -> None:
    q = compile_for(model, policy, "raj", {"dimensions": ["customer_email"], "time_range": {"start": "2025-01-01", "end": "2025-02-01"}})
    assert q.sql.startswith('SELECT DISTINCT "t0"."email"') and "GROUP BY" not in q.sql
    assert "fct_order_lines" in q.sql


def test_policy_refusals_collect_every_reason(model: SemanticModel, policy: access.AccessPolicy) -> None:
    with pytest.raises(PolicyError) as err:
        compile_for(model, policy, "pat", {"metrics": ["discounts"], "dimensions": ["segment", "customer"]})
    assert len(err.value.reasons) == 3


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ({"metrics": ["profit"]}, "not in the semantic layer"),
        ({"metrics": ["revenue"], "order": {"by": "region"}}, "cannot order by"),
        ({"metrics": ["orders"], "filters": [{"field": "status", "op": "eq", "values": ["cancelled"]}]}, "leaves nothing"),
        ({"metrics": ["cancelled_orders"], "filters": [{"field": "status", "op": "in", "values": ["open"]}]}, "leaves nothing"),
    ],
)
def test_plan_errors(model: SemanticModel, policy: access.AccessPolicy, plan: dict[str, Any], message: str) -> None:
    with pytest.raises(PlanError, match=message):
        compile_for(model, policy, "gina", plan)


def test_unsupported_grain_is_rejected(model: SemanticModel, policy: access.AccessPolicy) -> None:
    narrowed = model.model_copy(update={"time_dimension": model.time_dimension.model_copy(update={"grains": ["month"]})})
    with pytest.raises(PlanError, match="grain"):
        compile_plan(QueryPlan(metrics=["revenue"], grain="week"), narrowed, access.principal(policy, model, "gina"), 10)


@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"metrics": ["revenue", "revenue"]},
        {"metrics": ["revenue"], "sql": "select 1"},
        {"metrics": ["revenue"], "time_range": {"start": "2025-02-01", "end": "2025-01-01"}},
        {"metrics": ["revenue"], "filters": [{"field": "channel", "op": "eq", "values": ["x" * 201]}]},
        {"metrics": ["revenue"], "limit": 0},
        {"metrics": ["revenue"], "filters": [{"field": "channel", "op": "like", "values": ["%"]}]},
    ],
)
def test_plan_schema_rejects(plan: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(plan)


def test_describe() -> None:
    plan = QueryPlan.model_validate(
        {
            "metrics": ["revenue"],
            "filters": [{"field": "region", "op": "not_in", "values": ["EMEA"]}],
            "order": {"by": "revenue", "direction": "asc"},
            "limit": 2,
        }
    )
    assert plan.describe() == "revenue; region not in EMEA; ordered by revenue asc; first 2"
