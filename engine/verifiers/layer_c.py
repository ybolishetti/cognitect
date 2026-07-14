"""Layer C — the code-compliance hard gate (Architecture C).

Runs every registered CodeRule against a Layout, aggregates their
failures into a single VerifierResult, and returns it. Never mutates
the Layout. Never raises (rule bugs surface as failures on the rule
itself, not exceptions — see the try/except wrapper below).

Unlike Layer A (which is jurisdiction-agnostic geometry), Layer C is
inherently jurisdiction-scoped. This DRAFT ships IRC-2021 only; a
Layout tagged with any other jurisdiction produces a single
`unsupported_jurisdiction` failure and no rules run.
"""

from __future__ import annotations

import logging
import time

from engine.layout import Layout, SiteConstraints, VerifierResult
from engine.verifiers.rules import IRC_2021_RULES, CodeCheckContext, CodeRule

logger = logging.getLogger(__name__)


def verify_layer_c(
    layout: Layout,
    rules: list[CodeRule] | None = None,
    site: SiteConstraints | None = None,
) -> VerifierResult:
    """Run all Layer C code rules against a Layout.

    Args:
      layout: the Layout to check
      rules:  overrides the default IRC_2021_RULES (used by tests)
      site:   overrides layout's implicit jurisdiction. If None, uses IRC-2021.

    Returns a VerifierResult with verifier_name="layer_c_code".
    """
    start = time.perf_counter()
    rules = rules if rules is not None else IRC_2021_RULES
    site = site if site is not None else SiteConstraints()  # defaults to IRC-2021
    ctx = CodeCheckContext(jurisdiction=site.jurisdiction, site=site)

    checks_run = [r.rule_id for r in rules]
    all_failures: list[dict] = []

    if ctx.jurisdiction != "IRC-2021":
        all_failures.append({
            "check": "unsupported_jurisdiction",
            "citation": "",
            "detail": (
                f"jurisdiction={ctx.jurisdiction!r} is not supported by Layer C "
                f"in this DRAFT (IRC-2021 only). Add a rule set to "
                f"engine/verifiers/rules/ and register it in IRC_2021_RULES."
            ),
            "entity_ids": [],
        })
    else:
        for rule in rules:
            if not rule.applies_to_jurisdiction(ctx.jurisdiction):
                continue
            try:
                all_failures.extend(rule.check(layout, ctx))
            except Exception as exc:  # noqa: BLE001 — rule bug shouldn't crash the layer
                logger.exception("Layer C rule %s raised", rule.rule_id)
                all_failures.append({
                    "check": rule.rule_id,
                    "citation": rule.citation,
                    "detail": f"rule {rule.rule_id} raised an exception (rule bug): {exc}",
                    "entity_ids": [],
                })

    # Same deterministic ordering as Layer A.
    all_failures.sort(key=lambda f: (f["check"], tuple(sorted(f.get("entity_ids") or [])), f["detail"]))

    elapsed_ms = (time.perf_counter() - start) * 1000.0

    return VerifierResult(
        verifier_name="layer_c_code",
        passed=len(all_failures) == 0,
        checks_run=checks_run,
        failures=all_failures,
        warnings=[],
        score=None,
        elapsed_ms=elapsed_ms,
    )
