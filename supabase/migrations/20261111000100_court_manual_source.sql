-- Wave 4 (Nov 10, 2026): hand-entered PUBLIC bankruptcy filings (config/court_facts.yaml `cases`) as a source.
insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes) values
  ('court_manual', 'Hand-entered public bankruptcy filings', 'legal', 'manual', null, null,
   'config/court_facts.yaml cases: public docket listings our court sources missed; every entry has a public link')
on conflict (source_id) do nothing;
