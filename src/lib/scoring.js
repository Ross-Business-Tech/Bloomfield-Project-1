// Rules that turn extracted notices into a ranked lead list.
// This is plain code, not the AI model, so every score can be explained.
import { NOT_FOUND } from '../data/sampleLeads.js'

export const STAGES = ['Sale', 'Late', 'Middle', 'Early']

export const STAGE_INFO = {
  Sale: 'A buyer or new operator has appeared. This is the lead.',
  Late: 'Legal process: receivership, Chapter 11, foreclosure or deed-in-lieu.',
  Middle: 'Loan stress: low DSCR, missed payments, loan maturing.',
  Early: 'Weak signals: low occupancy, losses, delayed opening, liens.',
}

const STAGE_POINTS = { Sale: 40, Late: 30, Middle: 20, Early: 10 }

export const isFound = (value) => value !== NOT_FOUND && value !== undefined && value !== ''

export function monthsSince(dateString, today = new Date()) {
  const date = new Date(dateString)
  if (Number.isNaN(date.getTime())) return null
  const months = (today.getFullYear() - date.getFullYear()) * 12 + (today.getMonth() - date.getMonth())
  return Math.max(0, months)
}

const fieldsToCheck = ['distressType', 'stage', 'units', 'buyer', 'buyerTrackRecord', 'exitPath']

export function scoreLead(lead, today = new Date()) {
  const missing = fieldsToCheck.filter((field) => !isFound(lead[field]))
  const months = monthsSince(lead.eventDate, today)

  // When unsure, leave the score blank and flag the row. Never guess.
  if (!isFound(lead.stage) || !isFound(lead.distressType)) {
    return { ...lead, score: null, reasons: [], missing, months, needsReview: true }
  }

  const reasons = [{ label: `Stage: ${lead.stage}`, points: STAGE_POINTS[lead.stage] ?? 0 }]

  if (isFound(lead.buyer)) reasons.push({ label: 'Buyer / new operator identified', points: 20 })
  if (isFound(lead.buyerTrackRecord)) reasons.push({ label: 'Buyer has a track record', points: 10 })

  if (months !== null) {
    if (months <= 3) reasons.push({ label: `Recent: ${months} months ago`, points: 15 })
    else if (months <= 6) reasons.push({ label: `Fairly recent: ${months} months ago`, points: 10 })
    else if (months <= 12) reasons.push({ label: `Within a year: ${months} months ago`, points: 5 })
  }

  if (isFound(lead.salePricePerUnit) && isFound(lead.priorLoanPerUnit) && lead.salePricePerUnit < lead.priorLoanPerUnit * 0.7) {
    reasons.push({ label: 'Sale price far below prior loan', points: 10 })
  }
  if (isFound(lead.exitPath)) reasons.push({ label: 'Clear exit (refinance path)', points: 5 })

  const extraSignals = Math.min(Math.max(lead.signals.length - 1, 0), 3)
  if (extraSignals > 0) reasons.push({ label: `${extraSignals} extra distress signal${extraSignals > 1 ? 's' : ''}`, points: extraSignals * 5 })

  const score = reasons.reduce((sum, reason) => sum + reason.points, 0)
  return { ...lead, score, reasons, missing, months, needsReview: missing.length > 0 }
}

// Turn raw extracted notices into the ranked list shown to the analyst.
export function buildLeadList(rawLeads, today = new Date()) {
  const dropped = { office: 0, noSource: 0, duplicates: 0 }
  const byKey = new Map()

  for (const lead of rawLeads) {
    if (/office/i.test(lead.assetType)) {
      dropped.office += 1
      continue
    }
    // Never list a lead without a source link proving the distress signal.
    if (!lead.sources?.length) {
      dropped.noSource += 1
      continue
    }
    const key = `${lead.property}|${lead.city}`.toLowerCase()
    const existing = byKey.get(key)
    if (existing) {
      dropped.duplicates += 1
      byKey.set(key, {
        ...existing,
        signals: [...new Set([...existing.signals, ...lead.signals])],
        sources: [...existing.sources, ...lead.sources.filter((s) => !existing.sources.some((e) => e.url === s.url))],
      })
    } else {
      byKey.set(key, lead)
    }
  }

  const leads = [...byKey.values()]
    .map((lead) => scoreLead(lead, today))
    .sort((a, b) => (b.score ?? -1) - (a.score ?? -1))

  return { leads, dropped }
}
