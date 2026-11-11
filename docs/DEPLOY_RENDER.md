# Deploying to Render (checklist)

**Status: prep only. Nothing has been deployed and no Render account was created by Claude.** Hosting decision
(Nov 10, 2026): Render, not Vercel or here.now.

## Design (decisions Nov 10, 2026)
- The website shows a **static snapshot** of the public lead list (`app.v_lead_list`: no licensed-only leads,
  licensed parts masked, web links only), with past-deal homes left out. It has **no database connection and no
  Supabase key at runtime**, so nobody can query the database through it.
- The snapshot is **never committed** (the repo is public). It is made on a team laptop and uploaded to Render as a
  **Secret File**.
- Every page and download needs a password (HTTP basic auth, `SITE_PASSWORD`). Pages are `noindex`, and
  `/robots.txt` disallows all crawling. Only `/healthz` (returns "ok", no data) and `/robots.txt` are open.
- Lists: **A** live deals with a named buyer, **B** pre-sale distress with no buyer, **C** buyers named on 2+ homes in
  this list (built from public-view fields only; the fuller repeat-buyer / takeover list uses internal tables and is
  left out).
- **Daily cron: off** (no paid items; Render cron jobs cost at least $1 per month each). The snapshot is refreshed by
  hand (step 6).

## Free-plan limits (render.com/docs/free, checked Nov 10, 2026)
- A Free web service **spins down after 15 minutes without traffic**; the next visit waits about a minute.
- **750 free instance hours per workspace per month**; when they run out, Free web services are suspended until the
  next month.
- Free services count against the workspace's monthly outbound bandwidth and build minutes.

## Steps
1. **Make the snapshot (team laptop with the pipeline set up):**
   `.venv/bin/python -m src.web.snapshot` → `output/site_snapshot.json` (plus `site_list_a/b/c.csv`). `output/` is
   gitignored; never `git add -f` it. Past-deal homes are left out using `eval/private/exclude_facilities_PRIVATE.txt`.
2. **Check it:** `.venv/bin/pytest -q tests/web` (no licensed fields, no past-deal homes, password on every page).
   Optional local preview: `SITE_PASSWORD=<something> SNAPSHOT_PATH=output/site_snapshot.json PORT=8765
   .venv/bin/python -m src.web.server`, then open http://localhost:8765 (any user name, that password).
3. **Render account and repo (team owner):** sign in to Render, *New → Blueprint*, connect the GitHub repo and pick
   the `development` branch (`render.yaml` is read from there). Render needs access to the GitHub repo; ask the repo
   owner if it cannot see it.
4. **Secret File:** in the service, *Environment → Secret Files → Add Secret File*. Filename `site_snapshot.json`,
   contents = paste the whole file from step 1. Render mounts it at `/etc/secrets/site_snapshot.json` (already set as
   `SNAPSHOT_PATH` in `render.yaml`). Secret files are not stored in the repo or in build logs.
5. **Env var:** *Environment → Add Environment Variable* `SITE_PASSWORD` = a strong password (8+ characters). Share it
   with the team out of band (not in GitHub, not in the repo).
6. **Deploy:** `autoDeploy` is off; press *Manual Deploy*. Health check `/healthz`. Open the site: the browser asks
   for the password; List A, B and C show with source links; downloads at `/exports/list_a.csv` etc.
7. **Refresh the data:** rerun step 1, replace the Secret File contents (step 4) and redeploy. The page shows the
   snapshot time.

## Never
- Never commit the snapshot or put it anywhere public; never paste it into an issue or pull request.
- Never give the website a database URL, the service-role key or the Anthropic key: it needs none.
