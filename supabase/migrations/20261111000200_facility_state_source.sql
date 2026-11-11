-- Wave 4 (Nov 10, 2026): where a building's state comes from. NULL = stated by the source; 'inferred' = deduced
-- from the city or portfolio named in a public source (hand-entered facilities). Leads show flag 'state_inferred'.
alter table core.facility add column if not exists state_source text
  check (state_source is null or state_source in ('stated', 'inferred'));
