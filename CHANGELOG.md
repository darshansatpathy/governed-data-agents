# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-08

### Added

- Semantic layer in YAML: entities, joins, seven measures with their definitions, two ratio
  metrics, ten dimensions with classifications, a time dimension.
- Access policy: roles granting measures, dimensions and personal data; users with roles and
  regions; row and time limits.
- Rule planner for a bounded grammar, LLM planner for OpenAI-compatible endpoints, and an `auto`
  planner that combines them.
- Compiler with a grants check and parameterized SQL; governed PostgreSQL sessions with reader
  roles, row-level security, read-only transactions and timeouts; an audit log.
- dbt warehouse with governance applied by a post-hook and a governance test.
- HTTP API and `gda` command line.
- Evaluation gate: 78 cases across accuracy, access, unsupported questions, hostile questions and
  plans, and database enforcement, run in CI on PostgreSQL 16 and 17.
