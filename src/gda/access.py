"""Access policy: which measures, dimensions and regions each user may ask about."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from gda.resources import resource
from gda.semantic import SemanticModel


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Role(_Strict):
    measures: Literal["*"] | list[str]
    dimensions: Literal["*"] | list[str]
    pii: bool = False


class User(_Strict):
    name: str
    roles: list[str]
    regions: list[str]


class Limits(_Strict):
    max_rows: int = 500
    statement_timeout_ms: int = 5000


class AccessPolicy(_Strict):
    limits: Limits = Limits()
    roles: dict[str, Role]
    users: dict[str, User]

    def check(self, model: SemanticModel) -> None:
        known = set(model.measures) | set(model.metrics) | set(model.dimensions)
        problems = []
        for rname, role in self.roles.items():
            for kind in (role.measures, role.dimensions):
                if kind != "*":
                    problems += [f"role {rname}: unknown {n}" for n in kind if n not in known]
        allowed_regions = set(model.dimensions["region"].values) if "region" in model.dimensions else set()
        for uname, user in self.users.items():
            problems += [f"user {uname}: unknown role {r}" for r in user.roles if r not in self.roles]
            if allowed_regions:
                problems += [f"user {uname}: unknown region {r}" for r in user.regions if r not in allowed_regions]
        if problems:
            raise ValueError("; ".join(problems))


@dataclass(frozen=True)
class Principal:
    """A user's effective permissions: the union of their roles."""

    user_id: str
    name: str
    measures: frozenset[str]
    dimensions: frozenset[str]
    pii: bool
    regions: tuple[str, ...]
    roles: tuple[str, ...] = field(default=())

    def may_use(self, name: str, model: SemanticModel) -> str | None:
        """None if allowed, otherwise the reason it is not."""
        if model.is_measure_or_metric(name):
            return None if name in self.measures else f"{name} is not granted to your roles ({', '.join(self.roles)})"
        if name in model.dimensions:
            dim = model.dimensions[name]
            if dim.classification == "pii" and not self.pii:
                return f"{name} is classified as personal data and your roles do not include access to it"
            return None if name in self.dimensions else f"{name} is not granted to your roles ({', '.join(self.roles)})"
        if name == model.time_dimension.name:
            return None
        return f"{name} is not part of the semantic layer"


class UnknownUser(LookupError):
    pass


def principal(policy: AccessPolicy, model: SemanticModel, user_id: str) -> Principal:
    user = policy.users.get(user_id)
    if user is None:
        raise UnknownUser(user_id)
    measures: set[str] = set()
    dimensions: set[str] = set()
    pii = False
    for rname in user.roles:
        role = policy.roles[rname]
        measures |= set(model.measures) | set(model.metrics) if role.measures == "*" else set(role.measures)
        dimensions |= set(model.dimensions) if role.dimensions == "*" else set(role.dimensions)
        pii = pii or role.pii
    return Principal(user_id, user.name, frozenset(measures), frozenset(dimensions), pii, tuple(user.regions), tuple(user.roles))


def load(path: Path | None = None, model: SemanticModel | None = None) -> AccessPolicy:
    policy = AccessPolicy.model_validate(yaml.safe_load((path or resource("policies") / "access.yaml").read_text()))
    if model is not None:
        policy.check(model)
    return policy
