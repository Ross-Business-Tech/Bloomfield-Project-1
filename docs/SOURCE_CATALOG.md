# Source catalog: terms and access (checked Nov 10, 2026)

Every source the pipeline reads or decided not to read, with the terms / robots finding. Crawler identity:
`UMich-Team1-research/0.1 (contact: RESEARCH_CONTACT_EMAIL from .env)`; no client name in any request.
Loaders are run by hand (no weekly schedule). Licensed data never enters the repo.

## State assisted-living licence rosters
| source_id | State / file | Licensee? | Terms / robots | Access |
|---|---|---|---|---|
| mi_lara | MI LARA statewide AFC/HFA text file | yes | public download | automated |
| nc_dhsr | NC DHSR Adult Care Homes `Ahlist.xlsx` | yes | no robots.txt; DHSR disclaimer: "grants permission to copy and distribute files" | automated (+ Wayback 2022+) |
| tx_hhsc | TX HHSC ALF directory `apps.hhs.texas.gov/providers/directories/al.xlsx` | yes (+ management company) | **Page blocked, file public**: the linking page on www.hhs.texas.gov returns 403 to our crawler, so its terms were not read; the file host's robots.txt allows it and serves it. | automated (+ Wayback 2021+) |
| tx_hhsc_closures | TX HHSC ALF closures `alf_closures.xlsx` | owner | same host as above | automated |
| ny_doh | NY DOH health.data.ny.gov `vn5v-hh5r` + `2g9y-7kqm` | yes (operator) | robots allows /resource; no licence page found (Open NY terms assumed) | automated |
| ia_dial | IA DIAL data.iowa.gov dataset 562 | no | CC BY 4.0 | automated (buildings only) |
| pa_dhs | PA DHS data.pa.gov `pqf4-d4xn` (personal care homes) | no | public domain | automated (buildings only); no Right-to-Know request for now |
| fl_ahca | FL AHCA FloridaHealthFinder ALF export | yes (profile pages) | search-form export only | manual: one download by the user when needed |
| oh_odh | OH ODH extract tool | probably | ASP.NET form | manual |
| il_idph | IL IDPH assisted living / shared housing | ? | Salesforce portal, no bulk file | manual / FOIA |
| il_hfs_slf | IL HFS supportive living `OperationalSites.docx` | yes (company) | robots allows | not loaded yet |
| ks_kdads | KS KDADS directory | ? | site returns 403 (Akamai) | manual / records request |

## News
| source_id | Feed | Terms / robots | Access |
|---|---|---|---|
| shn_rss, snn_rss, shb_rss | SHN, SNN, Seniors Housing Business | robots allow; SHB has full text in feed | automated |
| bondoro_rss | bondoro.com tag feeds (Ch. 11 filing alerts, case summaries; > $10M liabilities) | robots allows all but /ghost/, /email/, /r/, /members/api/ | automated |
| whatnow_rss | whatnow.com WordPress search feeds | robots disallows */page/ (never paginate) | automated, low yield |
| levin_sci | Levin Associates / SeniorCare Investor | terms ban crawlers/bots; paywalled | manual reading only |

## Loan data
| Source | Registration (the user does it) | Notes |
|---|---|---|
| Freddie Mac MSIA (fm-msia.com) | free account; accept terms | only GSE source with property names; skip restricted deal documents |
| Freddie Mac MLPD | free web form (research use) | anonymized |
| Fannie Mae DUS Disclose | free account for bulk downloads | property names likely; not verified (403 to automated fetch) |
| Fannie Mae Multifamily Loan Performance Data | free Data Dynamics account | anonymized; no redistribution without consent |
| licensed_data | Licensed exports shared privately | licensed: files in eval/private only, facts in schema `private`, signals tagged `licensed:`; leads hidden from the public view |
