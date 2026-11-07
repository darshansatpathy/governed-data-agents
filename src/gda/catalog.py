"""What a user can see of the semantic layer. Fields they may not use are left out entirely."""

from __future__ import annotations

from typing import Any

from gda.access import Principal
from gda.semantic import SemanticModel


def visible_catalog(model: SemanticModel, principal: Principal) -> dict[str, Any]:
    measures = {
        name: {"description": m.description, "synonyms": m.synonyms, "format": m.format}
        for name, m in model.measures.items()
        if principal.may_use(name, model) is None
    }
    metrics = {
        name: {"description": m.description, "synonyms": m.synonyms, "format": m.format}
        for name, m in model.metrics.items()
        if principal.may_use(name, model) is None
    }
    dimensions = {
        name: {"synonyms": d.synonyms, "values": d.values, "classification": d.classification}
        for name, d in model.dimensions.items()
        if principal.may_use(name, model) is None
    }
    return {
        "measures": measures | metrics,
        "dimensions": dimensions,
        "time": {"name": model.time_dimension.name, "grains": model.time_dimension.grains},
        "regions": list(principal.regions),
    }
