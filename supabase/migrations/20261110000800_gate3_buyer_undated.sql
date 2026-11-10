-- Gate 3 (Nov 10, 2026): 'named (undated)' = buyer named in a source, but neither a sale order nor a CMS
-- change-of-ownership date exists; scored at scoring.buyer_undated_weight of the named bonus.
alter table app.lead drop constraint lead_buyer_status_check,
  add constraint lead_buyer_status_check
  check (buyer_status in ('named','named (undated)','candidate','shell','stale','NOT FOUND'));
