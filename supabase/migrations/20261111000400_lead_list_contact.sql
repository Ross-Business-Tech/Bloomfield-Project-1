-- Contact fields on the public lead list (Nov 11, 2026). Additive: same rows, same order, six columns appended at the
-- end (CREATE OR REPLACE may only add columns). No stored data is changed.
-- * Phone and website come ONLY from core.facility_contact, where every row has a public source_url (NOT NULL); the
--   source link is returned next to each value. Missing values read 'NOT FOUND' (never guessed).
-- * Address = street / city / state / zip from core.facility (public registries).
-- * Business contact points only (see 20261111000300_facility_contact.sql). Licensed facts are never in that table.
create or replace view app.v_lead_list as
SELECT v.lead_id, v.property, v.city, v.state, v.care_types, v.units, v.beds,
    CASE WHEN p.pub IS NOT NULL THEN p.pub->>'distress_type' ELSE v.distress_type END AS distress_type,
    CASE WHEN p.pub IS NOT NULL THEN p.pub->>'stage' ELSE v.stage END AS stage,
    CASE WHEN p.pub IS NOT NULL THEN p.pub->>'buyer' ELSE v.buyer END AS buyer,
    CASE WHEN p.pub IS NOT NULL THEN p.pub->>'buyer_status' ELSE v.buyer_status END AS buyer_status,
    CASE WHEN p.pub IS NOT NULL THEN (p.pub->>'score')::numeric ELSE v.score END AS score,
    CASE WHEN p.pub IS NOT NULL
         THEN jsonb_strip_nulls(jsonb_build_object('deal', v.score_breakdown->'deal', 'size_fit', v.score_breakdown->'size_fit',
                                                   'bankruptcy_family', v.score_breakdown->'bankruptcy_family'))
         ELSE v.score_breakdown END AS score_breakdown,
    array_remove(array_remove(v.flags, 'licensed_private'), 'licensed_only') AS flags,
    v.review_status, v.first_seen, v.last_updated,
    ARRAY(SELECT u FROM unnest(v.source_urls) u WHERE u ~ '^https?://') AS source_urls,
    v.facility_id, v.ccn, v.unconfirmed,
    CASE WHEN p.pub IS NOT NULL THEN p.pub->>'buyer_source_url' ELSE v.buyer_source_url END AS buyer_source_url,
    CASE WHEN p.pub IS NOT NULL THEN p.pub->'buyer_chain' ELSE v.buyer_chain END AS buyer_chain,
    v.tier,
    coalesce(nullif(concat_ws(', ', f.street, f.city, nullif(concat_ws(' ', f.state, f.zip5), '')), ''), 'NOT FOUND')
                                                       AS contact_address,
    coalesce(ph.value, 'NOT FOUND')                    AS contact_phone,
    ph.source_url                                      AS contact_phone_source_url,
    coalesce(web.value, 'NOT FOUND')                   AS contact_website,
    web.source_url                                     AS contact_website_source_url,
    greatest(ph.verified_at, web.verified_at)          AS contact_checked
   FROM app.v_lead_list_internal v
   CROSS JOIN LATERAL (SELECT CASE WHEN 'licensed_private' = ANY (coalesce(v.flags, '{}'))
                                   THEN v.score_breakdown->'public' END AS pub) p
   LEFT JOIN core.facility f ON f.facility_id = v.facility_id
   LEFT JOIN LATERAL (SELECT c.value, c.source_url, c.verified_at FROM core.facility_contact c
                       WHERE c.facility_id = v.facility_id AND c.kind = 'phone'
                       ORDER BY c.verified_at DESC, c.created_at DESC LIMIT 1) ph ON true
   LEFT JOIN LATERAL (SELECT c.value, c.source_url, c.verified_at FROM core.facility_contact c
                       WHERE c.facility_id = v.facility_id AND c.kind = 'website'
                       ORDER BY c.verified_at DESC, c.created_at DESC LIMIT 1) web ON true
  WHERE NOT ('licensed_only' = ANY (coalesce(v.flags, '{}')))
    -- a licensed lead without a public twin is never shown
    AND NOT ('licensed_private' = ANY (coalesce(v.flags, '{}')) AND p.pub IS NULL)
  ORDER BY 12 DESC NULLS LAST;
grant select on app.v_lead_list to anon, authenticated;
