# Cognitect Frontend

Next.js 14 (App Router) frontend for Cognitect. Talks to the `/v2/plans/*`
API (FastAPI + Supabase, deployed on Cloud Run) and uses Supabase Auth
(Google OAuth) for signed-in accounts, with an anonymous "try it" flow
backed by a device id.

## Local setup

```bash
cp .env.example .env.local
# fill in NEXT_PUBLIC_SUPABASE_ANON_KEY from SECRETS.md
npm install
npm run dev
```

`NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000` in `.env.example`
— point it at the deployed Cloud Run URL if you don't have the backend
running locally (see the repo root's `DRAFT_PRODUCTIZATION_1_BACKEND.md`
for running it locally).

## Testing

```bash
npm run test       # vitest run
npm run test:watch # vitest watch mode
```

See `TESTING.md` for the manual smoke-test checklist (auth flows, rate
limiting, cross-device claim) that isn't covered by the unit tests.

## Deploying to Vercel

```bash
vercel link
vercel env add NEXT_PUBLIC_SUPABASE_URL production
vercel env add NEXT_PUBLIC_SUPABASE_ANON_KEY production
vercel env add NEXT_PUBLIC_API_URL production   # the Cloud Run URL
vercel deploy --prod
```

`vercel.json` references those same three env vars by name (`@cognitect-*`)
so `vercel env add` above needs to use matching secret names, or update
`vercel.json` to match whatever `vercel link` generates.

After the first deploy, add the Vercel deployment URL as an authorized
redirect URL in the Supabase dashboard (Authentication → URL
Configuration) — otherwise the Google OAuth callback will fail in
production even though it works locally against `localhost:3000`.

Custom domain (`cognitect.app`? `cognitect.ai`?) is still TBD — deploy to
the auto-assigned `*.vercel.app` domain until that's decided.
