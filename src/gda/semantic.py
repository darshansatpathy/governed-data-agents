"""The semantic layer: entities, joins, measures, metrics and dimensions, loaded from YAML."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gda.resources import resource

Grain = Literal["day", "week", "month", "quarter", "year"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Entity(Strict):
    table: str
    description: str = ""

    @property
    def schema_table(self) -> tuple[str, str]:
        schema, _, table = self.table.partition(".")
        return schema, table


class Join(Strict):
    from_: str = Field(alias="from")
    to: str
    keys: tuple[str, str]


class Condition(Strict):
    field: str
    op: Literal["in", "not_in"]
    values: list[str]


class Measure(Strict):
    description: str
    entity: str
    agg: Literal["sum", "count_distinct", "count", "avg", "min", "max"]
    column: str
    where: list[Condition] = []
    synonyms: list[str] = []
    format: Literal["currency", "integer", "number", "percent"] = "number"


class Metric(Strict):
    description: str
    ratio: tuple[str, str]
    synonyms: list[str] = []
    format: Literal["currency", "integer", "number", "percent"] = "number"


class Dimension(Strict):
    entity: str
    column: str
    synonyms: list[str] = []
    values: list[str] = []
    classification: Literal["public", "pii"] = "public"


class TimeDimension(Strict):
    name: str
    entity: str
    column: str
    grains: list[Grain]


class SemanticModel(Strict):
    entities: dict[str, Entity]
    joins: list[Join]
    measures: dict[str, Measure]
    metrics: dict[str, Metric] = {}
    dimensions: dict[str, Dimension]
    time_dimension: TimeDimension

    @model_validator(mode="after")
    def _consistent(self) -> SemanticModel:
        problems: list[str] = []
        for j in self.joins:
            for e in (j.from_, j.to):
                if e not in self.entities:
                    problems.append(f"join references unknown entity {e}")
        for name, m in self.measures.items():
            if m.entity not in self.entities:
                problems.append(f"measure {name}: unknown entity {m.entity}")
        for name, metric in self.metrics.items():
            for part in metric.ratio:
                if part not in self.measures:
                    problems.append(f"metric {name}: unknown measure {part}")
        for name, d in self.dimensions.items():
            if d.entity not in self.entities:
                problems.append(f"dimension {name}: unknown entity {d.entity}")
        names = list(self.measures) + list(self.metrics) + list(self.dimensions) + [self.time_dimension.name]
        if len(names) != len(set(names)):
            problems.append("measure, metric and dimension names must be unique")
        seen: dict[str, str] = {}
        for name, synonyms in self.synonyms().items():
            for s in synonyms:
                if s in seen and seen[s] != name:
                    problems.append(f"synonym {s!r} used by both {seen[s]} and {name}")
                seen[s] = name
        if problems:
            raise ValueError("; ".join(problems))
        return self

    # ---- lookups -----------------------------------------------------------------------

    def is_measure_or_metric(self, name: str) -> bool:
        return name in self.measures or name in self.metrics

    def synonyms(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name, m in self.measures.items():
            out[name] = [s.lower() for s in [name.replace("_", " "), *m.synonyms]]
        for name, metric in self.metrics.items():
            out[name] = [s.lower() for s in [name.replace("_", " "), *metric.synonyms]]
        for name, d in self.dimensions.items():
            out[name] = [s.lower() for s in [name.replace("_", " "), *d.synonyms]]
        return {k: sorted(set(v)) for k, v in out.items()}

    def description(self, name: str) -> str:
        if name in self.measures:
            return self.measures[name].description
        if name in self.metrics:
            return self.metrics[name].description
        return ""

    def format(self, name: str) -> str:
        if name in self.measures:
            return self.measures[name].format
        if name in self.metrics:
            return self.metrics[name].format
        return "text"

    def base_measures(self, name: str) -> list[str]:
        return list(self.metrics[name].ratio) if name in self.metrics else [name]

    def entity_of(self, name: str) -> str:
        if name in self.measures:
            return self.measures[name].entity
        if name in self.metrics:
            return self.measures[self.metrics[name].ratio[0]].entity
        if name in self.dimensions:
            return self.dimensions[name].entity
        if name == self.time_dimension.name:
            return self.time_dimension.entity
        raise KeyError(name)

    def join_path(self, start: str, goal: str) -> list[Join]:
        """Shortest chain of joins from one entity to another (joins are usable both ways)."""
        if start == goal:
            return []
        queue: deque[tuple[str, list[Join]]] = deque([(start, [])])
        seen = {start}
        while queue:
            node, path = queue.popleft()
            for j in self.joins:
                for a, b in ((j.from_, j.to), (j.to, j.from_)):
                    if a == node and b not in seen:
                        if b == goal:
                            return [*path, j]
                        seen.add(b)
                        queue.append((b, [*path, j]))
        raise ValueError(f"no join path from {start} to {goal}")


def load(path: Path | None = None) -> SemanticModel:
    data = yaml.safe_load((path or resource("semantic") / "model.yaml").read_text())
    return SemanticModel.model_validate(data)
