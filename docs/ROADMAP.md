# Deal Radar roadmap (after the Nov 11, 2026 hand-over)

What to build next, roughly in order of value. Each item keeps the project rules: public sources only, a source link
on every fact, `NOT FOUND` instead of guesses, nothing licensed in the public view, no paid service without sign-off.

| # | Item | Why | Done when |
|---|---|---|---|
| 1 | **Daily auto-refresh** | News and CourtListener already collect daily in GitHub Actions (`news-daily`, `courtlistener-daily`), but extract → enrich → score → publish is run by hand, so the site goes stale between runs. | One scheduled job runs the pipeline and `python -m src.web.publish` (dry run), and publishes with `--confirm` only when all checks pass; a failed check leaves the live site unchanged and alerts the team. Needs a decision on cost (a Render cron is paid) or a GitHub Actions job holding `RENDER_API_KEY` as a secret. |
| 2 | **CMS provider coordinates (manual file)** | Map pins are ZIP-code centroids today; the CMS provider API blocks automated downloads. | A person downloads the CMS Provider Information CSV in a browser into `output/`; a loader reads latitude / longitude by CCN into `core.facility_geo` with `coord_source = 'cms'`; ZIP centroids remain the fallback. |
| 3 | **Per-signal source and date fields** | The lead card lists the signals and the lead's source links, but not which link and date belong to which signal. | Every signal in the snapshot carries its own `source_url` and `event_date`, and the lead card shows them side by side. |
| 4 | **Buyer track record** | The client lends to the buyer; an operator's record of past turnarounds is the key credit question. | Each named buyer shows homes acquired in 36 months, current CMS ratings / penalties for homes it runs, and any closures or exits, each with a source link. |
| 5 | **List C per-lead join** | List C lists repeat buyers, but a lead card does not yet say "this buyer is on List C". | Each lead whose buyer (or parent) is a List C company shows the link and the buyer's List C rank; the Buyers view links back to those leads. |
| 6 | **More state assisted-living licences (incl. Kansas)** | AL is under-covered compared with nursing homes, because CMS data covers nursing homes only. | Licence files from more states (Kansas first, then the next largest AL states) are loaded, with ownership / licensee changes feeding the same signals as Texas and North Carolina. |
| 7 | **Receivership news feed** | State-court receiverships are scattered and often only appear in local news or legal notices. | A daily public feed (news search + state court portals where allowed) adds receivership signals with source links, without bypassing any site's bot check. |
| 8 | **Claims-agent sale orders for final buyers** | Stalking-horse "candidates" and unnamed transferees are not final until the court's sale order. | For bankruptcy cases with a public claims-agent site, the sale order or notice of successful bidder is read and the buyer is upgraded from Candidate to Named, with that document as the source. No PACER. |
| 9 | **Project-name cleanup in code and comments** | The client's name still appears in comments, docs and identifiers (it is the repo name, so the leak guard allows it). | Code and comments use the neutral product name "Deal Radar"; the client's name stays only where it is needed (repo name, README background). |
| 10 | **Decision: git history and repo visibility** | Wording later removed for licensing reasons is still in older commits of this public repo; it was not rewritten (decision Nov 11, 2026). | The team and instructor decide one of: keep as is, make the repo private, or rewrite history (requires every clone to re-sync). Record the decision in `HANDOFF.md`. |

## Also worth doing

- Confirm the scoring assumptions with the client (care-type fit +10, 100+ homes mega-platform cut-off) and update
  `config/thresholds.yaml` labels from ASSUMPTION to CONFIRMED.
- Clear the contact candidates in `docs/contacts_needs_review.csv` and raise public-contact coverage above 45%.
- Update `docs/DEPLOY_RENDER.md` from "prep only" to the live setup.
