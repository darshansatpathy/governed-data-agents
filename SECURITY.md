# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems, in particular for any way to see rows
or columns your access does not allow. Use GitHub's
[private vulnerability reporting](https://github.com/darshansatpathy/governed-data-agents/security/advisories/new)
for this repository. You should get an acknowledgement within three working days.

## Scope

The [threat model](docs/threat-model.md) lists what the project defends against and what is
deliberately out of scope.

## Operating it safely

- Run the service as `gda_app`, never as the warehouse owner or a superuser: row-level security
  does not apply to them. The evaluation's `db-no-role` check fails if the login is a superuser.
- Keep the warehouse owner's credentials out of the service's environment.
- Put the API behind single sign-on, and use `GDA_TRUSTED_USER_HEADER` only when the proxy
  strips that header from incoming requests.
- Treat `semantic/model.yaml` and `policies/access.yaml` as code: changes need review, and the
  evaluation should run on every change.
- The LLM planner sends the user's question and their part of the catalog to the configured
  endpoint. It never sends data.
