import { useMemo, useState } from 'react'
import { sampleLeads } from './data/sampleLeads.js'
import { STAGES, STAGE_INFO, buildLeadList, isFound } from './lib/scoring.js'
import './App.css'

const money = (value) => (isFound(value) ? `$${Math.round(value / 1000)}k` : 'NOT FOUND')

function Field({ label, value }) {
  const found = isFound(value)
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd className={found ? '' : 'not-found'}>{found ? value : 'NOT FOUND'}</dd>
    </div>
  )
}

function StageBadge({ stage }) {
  const found = isFound(stage)
  return <span className={`badge stage-${found ? stage.toLowerCase() : 'unknown'}`}>{found ? stage : 'Stage unknown'}</span>
}

function LeadCard({ lead, rank }) {
  const [open, setOpen] = useState(false)

  return (
    <article className={`lead ${lead.needsReview ? 'flagged' : ''}`}>
      <header className="lead-head">
        <div className="score" aria-label={lead.score === null ? 'No score' : `Score ${lead.score}`}>
          <span className="score-num">{lead.score ?? '—'}</span>
          <span className="score-rank">#{rank}</span>
        </div>
        <div className="lead-title">
          <h3>{lead.property}</h3>
          <p className="muted">
            {lead.city}, {lead.state} · {lead.assetType} · {isFound(lead.units) ? `${lead.units} units` : 'units NOT FOUND'}
          </p>
          <div className="badges">
            <StageBadge stage={lead.stage} />
            <span className="badge plain">{isFound(lead.distressType) ? lead.distressType : 'Distress type NOT FOUND'}</span>
            {lead.engine === 'B' && <span className="badge plain">Delayed opening</span>}
            {lead.needsReview && <span className="badge review">Needs review</span>}
          </div>
        </div>
      </header>

      <div className="buyer">
        <span className="buyer-label">Likely borrower (buyer / new operator)</span>
        <span className={isFound(lead.buyer) ? 'buyer-name' : 'buyer-name not-found'}>
          {isFound(lead.buyer) ? lead.buyer : 'NOT FOUND'}
        </span>
        {!isFound(lead.buyer) && isFound(lead.receiver) && <span className="muted small">Contact meanwhile: {lead.receiver}</span>}
      </div>

      <ul className="sources">
        {lead.sources.map((source) => (
          <li key={source.url}>
            <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label} ↗</a>
          </li>
        ))}
      </ul>

      <button type="button" className="toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? 'Hide details' : 'Why this score & details'}
      </button>

      {open && (
        <div className="details">
          <h4>Distress signals</h4>
          <ul className="signals">
            {lead.signals.map((signal) => <li key={signal}>{signal}</li>)}
          </ul>

          <h4>Score breakdown</h4>
          {lead.score === null ? (
            <p className="not-found small">Score left blank: the stage or distress type could not be confirmed from the source.</p>
          ) : (
            <ul className="reasons">
              {lead.reasons.map((reason) => (
                <li key={reason.label}><span>{reason.label}</span><span>+{reason.points}</span></li>
              ))}
              <li className="total"><span>Total</span><span>{lead.score}</span></li>
            </ul>
          )}

          <h4>Fundability flags</h4>
          <dl className="fields">
            <Field label="Buyer track record" value={lead.buyerTrackRecord} />
            <Field label="Prior loan per unit" value={isFound(lead.priorLoanPerUnit) ? money(lead.priorLoanPerUnit) : lead.priorLoanPerUnit} />
            <Field label="Sale price per unit" value={isFound(lead.salePricePerUnit) ? money(lead.salePricePerUnit) : lead.salePricePerUnit} />
            <Field label="Exit path" value={lead.exitPath} />
            <Field label="Receiver / debtor / contact" value={lead.receiver} />
            <Field label="Event date" value={`${lead.eventDate}${lead.months !== null ? ` (${lead.months} months ago)` : ''}`} />
          </dl>
        </div>
      )}
    </article>
  )
}

export default function App() {
  const { leads, dropped } = useMemo(() => buildLeadList(sampleLeads), [])
  const [stage, setStage] = useState('All')
  const [state, setState] = useState('All')
  const [buyerOnly, setBuyerOnly] = useState(false)
  const [search, setSearch] = useState('')

  const states = useMemo(() => [...new Set(leads.map((lead) => lead.state))].sort(), [leads])

  const visible = leads.filter((lead) => {
    if (stage !== 'All' && lead.stage !== stage) return false
    if (state !== 'All' && lead.state !== state) return false
    if (buyerOnly && !isFound(lead.buyer)) return false
    const text = `${lead.property} ${lead.city} ${lead.buyer} ${lead.distressType}`.toLowerCase()
    return text.includes(search.trim().toLowerCase())
  })

  const withBuyer = leads.filter((lead) => isFound(lead.buyer)).length
  const lateOrSale = leads.filter((lead) => lead.stage === 'Sale' || lead.stage === 'Late').length
  const review = leads.filter((lead) => lead.needsReview).length

  return (
    <div className="page">
      <header className="top">
        <p className="eyebrow">Team 1 · Distressed Deal Sourcing</p>
        <h1>Find senior housing deals before a broker does</h1>
        <p className="lede">
          A ranked list of senior housing properties heading into distress, and the operators likely to buy them,
          so the originations team can offer a bridge loan first.
        </p>
        <p className="sample-note">Demo with made-up sample data. These properties, operators and sources are not real.</p>
      </header>

      <section className="stats" aria-label="Summary">
        <div className="stat"><span className="stat-num">{leads.length}</span><span>Leads</span></div>
        <div className="stat"><span className="stat-num">{lateOrSale}</span><span>Late stage or sale</span></div>
        <div className="stat"><span className="stat-num">{Math.round((withBuyer / leads.length) * 100)}%</span><span>Buyer identified</span></div>
        <div className="stat"><span className="stat-num">{review}</span><span>Need review</span></div>
      </section>

      <section className="filters" aria-label="Filters">
        <div className="chips" role="group" aria-label="Stage">
          {['All', ...STAGES].map((option) => (
            <button
              key={option}
              type="button"
              className={`chip ${stage === option ? 'active' : ''}`}
              aria-pressed={stage === option}
              onClick={() => setStage(option)}
            >
              {option}
            </button>
          ))}
        </div>
        {stage !== 'All' && <p className="muted small stage-help">{STAGE_INFO[stage]}</p>}
        <div className="filter-row">
          <input
            type="search"
            placeholder="Search property, city, operator"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search leads"
          />
          <select value={state} onChange={(event) => setState(event.target.value)} aria-label="State">
            <option value="All">All states</option>
            {states.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
          <label className="check">
            <input type="checkbox" checked={buyerOnly} onChange={(event) => setBuyerOnly(event.target.checked)} />
            Buyer identified
          </label>
        </div>
      </section>

      <section className="leads" aria-label="Leads">
        {visible.length === 0 && <p className="empty">No leads match these filters.</p>}
        {visible.map((lead) => (
          <LeadCard key={lead.id} lead={lead} rank={leads.indexOf(lead) + 1} />
        ))}
      </section>

      <footer className="foot">
        <p>
          Cleaned up automatically: {dropped.duplicates} duplicate merged, {dropped.office} office property removed (out of scope),{' '}
          {dropped.noSource} lead hidden for having no source link.
        </p>
        <p className="muted small">Every lead must link to the source that proves the distress signal. Missing facts show NOT FOUND, never a guess.</p>
      </footer>
    </div>
  )
}
