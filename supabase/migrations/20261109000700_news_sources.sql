-- Gate 1 follow-up: news sources that replace the blocked Google News searches. Safe to re-run.
insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes) values
  ('shb_rss',           'Seniors Housing Business RSS',    'news', 'rss',    'https://seniorshousingbusiness.com/feed/',     'daily', 'Backfill via /feed/?paged=N'),
  ('gdelt_doc',         'GDELT DOC 2.0 API',               'news', 'api',    'https://api.gdeltproject.org/api/v2/doc/doc',  'daily', 'Replaces Google News queries. Terms: free for any use with citation + link to https://www.gdeltproject.org/. Max 1 request / 5 s; ~3 month lookback'),
  ('mcknights_sl_rss',  'McKnight''s Senior Living RSS',   'news', 'manual', 'https://www.mcknightsseniorliving.com/feed/',  null,    'robots.txt disallows /feed/'),
  ('mcknights_ltc_rss', 'McKnight''s Long-Term Care News RSS', 'news', 'manual', 'https://www.mcknights.com/feed/',          null,    'Server returns 403 to our User-Agent')
on conflict (source_id) do update set
  name = excluded.name, category = excluded.category, access_method = excluded.access_method,
  base_url = excluded.base_url, refresh_cadence = excluded.refresh_cadence, notes = excluded.notes;

update raw.source set access_method = 'manual', notes = 'robots.txt disallows /rss/search; replaced by gdelt_doc'
where source_id = 'gnews_rss';
update raw.source set access_method = 'manual', notes = 'Only a JavaScript search form; no statewide ALF export with licensee'
where source_id = 'fl_ahca';
