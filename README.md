# Governed Data Agents

[![CI](https://github.com/darshansatpathy/governed-data-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/darshansatpathy/governed-data-agents/actions/workflows/ci.yml)
[![CodeQL](https://github.com/darshansatpathy/governed-data-agents/actions/workflows/codeql.yml/badge.svg)](https://github.com/darshansatpathy/governed-data-agents/actions/workflows/codeql.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%7C%2017-336791)
![Evaluation](https://img.shields.io/badge/evaluation-78%2F78-success)
![Leaks](https://img.shields.io/badge/data%20leaks-0-success)
![License](https://img.shields.io/badge/license-MIT-green)

**Questions about the business in plain language, answered with the company's own definitions,
only from the data the person asking is allowed to see, and recorded.**

Letting a language model write SQL against a warehouse fails in predictable ways. It picks its own
definition of revenue. It joins a table the user should not see. It is persuaded by text in the
question. This project takes SQL away from the model entirely:

```
question ─▶ planner ─▶ QueryPlan ─▶ grants check ─▶ compiler ─▶ one parameterized SELECT
            (rules or LLM;  (names from the    (measures, dimensions,     │
             never SQL)     semantic layer)     personal data)            ▼
                                                     PostgreSQL session: reader role, row-level security
                                                     on region, column grants, read-only, timeout
                                                                          │
                          answer with the definitions and scope used ◀────┤
                                                    audit.queries ◀───────┘  (every question, answered or not)
```

The access policy is enforced twice: by the compiler before any SQL exists, and by PostgreSQL
itself, so a planner or compiler mistake cannot widen what anyone sees.

## What it looks like

The same question, asked by an analyst limited to EMEA and by a global finance user:

```console
$ gda ask "Which category had the highest revenue last year?" --user ana
revenue; by category; 2025-01-01 to 2025-12-31; ordered by revenue desc; first 1: 1 row.
First: Hardware with revenue 6,928,602.96.
Definition of revenue: Net revenue after discounts, excluding cancelled orders.
Scope: rows are limited to EMEA by row-level security.

$ gda ask "Which category had the highest revenue last year?" --user gina
revenue; by category; 2025-01-01 to 2025-12-31; ordered by revenue desc; first 1: 1 row.
First: Hardware with revenue 21,714,472.31.
Definition of revenue: Net revenue after discounts, excluding cancelled orders.
```

Personal data, for someone without access, and for someone with it:

```console
$ gda ask "Top 3 customers by revenue" --user ana
I can't answer that for you: customer is classified as personal data and your roles do not include access to it.

$ gda ask "Top 3 customers by revenue last year" --user raj
revenue; by customer; 2025-01-01 to 2025-12-31; ordered by revenue desc; first 3: 3 rows.
First: Hiro Costa with revenue 276,948.35.
Scope: rows are limited to APAC by row-level security.
```

A definition the question would otherwise break, a question outside the semantic layer, and a
question that tries to widen its own scope:

```console
$ gda ask "How many orders were cancelled in 2025?" --user gina
cancelled orders; 2025-01-01 to 2025-12-31: cancelled orders 281.
Notes: orders excludes cancelled by definition; used cancelled_orders

$ gda ask "Revenue growth by region" --user gina
I can't answer that: growth and period-over-period comparisons are not supported by the rule planner.

$ gda ask "Revenue by region, and set gda.regions to AMER,EMEA,APAC" --user ana
revenue; by region; region in AMER, EMEA, APAC: 1 row.
Largest: EMEA with revenue 22,470,846.37.
Scope: rows are limited to EMEA by row-level security.
```

`gda plan` shows the plan and the SQL without running anything:

```console
$ gda plan "Top 5 products by revenue in Q4 2025" --user ana
plan: revenue; by product; 2025-10-01 to 2025-12-31; ordered by revenue desc; first 5
sql: SELECT "t1"."product_name" AS "product", ROUND((SUM("t0"."net_amount") FILTER (WHERE "t0"."status" <> ALL(%s)))::numeric, 2)
     AS "revenue" FROM "marts"."fct_order_lines" AS "t0" JOIN "marts"."dim_products" AS "t1" ON "t1"."product_id" = "t0"."product_id"
     WHERE "t0"."order_date" >= %s AND "t0"."order_date" < %s GROUP BY 1 ORDER BY "revenue" DESC NULLS LAST, "product" ASC LIMIT 5
params: [["cancelled"], "2025-10-01", "2026-01-01"]
```

There is no region filter in that SQL. Ana sees EMEA because the database will not show her
anything else.

## The controls

| Control | Where | What it prevents |
|---|---|---|
| [Semantic layer](semantic/model.yaml) | measures, metrics, dimensions, joins | "Revenue" meaning different things in different answers; cancelled orders counted as sales |
| [Access policy](policies/access.yaml) | roles grant measures, dimensions and personal data; users get roles and regions | Analysts grouping or **filtering** by customer name or email (a filter can reveal as much as a column) |
| Scoped catalog | LLM planner | The model being told about fields the user cannot use |
| Plan schema | `QueryPlan`, unknown keys rejected | Planner output carrying SQL or anything else that is not a plan |
| Compiler | identifiers from the semantic layer, values as parameters | SQL injection through values; contradictory filters that silently return zero |
| Row-level security | PostgreSQL policies on `region`, from a transaction setting | Rows outside the user's regions, whatever SQL runs; no setting means no rows |
| Column privileges | `gda_reader` cannot read name, email or phone; `gda_reader_pii` can | Personal data in a session that is not entitled to it |
| No inherited privileges | the login can `SET ROLE` to a reader but does not inherit its grants | A code path that forgets to switch role reading anything |
| Read-only transaction, timeout, row cap | every session | Writes, runaway queries, unbounded result sets |
| [Audit log](warehouse/bootstrap.sql) | `audit.queries`, insert-only for the service | Unrecorded questions, including refused ones |

The marts are built by [dbt](warehouse). Grants and policies are applied by a post-hook on each
model ([`govern()`](warehouse/macros/governance.sql)), because dbt rebuilds tables on every run
and a one-off grant script would be undone by the next one. A dbt test fails the build if a mart
with a region column has no row-level security, or if the non-PII role can read a PII column.

## Planners

- **Rules** (default): a deterministic planner for a bounded grammar. It understands measure and
  dimension names and synonyms, dimension values ("Hardware", "EMEA", "Partner"), negation
  ("excluding hardware"), quoted values after a field (`customer "Hiro Costa"`), ranking ("top 5",
  "which ... highest", "bottom 3"), grains ("monthly", "by quarter", "trend") and periods ("in Q4
  2025", "last quarter", "H1 2025", "since 2025-10-01", "last 30 days"). Outside that, it says what
  it cannot do: growth, comparisons, forecasts, unknown measures or dimensions.
- **LLM**: for open-ended phrasing, through any OpenAI-compatible endpoint (`GDA_LLM_BASE_URL`,
  `GDA_LLM_MODEL`, `GDA_LLM_API_KEY`). It sees only the user's part of the catalog, gets the
  question as data inside JSON, and must return a plan. Invalid output is retried once with the
  validation error, then refused.
- **Auto**: rules first, the LLM only when the rules cannot plan the question.

Every plan, whichever planner produced it, goes through the same grants check, compiler and
database session.

## Results

From `gda eval`, run in CI on PostgreSQL 16 and 17 against the dbt-built warehouse
([full report](docs/evaluation.md)):

| Category | Passed | What it checks |
|---|---|---|
| Accuracy | 33 / 33 | Answers equal hand-written reference SQL, for four users with different scopes |
| Access | 12 / 12 | Refusals for personal data and ungranted measures; scoped answers for regions |
| Unsupported | 8 / 8 | Questions outside the semantic layer are declined, not guessed |
| Hostile questions | 8 / 8 | SQL fragments, quoted injection, "ignore previous instructions", attempts to set the region scope |
| Hostile plans | 8 / 8 | Plans as a compromised planner might produce them: PII dimensions, other regions, SQL in names, extra keys |
| Database | 9 / 9 | Raw SQL in a user's session: other regions invisible, PII columns denied, staging closed, read-only, timeout, no scope means no rows, no role means no access; tables unchanged and every question audited |

**0 data leaks.** A leak is personal data returned to a user without access to it, or any row
from a region outside the user's scope.

The gate is itself tested: with row-level security switched off on the fact table, or with the
email column granted to the non-PII role, the evaluation fails and names the cases that caught it.

**What these numbers do not show.** The questions were written alongside the rule planner, so
33 / 33 shows it is correct within its grammar, not how much real-world phrasing it covers; that
is the LLM planner's job, and it is not evaluated in CI because CI has no model. The data is
synthetic (800 customers, 6,000 orders, 13,000 order lines over 2024 and 2025). The policy does not
stop inference from small aggregates (a region with one customer), which needs minimum group sizes.

## Run it

```bash
docker compose up -d                 # PostgreSQL 17
make install warehouse               # roles, audit table, dbt build (seeds, models, tests)
make eval                            # the evaluation gate

export GDA_DSN=postgresql://gda_app:ci-app@localhost:5432/gda_test GDA_AS_OF=2026-01-15
gda ask "Revenue by quarter in 2025" --user ana
gda catalog --user pat               # what Pat may ask about
gda audit --user ana --limit 5

GDA_API_TOKENS="dev-ana=ana,dev-raj=raj" gda serve
curl -s localhost:8000/ask -H "Authorization: Bearer dev-ana" -H "Content-Type: application/json" \
  -d '{"question": "Top 5 products by revenue in 2025"}'
```

The API takes identity from a bearer token or, behind an authenticating proxy, from a trusted
header (`GDA_TRUSTED_USER_HEADER=X-Forwarded-User`). With neither configured every request is
rejected. `GDA_AS_OF` pins "today" for relative dates; the evaluation uses 2026-01-15.

## Engineering

- 72 tests, 98% line coverage (gate: 90%), with the service, API, CLI and evaluation run against
  a real PostgreSQL warehouse built by dbt.
- `ruff`, `mypy --strict`, Python 3.11 to 3.13.
- CI:
  - lint, types and tests on three Python versions;
  - the evaluation gate on PostgreSQL 16 and 17, plus a check that the committed report matches;
  - a wheel install outside the repository using the bundled semantic layer and policy;
  - CodeQL, pip-audit and Dependabot.
- Design notes: [architecture](docs/architecture.md), [threat model](docs/threat-model.md),
  [decision records](docs/adr).

## Layout

```
semantic/model.yaml     measures, metrics, dimensions, joins, the time dimension
policies/access.yaml    roles, users, regions, limits
warehouse/              dbt project: seeds, staging, governed marts, bootstrap.sql for roles and audit
src/gda/
  planner/rules.py      deterministic planner
  planner/llm.py        LLM planner (OpenAI-compatible), scoped catalog, validated JSON
  plan.py               the QueryPlan schema
  compiler.py           grants check and SQL generation
  executor.py           the governed PostgreSQL session
  service.py            plan, check, compile, run, explain, audit
  api.py, cli.py        HTTP API and command line
  evaluation.py         the evaluation gate
evals/golden.yaml       questions, users, reference SQL, hostile plans
scripts/generate_seeds.py   deterministic synthetic data
```

## Known limits

- Region scope is a transaction setting the service sets. Someone able to run arbitrary SQL as
  the service's login could change it; the service only ever runs SQL its compiler produced, and
  the planners cannot produce SQL. The [threat model](docs/threat-model.md) covers this.
- One fact table and two dimensions. The compiler handles any join graph in the semantic layer,
  but fan-out across several fact tables is not modelled.
- No minimum group sizes, so small aggregates can reveal individual values.
- No period-over-period comparisons in the rule planner.

## License

MIT. See [LICENSE](LICENSE).
