-- Wave 3.5 (Nov 10, 2026): news sources whose terms / robots allow RSS use (checked Nov 10).
insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes) values
  ('bondoro_rss', 'Bondoro Chapter 11 filing alerts + case summaries (RSS)', 'news', 'rss', 'https://bondoro.com/tag/chapter-11-filing-alerts/rss/', 'daily', 'Free Ghost tag feeds, full text; cases > $10M liabilities'),
  ('whatnow_rss', 'WhatNow search feeds (senior/assisted living, nursing home, memory care)', 'news', 'rss', 'https://whatnow.com/?s=senior+living&feed=rss2', 'daily', 'robots disallows */page/: never paginate; low yield'),
  ('levin_sci', 'Levin Associates / SeniorCare Investor', 'news', 'manual', 'https://seniorcare.levinassociates.com/feed/', 'manual', 'Terms ban automated tools; paywalled; manual reading only')
on conflict (source_id) do nothing;
