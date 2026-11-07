"""Compile a QueryPlan into one parameterized SELECT, after checking it against the user's grants.

Identifiers come only from the semantic layer and are quoted; every value is a bound parameter.
Regions are not filtered here: PostgreSQL row-level security does that, so a compiler bug
cannot widen what a user sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from psycopg import sql

from gda.access import Principal
from gda.plan import QueryPlan
from gda.semantic import Measure, SemanticModel

AGGREGATES = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}
ROUNDING = {"currency": 2, "percent": 4, "number": 4}


class PlanError(ValueError):
    """The plan refers to something that does not exist or cannot be combined."""


class PolicyError(PermissionError):
    """The plan asks for something the user is not allowed to see."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class Column:
    name: str
    kind: str  # "dimension", "time" or "metric"
    format: str


@dataclass
class CompiledQuery:
    sql: str
    params: list[Any]
    columns: list[Column]
    row_cap: int
    entities: list[str] = field(default_factory=list)


def authorize(plan: QueryPlan, model: SemanticModel, principal: Principal) -> None:
    unknown = [n for n in plan.metrics if not model.is_measure_or_metric(n)]
    unknown += [n for n in plan.dimensions if n not in model.dimensions]
    unknown += [f.field for f in plan.filters if f.field not in model.dimensions]
    if plan.order and plan.order.by not in {*plan.metrics, *plan.dimensions, *([plan.grain] if plan.grain else [])}:
        raise PlanError(f"cannot order by {plan.order.by}: it is not in the result")
    if unknown:
        raise PlanError("not in the semantic layer: " + ", ".join(sorted(set(unknown))))
    if plan.grain and plan.grain not in model.time_dimension.grains:
        raise PlanError(f"unsupported time grain {plan.grain}")
    for name in plan.metrics:
        for base in model.base_measures(name) if model.is_measure_or_metric(name) else []:
            for cond in model.measures[base].where:
                for f in plan.filters:
                    if f.field == cond.field and f.op != "not_in":
                        kept = set(f.values) - set(cond.values) if cond.op == "not_in" else set(f.values) & set(cond.values)
                        if not kept:
                            raise PlanError(
                                f"{name} is defined on {cond.field} {cond.op.replace('_', ' ')} {', '.join(cond.values)}, "
                                f"so filtering it to {', '.join(f.values)} leaves nothing"
                            )
    reasons = []
    for name in dict.fromkeys(plan.fields()):
        reason = principal.may_use(name, model)
        if reason:
            reasons.append(reason)
    if reasons:
        raise PolicyError(reasons)


def compile_plan(plan: QueryPlan, model: SemanticModel, principal: Principal, max_rows: int) -> CompiledQuery:
    authorize(plan, model, principal)
    td = model.time_dimension

    needed = [model.entity_of(n) for n in plan.fields()]
    if plan.grain or plan.time_range:
        needed.append(td.entity)
    base = model.entity_of(plan.metrics[0]) if plan.metrics else needed[0]
    aliases = {base: "t0"}
    joins: list[sql.Composable] = []
    for entity in dict.fromkeys(needed):
        for j in model.join_path(base, entity):
            for left, right, lcol, rcol in ((j.from_, j.to, j.keys[0], j.keys[1]), (j.to, j.from_, j.keys[1], j.keys[0])):
                if left in aliases and right not in aliases:
                    aliases[right] = f"t{len(aliases)}"
                    joins.append(
                        sql.SQL("JOIN {} AS {} ON {}.{} = {}.{}").format(
                            _table(model, right),
                            sql.Identifier(aliases[right]),
                            sql.Identifier(aliases[right]),
                            sql.Identifier(rcol),
                            sql.Identifier(aliases[left]),
                            sql.Identifier(lcol),
                        )
                    )

    def col(entity: str, column: str) -> sql.Composable:
        return sql.SQL("{}.{}").format(sql.Identifier(aliases[entity]), sql.Identifier(column))

    params: list[Any] = []
    select: list[sql.Composable] = []
    columns: list[Column] = []
    for name in plan.dimensions:
        d = model.dimensions[name]
        select.append(sql.SQL("{} AS {}").format(col(d.entity, d.column), sql.Identifier(name)))
        columns.append(Column(name, "dimension", "text"))
    if plan.grain:
        select.append(
            sql.SQL("date_trunc({}, {})::date AS {}").format(sql.Literal(plan.grain), col(td.entity, td.column), sql.Identifier(plan.grain))
        )
        columns.append(Column(plan.grain, "time", "date"))
    group_count = len(select)

    def measure_sql(m: Measure) -> sql.Composable:
        target = col(m.entity, m.column)
        expr: sql.Composable
        if m.agg == "count_distinct":
            expr = sql.SQL("COUNT(DISTINCT {})").format(target)
        else:
            expr = sql.SQL("{}({})").format(sql.SQL(AGGREGATES[m.agg]), target)
        if m.where:
            conds = []
            for c in m.where:
                dim = model.dimensions[c.field]
                op = "= ANY(%s)" if c.op == "in" else "<> ALL(%s)"
                conds.append(sql.SQL("{} " + op).format(col(dim.entity, dim.column)))
                params.append(list(c.values))
            expr = sql.SQL("{} FILTER (WHERE {})").format(expr, sql.SQL(" AND ").join(conds))
        return expr

    for name in plan.metrics:
        fmt = model.format(name)
        expr: sql.Composable
        if name in model.metrics:
            num, den = model.metrics[name].ratio
            expr = sql.SQL("{}::numeric / NULLIF({}, 0)").format(measure_sql(model.measures[num]), measure_sql(model.measures[den]))
        else:
            expr = measure_sql(model.measures[name])
        if fmt in ROUNDING:
            expr = sql.SQL("ROUND(({})::numeric, {})").format(expr, sql.Literal(ROUNDING[fmt]))
        elif fmt == "integer":
            expr = sql.SQL("COALESCE({}, 0)").format(expr)
        select.append(sql.SQL("{} AS {}").format(expr, sql.Identifier(name)))
        columns.append(Column(name, "metric", fmt))

    where: list[sql.Composable] = []
    for f in plan.filters:
        d = model.dimensions[f.field]
        op = sql.SQL("<> ALL(%s)") if f.op == "not_in" else sql.SQL("= ANY(%s)")
        where.append(sql.SQL("{} {}").format(col(d.entity, d.column), op))
        params.append(list(f.values))
    if plan.time_range:
        where.append(sql.SQL("{} >= %s AND {} < %s").format(col(td.entity, td.column), col(td.entity, td.column)))
        params += [plan.time_range.start, plan.time_range.end]

    distinct = sql.SQL("DISTINCT ") if not plan.metrics else sql.SQL("")
    query = sql.SQL("SELECT {}{} FROM {} AS {}").format(distinct, sql.SQL(", ").join(select), _table(model, base), sql.Identifier("t0"))
    if joins:
        query = sql.SQL("{} {}").format(query, sql.SQL(" ").join(joins))
    if where:
        query = sql.SQL("{} WHERE {}").format(query, sql.SQL(" AND ").join(where))
    if plan.metrics and group_count:
        query = sql.SQL("{} GROUP BY {}").format(query, sql.SQL(", ").join(sql.Literal(i + 1) for i in range(group_count)))

    if plan.order:
        direction = sql.SQL("DESC") if plan.order.direction == "desc" else sql.SQL("ASC")
        order = [sql.SQL("{} {} NULLS LAST").format(sql.Identifier(plan.order.by), direction)]
    elif plan.grain:
        order = [sql.SQL("{} ASC").format(sql.Identifier(plan.grain))]
    elif plan.metrics and group_count:
        order = [sql.SQL("{} DESC NULLS LAST").format(sql.Identifier(plan.metrics[0]))]
    else:
        order = []
    # Ties are broken by the grouping columns so results are deterministic.
    first = plan.order.by if plan.order else plan.grain if plan.grain else None
    order += [sql.SQL("{} ASC").format(sql.Identifier(c.name)) for c in columns[:group_count] if c.name != first]
    if order:
        query = sql.SQL("{} ORDER BY {}").format(query, sql.SQL(", ").join(order))
    # A limit the question asked for is applied as is. Otherwise the policy cap applies, and one
    # extra row is fetched so the answer can say it was truncated.
    if plan.limit and plan.limit <= max_rows:
        cap, fetch = plan.limit, plan.limit
    else:
        cap, fetch = max_rows, max_rows + 1
    query = sql.SQL("{} LIMIT {}").format(query, sql.Literal(fetch))
    return CompiledQuery(query.as_string(None), params, columns, cap, list(aliases))


def _table(model: SemanticModel, entity: str) -> sql.Composable:
    schema, table = model.entities[entity].schema_table
    return sql.Identifier(schema, table)
