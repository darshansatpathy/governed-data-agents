from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gda import access, semantic
from gda.catalog import visible_catalog
from gda.resources import resource
from gda.semantic import SemanticModel


def test_model_loads_and_resolves(model: SemanticModel) -> None:
    assert model.entity_of("average_order_value") == "order_lines"
    assert model.entity_of("order_date") == "order_lines"
    assert model.base_measures("discount_rate") == ["discounts", "gross_revenue"]
    assert model.format("revenue") == "currency" and model.format("region") == "text"
    assert model.description("region") == ""
    assert [j.to for j in model.join_path("customers", "products")] == ["customers", "products"]
    with pytest.raises(KeyError):
        model.entity_of("nope")


def test_model_rejects_inconsistencies(tmp_path: Path) -> None:
    data = yaml.safe_load((resource("semantic") / "model.yaml").read_text())
    data["joins"].append({"from": "order_lines", "to": "ghosts", "keys": ["a", "b"]})
    data["measures"]["revenue"]["entity"] = "nowhere"
    data["metrics"]["average_order_value"]["ratio"] = ["revenue", "missing"]
    data["dimensions"]["region"]["entity"] = "nowhere"
    data["dimensions"]["channel"]["synonyms"].append("sales")
    data["dimensions"]["orders"] = {"entity": "order_lines", "column": "order_id"}
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError) as err:
        semantic.load(path)
    text = str(err.value)
    for expected in ["unknown entity ghosts", "measure revenue", "unknown measure missing", "dimension region", "'sales'", "unique"]:
        assert expected in text
    data = yaml.safe_load((resource("semantic") / "model.yaml").read_text())
    data["joins"] = []
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="no join path"):
        semantic.load(path).join_path("order_lines", "products")


def test_policy_union_and_reasons(model: SemanticModel, policy: access.AccessPolicy) -> None:
    raj = access.principal(policy, model, "raj")
    assert raj.pii and raj.regions == ("APAC",) and "discounts" not in raj.measures
    assert raj.may_use("customer_email", model) is None
    assert "not granted" in str(raj.may_use("discounts", model))
    ana = access.principal(policy, model, "ana")
    assert "personal data" in str(ana.may_use("customer", model))
    assert ana.may_use("order_date", model) is None
    assert "not part of" in str(ana.may_use("salary", model))
    pat = access.principal(policy, model, "pat")
    assert "not granted" in str(pat.may_use("segment", model))
    with pytest.raises(access.UnknownUser):
        access.principal(policy, model, "mallory")


def test_policy_check_rejects_unknown_names(model: SemanticModel, tmp_path: Path) -> None:
    data = yaml.safe_load((resource("policies") / "access.yaml").read_text())
    data["roles"]["analyst"]["measures"] = ["revenue", "profit"]
    data["users"]["ana"]["roles"].append("ghost")
    data["users"]["ana"]["regions"].append("MARS")
    path = tmp_path / "a.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError) as err:
        access.load(path, model)
    assert "unknown profit" in str(err.value) and "unknown role ghost" in str(err.value) and "unknown region MARS" in str(err.value)


def test_catalog_hides_what_a_user_cannot_use(model: SemanticModel, policy: access.AccessPolicy) -> None:
    ana = visible_catalog(model, access.principal(policy, model, "ana"))
    assert "customer_email" not in ana["dimensions"] and "customer" not in ana["dimensions"]
    assert "discounts" in ana["measures"] and ana["regions"] == ["EMEA"]
    pat = visible_catalog(model, access.principal(policy, model, "pat"))
    assert set(pat["measures"]) == {"revenue", "units", "orders"}
    raj = visible_catalog(model, access.principal(policy, model, "raj"))
    assert raj["dimensions"]["customer_email"]["classification"] == "pii"
