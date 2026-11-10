-- Gate 3 buyer fixes (Nov 10, 2026): buyer_status gains
--   'shell' = the named buyer is an OpCo / PropCo or facility-named LLC and CMS ownership shows no parent behind it
--   'stale' = the ownership change is older than scoring.buyer_named_max_months (24); kept for context, 0 buyer points
alter table app.lead drop constraint lead_buyer_status_check,
  add constraint lead_buyer_status_check check (buyer_status in ('named','candidate','shell','stale','NOT FOUND'));
