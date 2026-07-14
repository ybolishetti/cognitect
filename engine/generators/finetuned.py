"""FineTunedGenerator — placeholder for the fine-tuned layout model.

The plan (see ARCHITECTURE_C.md §"Success Criteria"): once PromptedGenerator's
empirical Layer A + Layer C pass rates and per-candidate latencies are
captured over enough real specs, use that data to justify fine-tuning a
smaller model (llama-3-8b or similar) on <FloorPlanSpec, Layout> pairs
harvested from the prompted path's survivors. The fine-tuned model becomes
the runtime default when its (pass_rate × 1/latency) exceeds prompted's.

This module ships the SKELETON so:
  1. `GeneratorFactory.by_name("finetuned")` no longer raises about a future
     DRAFT — it raises `NotImplementedError` from the generator itself, with
     the concrete TODO block below.
  2. Downstream code (DRAFT 8's /plan/generate route, monitoring dashboards,
     env-var docs) can reference `FineTunedGenerator` today without adding
     a follow-up when the real implementation lands.

Architecture rule (inherited): NEVER touches the LLM directly at import
time. NEVER touches geometry. Just fulfils the LayoutGenerator contract.
"""

from __future__ import annotations

from typing import Optional

from engine.generators.base import LayoutGenerator
from engine.layout import FloorPlanSpec, Layout


FINETUNED_VERSION = "placeholder-2026-07-14"  # bumps to a real semver on first real impl


class FineTunedGenerator(LayoutGenerator):
    """Placeholder for the fine-tuned layout model. Not yet trained.

    TODO(fine-tune):
      1. Data collection — harvest <FloorPlanSpec, Layout> pairs from
         PromptedGenerator's Layer-A/C-surviving candidates over N real
         user specs (target: N >= 1000 pairs before starting a run).
      2. Model choice — starting candidate is llama-3-8b (Together AI or
         Fireworks) with LoRA over the emit_layout tool call. Alternatives:
         qwen-2.5-7b-coder, deepseek-coder-v2-lite. Whichever hits Layer A
         + Layer C pass rate >= PromptedGenerator's at less than 1/3 the
         per-candidate latency wins.
      3. Serving — Baseten or Together dedicated endpoint. Env var
         FINETUNED_MODEL_URL selects the endpoint at construction time
         (mirror the PromptedGenerator client-DI pattern).
      4. Rollout — canary at LAYOUT_GENERATOR=finetuned behind a percentage
         gate in DRAFT 8; keep prompted as fallback until confidence.

    Until (1)-(4) land, this class raises NotImplementedError from
    generate() so anyone who accidentally sets LAYOUT_GENERATOR=finetuned
    in production fails loud, fast, and with a clear pointer.
    """

    MODEL = "placeholder"  # bumps to the actual model name on first real impl

    def __init__(
        self,
        model_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """Constructor accepts (model_url, api_key) so tests and future real
        code can DI both. Both default to None today — the real impl will
        read env vars FINETUNED_MODEL_URL + FINETUNED_API_KEY the same way
        PromptedGenerator reads COGNITECT_CLAUDE_API_KEY.

        Constructor deliberately does NOT raise on missing env vars. The
        raise happens in generate() so `GeneratorFactory.by_name("finetuned")`
        is safe to call from monitoring / health-check code paths that need
        an instance to exist but never actually invoke it.
        """
        self._model_url = model_url
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "finetuned"

    def generate(self, spec: FloorPlanSpec) -> list[Layout]:
        """Not yet implemented. See the class docstring's TODO block.

        Raises `NotImplementedError` with a message pointing at both:
          - the TODO in this file (implementation roadmap)
          - LAYOUT_GENERATOR=prompted as the fallback (runtime path today)
        """
        raise NotImplementedError(
            "FineTunedGenerator is a placeholder — the fine-tuned model is "
            "not yet trained. See engine/generators/finetuned.py class "
            "docstring for the roadmap. Set LAYOUT_GENERATOR=prompted (or "
            "'stub' for CI) to use one of the shipping generators."
        )
