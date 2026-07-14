"""Layer C rule registry.

`IRC_2021_RULES` is the canonical registry of instantiated rules that
`verify_layer_c` walks by default. Rules are instances (not classes) so
future parameterized rules work without registry changes.

To add a rule:
  1. Create engine/verifiers/rules/<rule_id>.py with a CodeRule subclass
  2. Import it here and append to IRC_2021_RULES in citation order
  3. Add a per-rule test file at tests/test_layer_c_rules_<rule_id>.py
"""

from __future__ import annotations

from engine.verifiers.rules.base import CodeCheckContext, CodeRule
from engine.verifiers.rules.irc_r303_1 import IRC_R303_1_WetRoomOpening
from engine.verifiers.rules.irc_r305_1 import IRC_R305_1_MinCeilingHeight
from engine.verifiers.rules.irc_r310_1 import IRC_R310_1_BedroomEgressWindow
from engine.verifiers.rules.irc_r311_2 import IRC_R311_2_PrimaryExitDoorWidth
from engine.verifiers.rules.irc_r311_7 import IRC_R311_7_HallwayWidth

IRC_2021_RULES: list[CodeRule] = [
    IRC_R303_1_WetRoomOpening(),
    IRC_R305_1_MinCeilingHeight(),
    IRC_R310_1_BedroomEgressWindow(),
    IRC_R311_2_PrimaryExitDoorWidth(),
    IRC_R311_7_HallwayWidth(),
]


def lookup_rule(rule_id: str) -> CodeRule:
    """Return the rule with the given rule_id, or raise KeyError."""
    for rule in IRC_2021_RULES:
        if rule.rule_id == rule_id:
            return rule
    raise KeyError(f"No IRC-2021 rule with rule_id={rule_id!r}")


__all__ = [
    "CodeCheckContext",
    "CodeRule",
    "IRC_2021_RULES",
    "lookup_rule",
]
