"""Plain-language summaries of results that state the definitions and the scope used."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from gda.access import Principal
from gda.compiler import CompiledQuery
from gda.executor import Result
from gda.plan import QueryPlan
from gda.semantic import SemanticModel


def fmt(value: Any, kind: str) -> str:
    if value is None:
        return "none"
    if kind == "currency":
        return f"{Decimal(value):,.2f}"
    if kind == "percent":
        return f"{Decimal(value) * 100:.2f}%"
    if kind == "integer":
        return f"{int(value):,}"
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:,}"
    return str(value)


def summarize(plan: QueryPlan, query: CompiledQuery, result: Result, model: SemanticModel, principal: Principal) -> str:
    metric_cols = [c for c in query.columns if c.kind == "metric"]
    group_cols = [c for c in query.columns if c.kind != "metric"]
    lines: list[str] = []
    title = plan.describe()
    if not result.rows or (not group_cols and all(v is None for v in result.rows[0])):
        lines.append(f"No data visible to you matches: {title}.")
    elif not group_cols:
        values = ", ".join(f"{c.name.replace('_', ' ')} {fmt(result.rows[0][i], c.format)}" for i, c in enumerate(metric_cols))
        lines.append(f"{title}: {values}.")
    else:
        lines.append(f"{title}: {len(result.rows)} row{'s' if len(result.rows) != 1 else ''}{' (truncated)' if result.truncated else ''}.")
        if metric_cols:
            first = result.rows[0]
            label = ", ".join(fmt(first[i], c.format) for i, c in enumerate(group_cols))
            m = metric_cols[0]
            word = "First" if plan.order or plan.grain else "Largest"
            lines.append(f"{word}: {label} with {m.name.replace('_', ' ')} {fmt(first[len(group_cols)], m.format)}.")
    for c in metric_cols:
        lines.append(f"Definition of {c.name.replace('_', ' ')}: {model.description(c.name)}")
    all_regions = model.dimensions["region"].values if "region" in model.dimensions else []
    if set(principal.regions) != set(all_regions):
        lines.append(f"Scope: rows are limited to {', '.join(principal.regions)} by row-level security.")
    return "\n".join(lines)
