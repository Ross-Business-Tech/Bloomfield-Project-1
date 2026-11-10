-- Wave 3.5 (Nov 10, 2026): per-community buyers from licensed bankruptcy-sale data (schema private only).
alter table private.licensed_buyer add column if not exists community text;
alter table private.licensed_buyer add column if not exists city text;
alter table private.licensed_buyer add column if not exists state text;
alter table private.licensed_buyer add column if not exists price_usd numeric;
alter table private.licensed_buyer add column if not exists closing_deadline date;
alter table private.licensed_buyer add column if not exists buyer_type_guess text;
alter table private.licensed_buyer add column if not exists match_method text;
alter table private.licensed_buyer add column if not exists match_score numeric;
alter table private.licensed_buyer add column if not exists sale_signal boolean not null default false;  -- court-approved sale = court_sale_process signal
