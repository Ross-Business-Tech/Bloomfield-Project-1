-- Nov 10, 2026: generic names for the licensed source (the repo is public; no provider names in keys or tags).
-- Moves the earlier licensed source row (found by its notes) to 'licensed_data', and rewrites the loan keys, file
-- names and 'licensed:' tags already in the database to the generic ones the loaders and scorer now write.
-- Safe to run on a fresh database (every statement matches 0 rows there).
drop table if exists _old_src;
create temp table _old_src as
select source_id from raw.source
 where notes like 'Licensed facts; local gitignored files in eval/private only%' and source_id <> 'licensed_data';

insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes)
select 'licensed_data', 'Licensed exports (shared privately)', category, access_method, base_url, refresh_cadence, notes
  from raw.source where source_id in (select source_id from _old_src)
on conflict (source_id) do nothing;

update raw.ingest_run      set source_id = 'licensed_data' where source_id in (select source_id from _old_src);
update raw.record          set source_id = 'licensed_data' where source_id in (select source_id from _old_src);
update raw.document        set source_id = 'licensed_data' where source_id in (select source_id from _old_src);
update core.facility_xref  set source_id = 'licensed_data' where source_id in (select source_id from _old_src);
update core.metric         set source_id = 'licensed_data' where source_id in (select source_id from _old_src);
delete from raw.source where source_id in (select source_id from _old_src);

-- loan keys: '<prefix>_hud232:...' -> 'lic_hud232:...', any other '<prefix>_<file>:...' -> 'lic_loans:...'
-- (only keys that are in private.licensed_loan; buyer and fact keys are left alone)
drop table if exists _keys;
create temp table _keys as
select loan_key AS old_key,
       CASE WHEN loan_key ~ '^[a-z]+_hud232:' THEN regexp_replace(loan_key, '^[a-z]+_hud232:', 'lic_hud232:')
            ELSE regexp_replace(loan_key, '^[a-z]+_[a-z0-9]+:', 'lic_loans:') END AS new_key
  from private.licensed_loan
 where loan_key !~ '^lic_(hud232|loans):';

update private.licensed_loan l set loan_key = k.new_key from _keys k where l.loan_key = k.old_key;
update private.licensed_loan set source_file = CASE WHEN source_file like '%hud232%'
                                                    THEN 'licensed_hud232_flags_PRIVATE.csv'
                                                    ELSE 'licensed_loans_PRIVATE.csv' END
 where source_file not in ('licensed_hud232_flags_PRIVATE.csv', 'licensed_loans_PRIVATE.csv');
update core.facility_xref x set source_key = k.new_key from _keys k
 where x.source_id = 'licensed_data' and x.source_key = k.old_key;

-- signals built on licensed loan facts ('lic:<type>:<loan key>') and every 'licensed:<provider>' tag
update core.signal s set source_key = regexp_replace(s.source_key, '^(lic:[a-z_0-9]+:).*$', '\1') || k.new_key
  from _keys k
 where s.source_key like 'lic:%' and regexp_replace(s.source_key, '^lic:[a-z_0-9]+:', '') = k.old_key;
update core.signal set source_url = regexp_replace(source_url, '^licensed:[^:]+', 'licensed:provider')
 where source_url like 'licensed:%' and source_url not like 'licensed:provider%';
-- loan signals end in the source file name ('licensed:provider:<file>'): the new file names
update core.signal set source_url = 'licensed:provider:' || CASE WHEN source_url like '%hud232%'
                                                                 THEN 'licensed_hud232_flags_PRIVATE.csv'
                                                                 ELSE 'licensed_loans_PRIVATE.csv' END
 where source_url ~ '^licensed:provider:.+'
   and source_url !~ ':(licensed_hud232_flags|licensed_loans)_PRIVATE\.csv$';

drop table _keys;
drop table _old_src;
