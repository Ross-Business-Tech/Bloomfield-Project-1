-- Wave 2 schema requests #5-#7 (core-builder) + Gate 2 decision: HUD borrower-code prefix saved as a 'borrower_group' role.
alter table core.facility_role drop constraint facility_role_role_check,
  add constraint facility_role_role_check
  check (role in ('owner','operator','licensee','propco','opco','receiver','lender','indirect_owner','borrower_group'));

alter table core.loan
  add column interest_rate     numeric,
  add column current_balance   numeric,
  add column termination_date  date,
  add column termination_type  text;

alter table core.case_facility
  add column link_method text check (link_method in ('facility_name','debtor_org')),
  add column match_score numeric;
