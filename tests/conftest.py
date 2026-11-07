from __future__ import annotations

import datetime as dt
import os

import pytest

from gda import access, semantic
from gda.semantic import SemanticModel

AS_OF = dt.date(2026, 1, 15)


@pytest.fixture(scope="session")
def model() -> SemanticModel:
    return semantic.load()


@pytest.fixture(scope="session")
def policy(model: SemanticModel) -> access.AccessPolicy:
    return access.load(model=model)


@pytest.fixture(scope="session")
def dsns() -> tuple[str, str]:
    """(service login, warehouse owner) for a database built with bootstrap.sql and dbt."""
    app, admin = os.environ.get("GDA_TEST_DSN"), os.environ.get("GDA_TEST_ADMIN_DSN")
    if not app or not admin:
        pytest.skip("GDA_TEST_DSN and GDA_TEST_ADMIN_DSN are not set")
    return app, admin
