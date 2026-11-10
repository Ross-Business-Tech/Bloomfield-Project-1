// Loads leads from Supabase when it's configured, otherwise falls back to the built-in sample leads.
// VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY are set in Vercel's environment variables.
// The anon key is public by design: the database only lets it read (see supabase/schema.sql).
import { sampleLeads, NOT_FOUND } from '../data/sampleLeads.js'

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY

const orNotFound = (value) => (value === null || value === undefined || value === '' ? NOT_FOUND : value)

function fromRow(row) {
  return {
    id: row.id,
    property: row.property,
    city: row.city,
    state: row.state,
    assetType: row.asset_type,
    units: orNotFound(row.units),
    engine: row.engine,
    distressType: orNotFound(row.distress_type),
    stage: orNotFound(row.stage),
    eventDate: row.event_date ?? '',
    buyer: orNotFound(row.buyer),
    buyerTrackRecord: orNotFound(row.buyer_track_record),
    receiver: orNotFound(row.receiver),
    signals: row.signals ?? [],
    priorLoanPerUnit: orNotFound(row.prior_loan_per_unit),
    salePricePerUnit: orNotFound(row.sale_price_per_unit),
    exitPath: orNotFound(row.exit_path),
    // Only keep links that are real web addresses, since rows come from scraped text.
    sources: (Array.isArray(row.sources) ? row.sources : []).filter((s) => /^https?:\/\//.test(s?.url ?? '')),
    isSample: row.is_sample,
  }
}

export async function loadLeads() {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    return { leads: sampleLeads, origin: 'sample' }
  }
  try {
    const response = await fetch(`${SUPABASE_URL}/rest/v1/leads?select=*`, {
      headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` },
    })
    if (!response.ok) throw new Error(`Supabase returned ${response.status}`)
    const rows = await response.json()
    return { leads: rows.map(fromRow), origin: 'database' }
  } catch {
    return { leads: sampleLeads, origin: 'offline' }
  }
}
