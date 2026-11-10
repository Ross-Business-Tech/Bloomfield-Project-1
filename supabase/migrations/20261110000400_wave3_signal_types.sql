-- Wave 3 / Gate 2 decisions: broker-arranged sales are market_sale (low weight, not distress);
-- court sale processes (sale motion, bidding procedures, stalking horse, sale order) are their own signal;
-- extraction distress types chapter_7, lease/loan default and closure get matching signal types.
alter table core.signal drop constraint signal_signal_type_check,
  add constraint signal_signal_type_check check (signal_type in (
    'dscr_below_1','maturity_12m_low_dscr','delayed_opening','mechanics_lien','chapter_11',
    'receivership','deed_in_lieu','foreclosure','operator_turnover','low_occupancy','negative_noi',
    'price_collapse','sff_or_candidate','staffing_drop','hud_loan_terminated','warn_closure',
    'bridge_on_bridge',
    'chapter_7','loan_or_lease_default','closure','court_sale_process','market_sale'));
