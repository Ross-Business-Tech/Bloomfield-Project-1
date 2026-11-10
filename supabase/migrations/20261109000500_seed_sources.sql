-- Wave 0 · migration 5 of 5: the source registry (raw.source). Safe to re-run.
-- robots_ok is left null until a loader checks robots.txt at run time.

insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes) values
  -- Phase 1: structured, free
  ('cms_chow',           'CMS SNF Change of Ownership',                'ownership', 'api',  'https://data.cms.gov/data-api/v1/dataset/f557a6ed-95b3-4a22-8433-4175db2dec1c/data', 'monthly',   'Dataset ID changes each release: re-resolve from data.cms.gov/data.json by title'),
  ('cms_chow_owner',     'CMS SNF Change of Ownership - Owner Information', 'ownership', 'api', 'https://data.cms.gov/data-api/v1/dataset/a4358712-e910-4eaf-8f24-5e90ba3cf8d0/data', 'monthly', 'Re-resolve dataset ID at run time'),
  ('cms_snf_owner',      'CMS SNF All Owners',                         'ownership', 'api',  'https://data.cms.gov/data-api/v1/dataset/afe44b85-cc6d-40d7-b5df-00ae8910d1d2/data', 'monthly',   'Re-resolve dataset ID at run time'),
  ('cms_snf_enrollment', 'CMS SNF Enrollments',                        'ownership', 'api',  'https://data.cms.gov/data-api/v1/dataset/5f2c306f-3b1c-42cd-b037-187b2ce22126/data', 'monthly',   'Seeds core.facility (CCN exact key)'),
  ('cms_cost_report',    'CMS SNF Cost Report',                        'operating', 'api',  'https://data.cms.gov/data-api/v1/dataset/a69d3df7-3f66-4a0d-b5b8-0d66049bd565/data', 'annual',    'Lagged; occupancy proxy and net income'),
  ('cms_pbj',            'CMS Payroll Based Journal Daily Nurse Staffing', 'operating', 'api', 'https://data.cms.gov/data-api/v1/dataset/7e0d53ba-8f02-4c66-98a5-14a1c997c50d/data', 'quarterly', 'Aggregate to quarter before insert'),
  ('cms_chain_perf',     'CMS Nursing Home Chain Performance',         'operating', 'api',  'https://data.cms.gov/data-api/v1/dataset/97ecfad1-d3f1-4d42-b774-d74661d830bc/data', 'annual',    'Buyer track record'),
  ('cms_sff',            'CMS Special Focus Facility list',            'operating', 'file', 'https://www.cms.gov/files/document/sff-posting-candidate-list-october-2026.pdf',      'monthly',   'Monthly PDF; URL pattern changes each month'),
  ('hud_active',         'HUD FHA insured multifamily loans (active)', 'debt_clock','file', 'https://www.hud.gov/sites/default/files/Housing/documents/FHA-BF90-RM-A.xlsx',        'monthly',   'Keep Section 232 / nursing / assisted / board categories'),
  ('hud_terminated',     'HUD FHA multifamily loans (terminated)',     'debt_clock','file', 'https://www.hud.gov/sites/default/files/Housing/documents/FHA-BF90-RM-T.xlsx',        'monthly',   'Termination type -> hud_loan_terminated'),
  -- Phase 2: text and courts
  ('shn_rss',            'Senior Housing News RSS',                    'news',      'rss',  'https://seniorhousingnews.com/feed/',      'daily',  null),
  ('snn_rss',            'Skilled Nursing News RSS',                   'news',      'rss',  'https://skillednursingnews.com/feed/',     'daily',  null),
  ('gnews_rss',          'Google News RSS queries',                    'news',      'rss',  'https://news.google.com/rss/search?q=',   'daily',  'Queries listed in docs/BUILD_SPEC.md'),
  ('courtlistener',      'CourtListener bankruptcy dockets',           'legal',     'api',  'https://www.courtlistener.com/api/rest/v4/', 'daily', 'Token required (COURTLISTENER_TOKEN)'),
  ('stretto',            'Stretto case pages',                         'legal',     'html', 'https://cases.stretto.com',                'weekly', 'Case pages only'),
  ('epiq',               'Epiq case pages',                            'legal',     'html', 'https://dm.epiq11.com',                    'weekly', 'Case pages only'),
  ('elevenflo',          'elevenflo case pages',                       'legal',     'html', 'https://elevenflo.com',                    'weekly', 'Case pages only; robots blocks /search and /api/search/open'),
  ('mi_lara',            'Michigan LARA HFA/AFC statewide file',       'ownership', 'file', 'https://www.michigan.gov/lara/bureau-list/bchs/adult/online-lookups/statewide-text-file-of-adult-foster-care-homes-for-the-aged-facilities', 'monthly', 'Assisted living licensee changes'),
  ('fl_ahca',            'Florida AHCA FloridaHealthFinder',           'ownership', 'html', 'https://quality.healthfinder.fl.gov',      'monthly', 'Prefer bulk export over page scraping'),
  ('warn',               'WARN notices (warnfirehose)',                'news',      'html', 'https://www.warnfirehose.com/layoffs',     'weekly', 'Filter to senior living / nursing employers'),
  -- Manual only: logged, never scraped
  ('cmbs_news',          'cmbs.news',                                  'debt_clock','manual','https://cmbs.news',                       null,     'robots blocks /api/'),
  ('msrb_emma',          'MSRB EMMA',                                  'debt_clock','manual','https://emma.msrb.org',                   null,     'Manual lookups only'),
  ('kroll',              'Kroll restructuring portal',                 'legal',     'manual','https://restructuring.ra.kroll.com',      null,     'Returns 403; use CourtListener for those cases'),
  ('cms_care_compare',   'CMS Care Compare provider data',             'operating', 'manual','https://data.cms.gov/provider-data',       null,     'Returns 403 to automated clients'),
  ('county_recorders',   'County registers of deeds',                  'legal',     'manual', null,                                     null,     'Liens, deeds-in-lieu, sale prices; varies by county'),
  ('freddie_mac',        'Freddie Mac multifamily loan-level data',    'debt_clock','manual','https://mf.freddiemac.com',               null,     'Registration required')
on conflict (source_id) do update set
  name = excluded.name,
  category = excluded.category,
  access_method = excluded.access_method,
  base_url = excluded.base_url,
  refresh_cadence = excluded.refresh_cadence,
  notes = excluded.notes;
