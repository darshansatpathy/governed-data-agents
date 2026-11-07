# Threat model

## Assets

- Rows of the warehouse outside a user's regions.
- Personal data: customer names, email addresses, phone numbers.
- Measures restricted to some roles (discounts, for example).
- The integrity of the warehouse and of the audit log.

## Actors

- **A signed-in user** asking questions within the product, possibly trying to see more than
  their access allows, including by phrasing questions as instructions.
- **A compromised or confused planner**, most plausibly the LLM following text in a question.
- **A bug in the service**: the compiler, the executor or a future code path.

The service's own login and the database administrators are trusted.

## Threats and controls

| # | Threat | Control | Tested by |
|---|---|---|---|
| T1 | A user asks for another region's data | Row-level security in PostgreSQL; the compiler never adds region filters | `acl-09`..`acl-11`, `plan-02`, `plan-05`, `db-rls` |
| T2 | Personal data shown as a column | Grants check on PII dimensions; column privileges on the reader role | `acl-01`, `acl-02`, `acl-05`, `plan-01`, `db-pii` |
| T3 | Personal data inferred through a filter (revenue for one named customer) | Filter fields need the same access as dimensions | `acl-03`, `plan-07` |
| T4 | Text in the question steers the LLM | The model gets the scoped catalog only and the question as data; output must be a plan; the plan is checked and compiled like any other | `test_llm.py`, `inj-03`, `inj-05` |
| T5 | SQL injection through values | All values are bound parameters; identifiers come from the semantic layer | `inj-02`, `inj-04`, `test_compiler.py` |
| T6 | A planner emits SQL, unknown names or extra keys | Strict plan schema; unknown names rejected | `plan-03`, `plan-04`, `plan-08` |
| T7 | A compiler bug produces a query that reads too much | The database enforces regions and PII columns regardless of the SQL | `db-rls`, `db-pii`, `db-staging` |
| T8 | A code path forgets to switch role or set the scope | No inherited privileges; no scope means no rows | `db-no-role`, `db-no-scope` |
| T9 | A query writes or runs for a long time | Read-only transaction; statement timeout; row cap | `db-readonly`, `db-timeout`, `inj-08` |
| T10 | Questions go unrecorded, or the log is altered | Every outcome audited before returning; the service can insert and read the log but not change it | `db-audit`, `test_postgres_audit_log` |
| T11 | Ungranted measures | Grants check | `acl-06`..`acl-08`, `plan-06` |

## Out of scope, and why

- **Arbitrary SQL in the service's session.** The region scope is a transaction setting. Code
  that could run arbitrary SQL as `gda_app` could change it, or switch to the PII role. The
  service never runs SQL it did not compile, the planners cannot produce SQL, and compiled
  queries are a single statement sent with bound parameters. Per-user database logins would close
  this at the cost of a connection per user.
- **Inference from small groups.** An aggregate over one customer is that customer's value. The
  policy has no minimum group size.
- **Authentication.** The API trusts a configured bearer token or an upstream proxy's header.
  Production deployments should put it behind single sign-on.
- **Availability.** No rate limiting beyond the statement timeout and row cap.
