-- Wave 0 · migration 4 of 5: lock everything down.
-- RLS on every table with no policies = nobody but the service role (which bypasses RLS) can read or write.
-- The public (anon) key may read exactly one thing: app.v_lead_list.

do $$
declare t record;
begin
  for t in
    select schemaname, tablename from pg_tables where schemaname in ('raw','stg','core','app')
  loop
    execute format('alter table %I.%I enable row level security', t.schemaname, t.tablename);
  end loop;
end $$;

revoke all on all tables in schema raw, stg, core, app from anon, authenticated;
revoke all on schema raw, stg, core from anon, authenticated;

-- The view runs with its owner's rights, so anon can read it without seeing the tables behind it.
grant usage on schema app to anon, authenticated;
grant select on app.v_lead_list to anon, authenticated;

-- The pipeline writes with the service role.
grant usage on schema raw, stg, core, app to service_role;
grant all on all tables in schema raw, stg, core, app to service_role;
grant all on all sequences in schema raw, stg, core, app to service_role;
alter default privileges in schema raw, stg, core, app grant all on tables to service_role;
alter default privileges in schema raw, stg, core, app grant all on sequences to service_role;
