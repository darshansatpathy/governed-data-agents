-- Fails if a mart table with a region column lacks row-level security, or if the non-PII
-- reader role can read a PII column.
select c.relname as problem
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'marts'
  and c.relkind = 'r'
  and exists (
      select 1 from information_schema.columns ic
      where ic.table_schema = 'marts' and ic.table_name = c.relname and ic.column_name = 'region'
  )
  and not c.relrowsecurity
union all
select table_name || '.' || column_name
from information_schema.column_privileges
where table_schema = 'marts'
  and grantee = 'gda_reader'
  and column_name in ('customer_name', 'email', 'phone')
