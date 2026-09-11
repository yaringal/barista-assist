"""Tests for flavor_correction.py's Phase 5 resolve_flavor_state() - the
damped-step escalation state machine (docs/DESIGN.md's Phase 5): primary
lever on first/persisting report, a damped (smaller) correction on the
axis's other tag when the two share a lever ("coupled"), a fresh primary
report of that other tag when they don't ("uncoupled"), escalation once a
coupled correction converges below the lever's own minimum_meaningful_step,
and a full reset on "balanced".

The damped step is shared across a coupled pair (they're two labels for
the same physical lever) and persists across a run of the same tag being
reported - it only resets to a fresh base step when active_tag/stage
genuinely restarts (a first-ever report, an uncoupled switch, or an
escalation into a new stage). One consequence worth remembering while
reading these tests: whether a coupled overshoot produces a genuine
damped-and-applied nudge or escalates immediately can depend on *which*
tag's report started the sequence (and so set the initial step), not just
on the tags' own individually configured magnitudes - see the paired tests
below that use the same two tags in opposite order."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402

flavor_correction = ha_stubs.import_barista_module("flavor_correction")
definitions = ha_stubs.import_barista_module("definitions")

resolve_flavor_state = flavor_correction.resolve_flavor_state

# Mirrors the shape of expert_rules.flavor_correction in definitions.yaml -
# sour_sharp/bitter_harsh are coupled (same lever, opposite directions,
# both with an escalation lever); thin_weak/dry_astringent are uncoupled
# (different levers), and thin_weak deliberately has no escalation.
#
# sour_sharp's own delta_g (4) is deliberately more than double the
# target_yield_g floor (1.5), so a sequence that establishes it as the
# current step can still produce a real damped-and-applied nudge (2.0)
# afterward; bitter_harsh (no delta_g, so its own base already equals the
# floor) can't - any overshoot starting from bitter_harsh's own step
# escalates immediately. Both cases need covering.
CONFIG = {
    "minimum_meaningful_step": {
        "temperature_offset_c": 1,
        "dose_g": 0.5,
        "target_yield_g": 1.5,
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
        self.assertIsNone(state["active_tag"])

    def test_first_report_recommends_the_primary_lever_immediately(self):
        """No persistence gate on the first report."""
        state = resolve_flavor_state(["sour_sharp"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "increase", "delta": 4.0},
        )
        self.assertEqual(state["active_tag"], "sour_sharp")

    def test_a_tag_with_no_delta_g_falls_back_to_minimum_meaningful_step(self):
        state = resolve_flavor_state(["bitter_harsh"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "decrease", "delta": 1.5},
        )

    def test_the_same_tag_persisting_keeps_going_at_the_same_step(self):
        """Confirmed correct direction, just not sufficient yet - repeats
        the current step, not a smaller or larger one."""
        state = resolve_flavor_state(["sour_sharp", "sour_sharp"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "increase", "delta": 4.0},
        )
        self.assertEqual(state["active_tag"], "sour_sharp")

    def test_a_coupled_overshoot_applies_a_damped_correction_when_above_the_floor(self):
        """sour_sharp (yield, increase, step 4) then bitter_harsh (yield,
        decrease) - same lever, opposite direction, so this is an
        overshoot, not a fresh problem. The current step (4, from
        sour_sharp's own fresh report) halved is 2, still at or above this
        fixture's 1.5 floor, so it's applied directly rather than
        escalating."""
        state = resolve_flavor_state(["sour_sharp", "bitter_harsh"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "decrease", "delta": 2.0},
        )
        self.assertEqual(state["active_tag"], "bitter_harsh")

    def test_persisting_after_a_damped_correction_keeps_the_damped_step(self):
        """The fix this test guards: once an overshoot has shown that the
        original step (4) was too coarse and been damped to 2, a further
        report of the *same* (now-active) tag must keep fine-tuning at
        that already-damped scale - not jump back to bitter_harsh's own
        nominal step (1.5, smaller) or sour_sharp's original one (4,
        larger). Continuing the sequence from the test above with one more
        bitter_harsh report:"""
        state = resolve_flavor_state(["sour_sharp", "bitter_harsh", "bitter_harsh"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "decrease", "delta": 2.0},
        )
        self.assertEqual(state["active_tag"], "bitter_harsh")

    def test_a_coupled_overshoot_escalates_once_the_damped_value_is_below_the_floor(self):
        """bitter_harsh (yield, decrease, step 1.5 - its own base, since it
        has no delta_g and this fixture's floor is 1.5) then sour_sharp
        (yield, increase) - the current step (1.5) halved is 0.75, below
        the floor, so this escalates immediately rather than applying a
        sub-floor nudge.

        This also doubles as the regression test for active_tag
        reassigning to whichever tag is *currently* reported: escalation
        uses sour_sharp's own escalation direction ("increase"), not
        bitter_harsh's ("decrease") - staying anchored to the tag that
        started the sequence would have lowered temperature here, exactly
        backwards for a shot that just reported sour."""
        state = resolve_flavor_state(["bitter_harsh", "sour_sharp"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "temperature", "field": "temperature_offset_c", "direction": "increase", "delta": 1.0},
        )
        self.assertEqual(state["active_tag"], "sour_sharp")

    def test_a_coupled_overshoot_at_the_escalated_stage_with_nowhere_further_stalls(self):
        """Continuing past the previous escalation: once at the escalated
        (temperature) stage, another coupled flip has nowhere further to
        escalate to - no automatic recommendation, but active_tag stays set
        (correctly reassigned to whichever tag is reporting now) so callers
        can tell this apart from a plain reset."""
        state = resolve_flavor_state(["bitter_harsh", "sour_sharp", "bitter_harsh"], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertEqual(state["active_tag"], "bitter_harsh")

    def test_an_uncoupled_other_tag_is_treated_as_a_fresh_report(self):
        """thin_weak (dose) and dry_astringent (yield) don't share a lever -
        reporting one while tracking the other isn't a correction of
        anything, just this axis's other, independent problem, so it starts
        fresh at its own base step regardless of whatever step thin_weak
        was using."""
        state = resolve_flavor_state(["thin_weak", "dry_astringent"], CONFIG)
        self.assertEqual(
            state["recommendation"],
            {"lever": "yield", "field": "target_yield_g", "direction": "decrease", "delta": 1.5},
        )
        self.assertEqual(state["active_tag"], "dry_astringent")

    def test_an_uncoupled_tag_can_then_persist_normally(self):
        state = resolve_flavor_state(["thin_weak", "dry_astringent", "dry_astringent"], CONFIG)
        self.assertEqual(state["recommendation"]["delta"], 1.5)
        self.assertEqual(state["active_tag"], "dry_astringent")

    def test_balanced_fully_resets_state(self):
        state = resolve_flavor_state(["sour_sharp", "balanced"], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertIsNone(state["active_tag"])

    def test_balanced_then_a_fresh_report_starts_over_at_primary(self):
        state = resolve_flavor_state(["sour_sharp", "balanced", "bitter_harsh"], CONFIG)
        self.assertEqual(state["active_tag"], "bitter_harsh")
        self.assertEqual(state["recommendation"]["direction"], "decrease")
        self.assertEqual(state["recommendation"]["delta"], 1.5)

    def test_an_unknown_tag_outside_config_is_ignored(self):
        state = resolve_flavor_state(["nonsense"], CONFIG)
        self.assertIsNone(state["recommendation"])
        self.assertIsNone(state["active_tag"])

    def test_an_unknown_tag_does_not_disturb_existing_state(self):
        state = resolve_flavor_state(["sour_sharp", "nonsense"], CONFIG)
        self.assertEqual(state["active_tag"], "sour_sharp")
        self.assertEqual(state["recommendation"]["delta"], 4.0)


class RealDefinitionsConfigTests(unittest.TestCase):
    """Ties the escalation state machine back to today's actual
    definitions.yaml - derives the expected outcome from the live config
    at test time rather than hardcoding either branch, so a future retune
    of delta_g/minimum_meaningful_step can't silently leave this suite
    asserting a stale result (see docs/DESIGN.md's Phase 5 for why this
    branches on live numbers instead of always going one way: whichever
    tag's own step exceeds the field's floor by more than double is the
    one that, reported first, lets a subsequent overshoot land a genuine
    damped nudge instead of escalating immediately)."""

    @classmethod
    def setUpClass(cls):
        cls.config = definitions.load_definitions().expert_rules["flavor_correction"]

    def _own_primary_step(self, tag: str) -> float:
        """The same fallback rule flavor_correction._base_step applies at
        the primary stage: delta_g if the tag defines one, else the
        field's own minimum_meaningful_step."""
        tag_config = self.config["tags"][tag]
        delta_g = tag_config.get("delta_g")
        if delta_g is not None:
            return float(delta_g)
        return float(self.config["minimum_meaningful_step"]["target_yield_g"])

    def _assert_overshoot_matches_expectation(self, first: str, second: str) -> None:
        """`first` reports fresh, establishing its own step; `second` then
        overshoots (coupled, since both are on the extraction axis's
        shared yield lever at the primary stage). Whether that damped step
        clears the floor - and so whether this applies a real yield nudge
        or escalates straight to temperature - is derived from the live
        config, not assumed."""
        floor = self.config["minimum_meaningful_step"]["target_yield_g"]
        damped_step = self._own_primary_step(first) * flavor_correction._DAMPING_RATIO
        state = resolve_flavor_state([first, second], self.config)
        recommendation = state["recommendation"]
        self.assertIsNotNone(recommendation)
        if damped_step >= floor:
            self.assertEqual(recommendation["lever"], "yield")
            self.assertEqual(recommendation["direction"], self.config["tags"][second]["direction"])
            self.assertEqual(recommendation["delta"], damped_step)
            self.assertEqual(state["active_tag"], second)
        else:
            self.assertEqual(recommendation["lever"], "temperature")
            self.assertEqual(
                recommendation["direction"], self.config["tags"][second]["escalation"]["direction"]
            )
            self.assertEqual(state["active_tag"], second)

    def test_an_overshoot_following_sour_sharp_s_own_step_matches_the_live_config(self):
        self._assert_overshoot_matches_expectation("sour_sharp", "bitter_harsh")

    def test_an_overshoot_following_bitter_harsh_s_own_step_matches_the_live_config(self):
        self._assert_overshoot_matches_expectation("bitter_harsh", "sour_sharp")


if __name__ == "__main__":
    unittest.main()
