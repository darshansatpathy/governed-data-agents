{#
  Applied as a post-hook on every mart. dbt rebuilds tables on each run, so grants and policies
  are part of the model, not a one-off script that the next run would silently undo.

  - gda_reader may read every column except the ones listed as pii.
  - gda_reader_pii may read every column.
  - With a row_policy column, both roles see only rows whose value is in the comma-separated
    list held by the transaction setting gda.regions. When the setting is absent, they see
    nothing.
#}
{% macro govern(row_policy=none, pii=[]) %}
  {% set columns = adapter.get_columns_in_relation(this) | map(attribute='name') | list %}
  {% set open_columns = columns | reject('in', pii) | list %}
  grant usage on schema {{ this.schema }} to gda_reader, gda_reader_pii;
  grant select ({{ open_columns | join(', ') }}) on {{ this }} to gda_reader;
  grant select on {{ this }} to gda_reader_pii;
  {% if row_policy %}
  alter table {{ this }} enable row level security;
  create policy region_scope on {{ this }} for select to gda_reader, gda_reader_pii
    using ({{ row_policy }} = any (string_to_array(current_setting('gda.regions', true), ',')));
  {% endif %}
{% endmacro %}
