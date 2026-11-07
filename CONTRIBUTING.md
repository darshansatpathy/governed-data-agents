# Contributing

```bash
docker compose up -d
python -m venv .venv && . .venv/bin/activate
make install warehouse
export GDA_TEST_DSN=postgresql://gda_app:ci-app@localhost:5432/gda_test
export GDA_TEST_ADMIN_DSN=postgresql://postgres:ci-only@localhost:5432/gda_test
make check eval
```

## Adding a measure or dimension

1. Add it to `semantic/model.yaml` with a description, synonyms and, for a dimension, its
   classification. Personal data must be `pii`.
2. If it is a new column of a mart, list it in that model's `govern(pii=[...])` when it is
   personal data.
3. Grant it to the roles that should have it in `policies/access.yaml`.
4. Add golden cases with hand-written reference SQL, and access cases for a user who must not
   see it.
5. `make report` to regenerate `docs/evaluation.md`.

## Changing the planners

Every new phrasing the rule planner understands needs a test in `tests/test_planner.py` and, if
it changes answers, a golden case. If the planner cannot be sure, it should decline.
