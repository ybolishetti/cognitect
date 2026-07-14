"""Layer C rule interface.

Each IRC rule is a CodeRule subclass. Rules are pure: they receive a
Layout + jurisdiction context, return a list of failure dicts (never
raise). The orchestrator (verify_layer_c) walks the registry, calls each
rule, and aggregates results into a single VerifierResult.

Architecture rule: a CodeRule NEVER touches the LLM, NEVER mutates the
Layout, NEVER calls other rules. It reads the Layout, applies its check,
and returns failures. That's it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from engine.layout import Layout, SiteConstraints


@dataclass(frozen=True)
class CodeCheckContext:
    """Read-only context passed to every rule.

    Splits jurisdiction + site data out of the Layout so rules don't have
    to know how to dig it out themselves. Also gives DRAFT 8 a clean place
    to inject per-request overrides (a jurisdiction-picker flag, a test
    "skip rule X" hint, etc.) without changing every rule signature.
    """

    jurisdiction: str = "IRC-2021"
    site: Optional[SiteConstraints] = None


class CodeRule(ABC):
    """Abstract base for a single code compliance rule.

    Subclasses set `rule_id` (e.g. "irc_r310_1"), `citation`
    (human-readable reference like "IRC 2021 §R310.1"), and implement
    `applies_to_jurisdiction()` + `check()`.
    """

    rule_id: str  # snake_case, matches the file name
    citation: str

    def applies_to_jurisdiction(self, jurisdiction: str) -> bool:
        """Return True if this rule applies for the given jurisdiction.

        Default: applies to IRC-2021 only. Override for rules that also
        apply under other codes.
        """
        return jurisdiction == "IRC-2021"

    @abstractmethod
    def check(self, layout: Layout, ctx: CodeCheckContext) -> list[dict]:
        """Return a list of failure dicts. Empty list = rule passed.

        Each failure dict MUST have keys:
          - "check":     the rule_id (NOT the class name)
          - "detail":    human-readable one-line failure description
          - "entity_ids": sorted list of Layout entity ids implicated
          - "citation":  self.citation (for audit manifest)
        """
