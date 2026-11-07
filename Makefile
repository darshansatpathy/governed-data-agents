.PHONY: install check lint test warehouse seeds eval serve report

# The warehouse owner (runs bootstrap and dbt) and the service's login.
ADMIN_DSN ?= postgresql://postgres:ci-only@localhost:5432/gda_test
APP_DSN ?= postgresql://gda_app:ci-app@localhost:5432/gda_test
APP_PASSWORD ?= ci-app

install:
	pip install -e ".[dev,dbt]"

lint:
	ruff check .
	ruff format --check .
	mypy

test:
	pytest --cov --cov-report=term-missing --cov-fail-under=90

check: lint test

seeds:
	python scripts/generate_seeds.py

# Roles and the audit table, then dbt: seeds, staging views, governed marts, data tests.
warehouse:
	psql "$(ADMIN_DSN)" -v ON_ERROR_STOP=1 -q -f warehouse/bootstrap.sql
	psql "$(ADMIN_DSN)" -v ON_ERROR_STOP=1 -q -c "ALTER ROLE gda_app PASSWORD '$(APP_PASSWORD)'"
	cd warehouse && \
	  GDA_DB_HOST=$$(python -c "from urllib.parse import urlparse as u; print(u('$(ADMIN_DSN)').hostname)") \
	  GDA_DB_PORT=$$(python -c "from urllib.parse import urlparse as u; print(u('$(ADMIN_DSN)').port or 5432)") \
	  GDA_DB_USER=$$(python -c "from urllib.parse import urlparse as u; print(u('$(ADMIN_DSN)').username)") \
	  GDA_DB_PASSWORD=$$(python -c "from urllib.parse import urlparse as u; print(u('$(ADMIN_DSN)').password or '')") \
	  GDA_DB_NAME=$$(python -c "from urllib.parse import urlparse as u; print(u('$(ADMIN_DSN)').path.lstrip('/'))") \
	  dbt build --profiles-dir .

eval:
	gda eval --dsn "$(APP_DSN)" --admin-dsn "$(ADMIN_DSN)" --check --report eval-report.md

report:
	gda eval --dsn "$(APP_DSN)" --admin-dsn "$(ADMIN_DSN)" --report docs/evaluation.md

serve:
	GDA_DSN="$(APP_DSN)" gda serve
