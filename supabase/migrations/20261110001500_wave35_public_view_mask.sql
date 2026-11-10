-- Wave 3.5 deliverable rule (Nov 10, 2026): Bloomfield-facing output never shows licensed facts.
-- Leads flagged 'licensed_only' (public signals alone would not make the main list) are excluded; leads flagged
-- 'licensed_private' are shown with licensed parts masked: public distress types only, buyer NOT FOUND when the
-- buyer came from a licensed source, stage from public signals, score_breakdown cut to deal / size / bankruptcy family, only web-link sources.
-- The score itself is the internal score (it may include licensed points; it is a number, not a fact).
create or replace view app.v_lead_list as
SELECT v.lead_id, v.property, v.city, v.state, v.care_types, v.units, v.beds,
    CASE WHEN 'licensed_private' = ANY (coalesce(v.flags, '{}')) THEN v.score_breakdown->>'public_distress_type' ELSE v.distress_type END AS distress_type,
    CASE WHEN 'licensed_private' = ANY (coalesce(v.flags, '{}')) THEN coalesce(v.score_breakdown->>'public_stage', v.stage) ELSE v.stage END AS stage,
    CASE WHEN coalesce((v.score_breakdown->>'licensed_buyer')::boolean, false) THEN 'NOT FOUND' ELSE v.buyer END AS buyer,
    CASE WHEN coalesce((v.score_breakdown->>'licensed_buyer')::boolean, false) THEN 'NOT FOUND' ELSE v.buyer_status END AS buyer_status,
    v.score,
    CASE WHEN 'licensed_private' = ANY (coalesce(v.flags, '{}'))
         THEN jsonb_strip_nulls(jsonb_build_object('deal', v.score_breakdown->'deal', 'size_fit', v.score_breakdown->'size_fit',
                                                   'bankruptcy_family', v.score_breakdown->'bankruptcy_family'))
         ELSE v.score_breakdown END AS score_breakdown,
    array_remove(array_remove(v.flags, 'licensed_private'), 'licensed_only') AS flags,
    v.review_status, v.first_seen, v.last_updated,
    ARRAY(SELECT u FROM unnest(v.source_urls) u WHERE u ~ '^https?://') AS source_urls,
    v.facility_id, v.ccn, v.unconfirmed,
    CASE WHEN coalesce(v.buyer_source_url, '') ~ '^licensed:' THEN NULL ELSE v.buyer_source_url END AS buyer_source_url,
    CASE WHEN coalesce((v.score_breakdown->>'licensed_buyer')::boolean, false) THEN NULL ELSE v.buyer_chain END AS buyer_chain,
    v.tier
   FROM app.v_lead_list_internal v
  WHERE NOT ('licensed_only' = ANY (coalesce(v.flags, '{}')));
grant select on app.v_lead_list to anon, authenticated;
