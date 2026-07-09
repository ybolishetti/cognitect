# Manual Testing Checklist

Automated coverage (`npm run test`) is intentionally light — device id /
auth-header plumbing and the claim-on-sign-in flow. Everything below needs
a real browser against the **live prod backend**
(`https://cognitect-api-247099587583.us-central1.run.app`), not localhost,
since anonymous/auth flows depend on real Supabase sessions and the
device-id/rate-limit behavior is only meaningful against the real API.

- [ ] Anonymous: visit `/try`, a plan is created and the preview renders;
      refresh the page — the same plan is restored (device id persisted in
      localStorage, `?plan=<id>` reflected in the URL)
- [ ] Anonymous: plan name on `/try` is read-only; clicking it opens the
      sign-in modal with "Sign in to rename plans."
- [ ] Anonymous: clicking "New Plan" on `/try` always opens the sign-in
      modal — it never silently creates a second plan
- [ ] Anonymous → auth: sign in via Google from `/try`, see a "Claimed 1
      plan" toast, land on `/plans/<the-claimed-id>`
- [ ] Authed: `/plans` lists the account's plans; cards show the
      gradient-placeholder-with-initials (no thumbnails in Phase 1)
- [ ] Authed: create a plan from `/plans`, rename it inline in the editor,
      delete it from the `/plans` grid's dropdown — the row disappears
      (this is a soft-delete server-side; there's no restore/undo in this
      UI)
- [ ] Authed: sign out from the header, session clears, redirected to `/`
- [ ] Cross-device: sign in on a second browser (or incognito window) with
      the same account — the same plans are visible
- [ ] Rate limit: as an anonymous user, send two `/instruct` calls within
      the same hour — the second shows a toast: "Rate limit reached. Try
      again in ~1h, or sign in for a higher limit." with a "Sign in"
      action button
- [ ] Route protection: visit `/plans` or `/account` while signed out —
      redirected to `/` with the sign-in modal open automatically
- [ ] All 6 routes render without hydration errors (check the browser
      console): `/`, `/try`, `/plans`, `/plans/[id]`, `/account`,
      `/auth/callback`
- [ ] Auth: Upload a `.dxf` floor plan, redirected to `/plans/<new-id>`,
      rooms render
- [ ] Auth: Upload a `.json` FloorPlanState, same
- [ ] Auth: Upload a `.png`, see a 400 toast
- [ ] Auth: Upload an 11 MB junk file, see a 413 toast
