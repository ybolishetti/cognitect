# Architecture C — Master Plan

**Status:** In progress — staged DRAFTs
**Owner:** Hermes (spec) / Yash + Cursor Composer (implementation)
**Started:** 2026-07-05

---

## Goal

Implement the full "AI Floor-Plan Generation" spec (Option C from the July 3 review) — **without** a fine-tuned model yet. The architecture is built to slot in a fine-tuned model later without touching anything downstream.

**Guiding principle from the spec:**
> Weights move probability mass. Schema makes geometry valid by construction. Verifiers enforce a hard floor. Structure is advisory.

---

## What Changes vs. Current Cognitect

| Layer | Current | Architecture C |
|---|---|---|
| Input | NL string → FloorPlanOp (mutation) | NL string → FloorPlanSpec (whole-plan intent) |
| Generation | kiwisolver + shelf-packer produces coordinate_matrix | LayoutGenerator produces N candidate Layouts (typed schema) |
| Validity | Trusted by construction (buggy) | Layer A geometry verifier (Shapely) — hard reject |
| Code compliance | None | Layer C code checker — hard reject on 3–5 IRC rules |
| Structural sanity | None | Layer B advisory scorer |
| Selection | Single output | Best-of-N: rank surviving candidates |
| Model | claude-haiku-4-5 (intent parser) | Swappable LayoutGenerator (Stub / Prompted / FineTuned) |
| Audit | None | Per-Layout manifest of verifier passes/fails |

**What survives from current codebase:**
- `intent_parser/` — repurposed for editing existing plans (FloorPlanOp still valid for edit flow)
- `exporter/` — DXF/PDF export unchanged, consumes typed Layout instead of coordinate_matrix
- `previewer.py` — the Phase 10 rewrite still applies, consumes Layout
- FastAPI routes — restructured but same shapes

**What gets deprecated (kept in tree, gradually replaced):**
- `constraint_solver/solver.py` two-phase logic → wrapped by `StubGenerator` for testing
- Direct coordinate_matrix as the source of truth → replaced by Layout schema

---

## Staged DRAFTs

Each DRAFT is a self-contained Cursor Composer spec. Send them in order — each depends on the previous being merged.

| # | DRAFT | Scope | Est. lines changed | Dependencies |
|---|---|---|---|---|
| A | `DRAFT_ARCH_C_1_SCHEMA.md` | Typed Layout schema (Room, Wall, Opening, StructuralGrid, Exit) + FloorPlanSpec | ~500 new / 0 changed | None |
| B | `DRAFT_ARCH_C_2_LAYER_A.md` | Layer A geometry verifier (Shapely) + tests | ~400 new | A |
| C | `DRAFT_ARCH_C_3_GENERATOR_IFACE.md` | LayoutGenerator abstract interface + StubGenerator (wraps existing solver) | ~350 new | A |
| D | `DRAFT_ARCH_C_4_PROMPTED_GEN.md` | PromptedGenerator (claude-sonnet with heavy prompt + JSON-schema-constrained output) | ~500 new | A, C |
| E | `DRAFT_ARCH_C_5_LAYER_C.md` | Layer C code checker — 5 IRC rules + rule registry | ~600 new | A |
| F | `DRAFT_ARCH_C_6_BEST_OF_N.md` | Best-of-N selector + Layer B advisory scorer + audit manifest | ~450 new | B, C, D, E |
| G | `DRAFT_ARCH_C_7_FINETUNED_STUB.md` | FineTunedGenerator placeholder — NotImplementedError with clear TODO | ~80 new | C |
| H | `DRAFT_ARCH_C_8_API_WIRING.md` | New `/plan/generate` endpoint using LayoutGenerator + best-of-N; legacy routes preserved | ~300 new / ~100 changed | F, G |

**Total estimated scope:** ~3,200 new lines of code + ~1,500 lines of tests. About 3–5 weeks of Cursor Composer work if you feed one DRAFT per session.

---

## Key Architectural Decisions

### 1. Layout schema is the ground truth, not coordinate_matrix

The typed `Layout` (Rooms + Walls + Openings + StructuralGrid + Exits) replaces `coordinate_matrix` as the pipeline's source of truth. This kills an entire class of bugs where the solver produces geometry that violates invisible constraints — walls must meet at endpoints, openings must sit on walls, rooms must be closed polygons, etc.

### 2. FloorPlanSpec is a whole-plan intent, not a mutation

Current pipeline: NL → single-op mutation (FloorPlanOp). New pipeline: NL → whole-plan spec (FloorPlanSpec) with room list, adjacency requirements, jurisdiction, constraints. LayoutGenerator produces N candidate Layouts from one Spec.

**Edit flow preservation:** FloorPlanOp remains valid for edit operations on an existing Layout. Two flows exist in parallel:
- Generate flow: NL → FloorPlanSpec → LayoutGenerator → best-of-N → Layout
- Edit flow (existing): NL → FloorPlanOp → apply to Layout → re-verify → Layout

### 3. LayoutGenerator is behind an interface

```python
class LayoutGenerator(ABC):
    @abstractmethod
    def generate(self, spec: FloorPlanSpec, n: int = 1) -> list[Layout]: ...
```

Three implementations:
- `StubGenerator`: wraps existing kiwisolver+shelf-packer. Deterministic. For CI/tests. Zero API cost.
- `PromptedGenerator`: claude-sonnet with heavy prompting + JSON-schema-constrained output. Runtime default until FineTuned exists.
- `FineTunedGenerator`: placeholder that raises `NotImplementedError` until model is trained.

Selected via `LAYOUT_GENERATOR=stub|prompted|finetuned` env var.

### 4. Verifiers are hard gates (A, C) + advisor (B)

- **Layer A (geometry, Shapely)**: hard reject. Non-negotiable — a plan with overlapping rooms is not a plan.
- **Layer C (code checker)**: hard reject. If it violates egress/corridor/door/stair rules, it's not deliverable.
- **Layer B (structural sanity)**: advisory only. Attaches warnings to the Layout, doesn't reject. Scores affect best-of-N ranking.

### 5. Best-of-N loop

```
spec → generator.generate(spec, n=8)
     → filter through Layer A (drop failed)
     → filter through Layer C (drop failed)
     → score survivors: user constraints (adjacency, area targets) + Layer B advisories
     → return top-K with per-Layout audit manifest
```

If zero survive: return `GenerationFailure` with reason breakdown per verifier. Do not fall back to invalid geometry.

### 6. Audit manifest is the moat

Every returned Layout ships with:
```json
{
  "generator": "prompted-claude-sonnet-4-5",
  "generator_version": "2026-07-05",
  "verifiers": {
    "layer_a": {"passed": true, "checks": [...]},
    "layer_c": {"passed": true, "rules_checked": ["egress_window", ...], "citations": [...]},
    "layer_b": {"warnings": [...], "score": 0.87}
  },
  "generated_at": "2026-07-05T20:30:00Z",
  "spec_hash": "sha256:..."
}
```

This is what makes the platform defensible: reproducible, auditable, and per-jurisdiction traceable. Works with **any** generator behind the interface.

---

## Non-Goals for Architecture C

- Not training the fine-tuned model (that's a separate corpus + infra project)
- Not building the partner-firm ingest pipeline (DWG→schema parser)
- Not adding new IRC rules beyond the initial 5 (extensible via rule registry)
- Not rewriting the exporter or previewer (Phase 10/11 DRAFTs still relevant, target new Layout schema)
- Not touching the intent_parser's FloorPlanOp system for edits (edit flow keeps working)

---

## Rollout Strategy

1. Ship DRAFTs A → H in order. Merge each to `main` before sending the next.
2. New `/plan/generate` endpoint runs alongside existing `/plan/{id}/instruct` (edit) endpoint.
3. Frontend can migrate progressively — start using `/plan/generate` for new plans, keep `/instruct` for edits.
4. When FineTunedGenerator is ready, swap the env var. Zero downstream changes.

---

## Success Criteria

Architecture C ships successfully when:
- All 8 DRAFTs merged to `main`
- `LAYOUT_GENERATOR=prompted` runs end-to-end: NL → spec → 8 candidates → Layer A/C filter → best-of-N → Layout with audit manifest
- Layer A rejects overlapping-room test cases (100% catch rate on hand-crafted invalid Layouts)
- Layer C rejects 5 IRC-violating test cases (one per rule)
- Test suite ≥ 200 passing (currently 130)
- `LAYOUT_GENERATOR=stub` used in CI (fast, deterministic, zero API cost)
- Empirical baseline captured: mean Layer A pass rate + mean Layer C pass rate + mean latency per candidate with `PromptedGenerator`. This is the justification data for the fine-tune spend.

---

## Reference Documents

- **Original spec:** `AI Floor-Plan Generation — Pipeline Design & Handoff Spec.docx` (July 2026)
- **Options review:** July 3 2026 Slack session (Option C selected 2026-07-05)
- **Current architecture:** `cognitect-engine` skill (Hermes skill store)
- **Bug catalog:** `references/audit-june-2026.md`, `references/layout-solver-bugs.md`, `references/dxf-import-bugs.md`
