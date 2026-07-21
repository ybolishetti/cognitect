# Cognitect

**Natural-language to code-compliant floor plans.** Describe your rooms — Cognitect generates architectural layouts, verifies them against IRC-2021 building code, and exports precise DXF or PDF files ready for AutoCAD or Revit.

Live demo: **[cognitect-six.vercel.app](https://cognitect-six.vercel.app)**

## What it does

1. You describe a floor plan in structured form (rooms, sizes, adjacencies, site constraints).
2. Cognitect generates up to 4 candidate layouts using a Claude Sonnet 4.5-driven LayoutGenerator.
3. Each candidate is verified against:
   - **Layer A — geometry**: overlapping rooms, dangling walls, invalid polygons
   - **Layer C — IRC-2021 code**: R303.1 (lighting/ventilation), R305.1 (minimum ceiling height), R310.1 (bedroom egress windows), R311.2 (exterior door), R311.7 (hallway width)
4. Candidates that fail geometry or code checks are dropped; the best-scoring survivors are returned as a gallery.
5. You pick one, refine it via natural-language edits, and export CAD-ready DXF or PDF.

## Why it's different

Most "AI floor plan" tools generate impressive-looking sketches that fail on real-world constraints — no exit door, bedrooms without egress windows, rooms below minimum ceiling height. Cognitect treats code compliance as a hard gate: a plan that violates any of the 5 IRC-2021 rules is dropped before you see it, not surfaced with a caveat.

- **Constraint-driven, not free-form.** Rooms are placed by [kiwisolver](https://github.com/nucleic/kiwi) (Cassowary constraint solver), not an LLM sketching pixels. Every dimension is precise.
- **Code-compliant by construction.** Layer C runs as a hard gate, not a warning.
- **CAD-ready export.** DXF output via [ezdxf](https://ezdxf.readthedocs.io/); usable in AutoCAD, Revit, and any DXF-compatible workflow.

## Architecture

```
Vercel (Next.js frontend)
       │ HTTPS + Supabase JWT
       ▼
Cloud Run (FastAPI backend) ──── Anthropic Claude Sonnet 4.5 (LayoutGenerator)
       │
       ▼
Supabase Postgres (RLS, anonymous device_id, JWT auth)
```

- **Frontend**: Next.js 14 App Router, Tailwind + shadcn/ui, Supabase Auth (Google OAuth), Vercel Analytics + Sentry
- **Backend**: FastAPI + Pydantic, engine composed of typed Layout schema → PromptedGenerator → Layer A geometry verifier → Layer C code verifier → best-of-N pipeline → SVG preview + DXF export
- **Rate limits**: 1 plan/hour anonymous, 20 plans/day authenticated (via `X-Device-Id` header or Supabase JWT `sub` claim)

## Status

Public beta. Free during beta; paid tiers with higher limits are planned.

## Development

Local dev requires Docker (FreeCAD AppImage runs headless in a container). See [`DEPLOYMENT.md`](DEPLOYMENT.md) for the full setup — Supabase project provisioning, GCP Cloud Run image builds, Vercel env vars, and end-to-end anonymous-plan claim testing.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Security

See [`SECURITY.md`](SECURITY.md).

## License

MIT — see [`LICENSE`](LICENSE).
