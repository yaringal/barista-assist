"""Tests for flavor_correction.py's Phase 5 resolve_flavor_state() - the
full escalation state machine from docs/todo/LEVER_SEQUENCING_PLAN.md
§3.2/§3.3 (primary intervention on first report, repeat-on-better,
confirm-then-escalate-or-revert on same/worse, reset on balanced)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402

flavor_correction = ha_stubs.import_barista_module("flavor_correction")

resolve_flavor_state = flavor_correction.resolve_flavor_state

# Mirrors the shape (not necessarily the exact values) of
# expert_rules.flavor_correction in definitions.yaml - sour_sharp/
# bitter_harsh have an escalation lever, thin_weak/dry_astringent
# deliberately don't (matching the real config exactly).
CONFIG = {
    "minimum_meaningful_step": {
        "temperature_offset_c": 1,
        "dose_g": 0.5,
        "target_yield_g": 2.5,
    },
    "tags": {
        "sour_sharp": {
            "lever": "yield",
            "direction": "increase",
            "delta_g": 4,
            "escalation": {"lever": "temperature", "direction": "increase"},
        },
        "bitter_harsh": {
            "lever": "yield",
            "direction": "decrease",
            "escalation": {"lever": "temperature", "direction": "decrease"},
        },
        "thin_weak": {"lever": "dose", "direction": "increase", "delta_g": 0.5},
        "dry_astringent": {"lever": "yield", "direction": "decrease"},
    },
}


class ResolveFlavorStateTests(unittest.TestCase):
    def test_empty_history_recommends_nothing(self):
        state = resolve_flavor_state([], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertEqual(state["next_question"], "tag")
        self.assertIsNone(state["active_tag"])

    def test_first_report_recommends_the_primary_lever_immediately(self):
        """No persistence gate on the first report - this is the behavior
        change from the old require_persistent_pattern_shots-gated
        function (docs/todo/LEVER_SEQUENCING_PLAN.md §3.2 point 1)."""
        state = resolve_flavor_state(["sour_sharp"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "increase", "delta": 4.0},
        )
        self.assertEqual(state["next_question"], "outcome")
        self.assertEqual(state["active_tag"], "sour_sharp")

    def test_a_tag_with_no_delta_g_falls_back_to_minimum_meaningful_step(self):
        state = resolve_flavor_state(["bitter_harsh"], CONFIG)
        self.assertEqual(state["recommendation"]["field"], "target_yield_g")
        self.assertEqual(state["recommendation"]["delta"], 2.5)
        self.assertEqual(state["recommendation"]["direction"], "decrease")

    def test_better_repeats_the_same_stage_s_delta(self):
        state = resolve_flavor_state(["sour_sharp", "better"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "increase", "delta": 4.0},
        )
        self.assertEqual(state["next_question"], "outcome")

    def test_better_can_repeat_more_than_once(self):
        state = resolve_flavor_state(["sour_sharp", "better", "better"], CONFIG)
        self.assertIsNotNone(state["recommendation"])
        self.assertEqual(state["next_question"], "outcome")

    def test_same_stops_and_recommends_nothing_while_confirming(self):
        state = resolve_flavor_state(["sour_sharp", "same"], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertEqual(state["next_question"], "tag")
        self.assertEqual(state["active_tag"], "sour_sharp")

    def test_worse_reverts_the_last_stage_s_direction_once(self):
        """Overshoot: the "worse" answer reverses direction (yield increase
        -> decrease), same magnitude, before the confirmation step."""
        state = resolve_flavor_state(["sour_sharp", "worse"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "decrease", "delta": 4.0},
        )
        self.assertEqual(state["next_question"], "tag")

    def test_confirmation_with_same_tag_escalates_when_escalation_defined(self):
        """sour_sharp defines an escalation lever (temperature) - the
        confirmation tag matching the active tag steps up to it, using
        minimum_meaningful_step since escalation never carries its own
        delta_g."""
        state = resolve_flavor_state(["sour_sharp", "same", "sour_sharp"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {
                "lever": "temperature",
                "field": "temperature_offset_c",
                "direction": "increase",
                "delta": 1.0,
            },
        )
        self.assertEqual(state["next_question"], "outcome")

    def test_confirmation_after_worse_also_escalates_at_normal_direction(self):
        """The revert only applies to the one recommendation shown right
        after "worse" - the escalation step itself is a fresh, non-reversed
        application of the next lever."""
        state = resolve_flavor_state(["sour_sharp", "worse", "sour_sharp"], CONFIG)
        self.assertEqual(state["recommendation"]["lever"], "temperature")
        self.assertEqual(state["recommendation"]["direction"], "increase")

    def test_confirmation_with_no_escalation_defined_renudges_the_same_lever(self):
        """thin_weak has no escalation block - confirmation just re-applies
        the primary lever again rather than erroring or silently doing
        nothing (a project implementation choice, not sourced - see module
        docstring)."""
        state = resolve_flavor_state(["thin_weak", "same", "thin_weak"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "dose", "field": "dose_g", "direction": "increase", "delta": 0.5},
        )
        self.assertEqual(state["next_question"], "outcome")

    def test_balanced_fully_resets_state(self):
        state = resolve_flavor_state(["sour_sharp", "same", "balanced"], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertEqual(state["next_question"], "tag")
        self.assertIsNone(state["active_tag"])

    def test_balanced_then_a_fresh_report_starts_over_at_primary(self):
        state = resolve_flavor_state(["sour_sharp", "same", "balanced", "bitter_harsh"], CONFIG)
        self.assertEqual(state["active_tag"], "bitter_harsh")
        self.assertEqual(state["recommendation"]["direction"], "decrease")
        self.assertEqual(state["next_question"], "outcome")

    def test_a_different_tag_during_confirmation_is_treated_as_a_fresh_report(self):
        """Reported bitter_harsh while confirming sour_sharp - not the same
        tag persisting, so this starts a fresh primary intervention for
        the new tag rather than escalating the old one."""
        state = resolve_flavor_state(["sour_sharp", "same", "bitter_harsh"], CONFIG)
        self.assertEqual(state["active_tag"], "bitter_harsh")
        self.assertEqual(state["recommendation"]["lever"], "yield")
        self.assertEqual(state["recommendation"]["direction"], "decrease")
        self.assertEqual(state["next_question"], "outcome")

    def test_an_outcome_response_with_nothing_pending_is_ignored(self):
        """Stale/out-of-order data (e.g. balanced then a leftover "better")
        shouldn't raise or fabricate a recommendation out of nothing."""
        state = resolve_flavor_state(["sour_sharp", "same", "balanced", "better"], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertIsNone(state["active_tag"])

    def test_an_unknown_tag_outside_config_is_ignored(self):
        state = resolve_flavor_state(["nonsense"], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertIsNone(state["active_tag"])


if __name__ == "__main__":
    unittest.main()
