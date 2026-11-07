"""The structured query plan. Planners produce it; only the compiler turns it into SQL.

Nothing in a plan is SQL: names refer to the semantic layer and values are data, bound as
parameters.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gda.semantic import Grain


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(_Strict):
    field: str = Field(max_length=64)
    op: Literal["eq", "in", "not_in"]
    values: list[str] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _short(self) -> Filter:
        if any(len(v) > 200 for v in self.values):
            raise ValueError("filter values must be at most 200 characters")
        return self


class TimeRange(_Strict):
    start: dt.date
    end: dt.date = Field(description="exclusive")

    @model_validator(mode="after")
    def _ordered(self) -> TimeRange:
        if self.end <= self.start:
            raise ValueError("time range end must be after start")
        return self


class Order(_Strict):
    by: str = Field(max_length=64)
    direction: Literal["asc", "desc"] = "desc"


class QueryPlan(_Strict):
    metrics: list[str] = Field(default=[], max_length=5)
    dimensions: list[str] = Field(default=[], max_length=4)
    grain: Grain | None = None
    filters: list[Filter] = Field(default=[], max_length=10)
    time_range: TimeRange | None = None
    order: Order | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def _something(self) -> QueryPlan:
        if not self.metrics and not self.dimensions and not self.grain:
            raise ValueError("a plan needs at least one metric or dimension")
        if len(set(self.metrics)) != len(self.metrics) or len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("metrics and dimensions must not repeat")
        return self

    def fields(self) -> list[str]:
        return [*self.metrics, *self.dimensions, *(f.field for f in self.filters)]

    def describe(self) -> str:
        parts = [", ".join(m.replace("_", " ") for m in self.metrics) or "distinct values"]
        group = [d.replace("_", " ") for d in self.dimensions] + ([self.grain] if self.grain else [])
        if group:
            parts.append("by " + ", ".join(group))
        for f in self.filters:
            word = "not in" if f.op == "not_in" else "in" if len(f.values) > 1 else "="
            parts.append(f"{f.field.replace('_', ' ')} {word} {', '.join(f.values)}")
        if self.time_range:
            last = self.time_range.end - dt.timedelta(days=1)
            parts.append(f"{self.time_range.start.isoformat()} to {last.isoformat()}")
        if self.order:
            parts.append(f"ordered by {self.order.by.replace('_', ' ')} {self.order.direction}")
        if self.limit:
            parts.append(f"first {self.limit}")
        return "; ".join(parts)
