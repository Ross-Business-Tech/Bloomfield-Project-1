# Database (Supabase)

1. In the Supabase project, open **SQL Editor > New query**, paste `schema.sql`, and click **Run**.
2. New query again: paste `seed.sql` and click **Run** to load the made-up sample leads.
3. In **Project Settings > API**, copy the **Project URL** and the **anon / publishable** key into Vercel's environment variables as `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.

The anon key is safe to be public: the table only allows reading with it.
The **service_role / secret** key can write anything. It goes only into Vercel for the collector (as `SUPABASE_SERVICE_ROLE_KEY`), never into code, chat, or GitHub.

After changing the sample leads in `src/data/sampleLeads.js`, regenerate the seed with `node supabase/make-seed.mjs`.
