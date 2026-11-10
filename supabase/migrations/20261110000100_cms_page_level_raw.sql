-- Free-plan size fix (user decision, Gate 1): CMS files are huge and public, so raw.record keeps one
-- fingerprint row per downloaded page instead of one per data row. Each stg row keeps its own content_hash,
-- so re-runs stay idempotent; the full downloads stay in the local cache (data/raw/cache).
alter table stg.cms_chow       add column content_hash text;
alter table stg.cms_chow_owner add column content_hash text;
alter table stg.cms_snf_owner  add column content_hash text;
alter table stg.cms_chow       drop constraint cms_chow_raw_id_key,       add constraint cms_chow_content_hash_key unique (content_hash);
alter table stg.cms_chow_owner drop constraint cms_chow_owner_raw_id_key, add constraint cms_chow_owner_content_hash_key unique (content_hash);
alter table stg.cms_snf_owner  drop constraint cms_snf_owner_raw_id_key,  add constraint cms_snf_owner_content_hash_key unique (content_hash);
