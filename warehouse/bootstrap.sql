-- Roles and the audit table. Run once per database by an administrator, before dbt.
-- Idempotent.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'gda_reader') then
    create role gda_reader nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'gda_reader_pii') then
    create role gda_reader_pii nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'gda_app') then
    create role gda_app login;
  end if;
end
$$;

-- The service logs in as gda_app. It may switch to a reader role but does not inherit its
-- privileges, so a code path that forgets to switch reads nothing.
grant gda_reader to gda_app with inherit false, set true;
grant gda_reader_pii to gda_app with inherit false, set true;

create schema if not exists audit;
create table if not exists audit.queries (
    id bigserial primary key,
    at timestamptz not null default now(),
    user_id text not null,
    question text not null,
    planner text,
    outcome text not null,
    reasons jsonb not null default '[]',
    plan jsonb,
    sql text,
    params jsonb,
    row_count integer,
    duration_ms integer
);
grant usage on schema audit to gda_app;
grant insert, select on audit.queries to gda_app;
grant usage on sequence audit.queries_id_seq to gda_app;
