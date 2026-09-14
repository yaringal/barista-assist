"""Standing drift-detection reports for constants that can never be
auto-tuned because there's no automatic ground truth to check them against
(docs/DESIGN.md's Phase 3b) - NOT pass/fail gates. As
tests/fixtures/real_shots/ accumulates new real shots over time, these flag
anything worth a human's attention, on the working assumption that the
fixture set grows representative of real usage as it grows.

§2.9 (ConstantDriftReport): five flow_analysis.py constants, each hand-tuned
against one specific real anomalous shot a human diagnosed by eye, with no
automatic ground truth for what they detect. Mechanism: for each real
fixture and each monitored constant, re-run analyze_shot with that one
constant nudged +/-PERTURBATION_FRACTION and compare the result against the
unperturbed one - a difference means this fixture sits close enough to the
threshold that a human should look, not that anything is actually wrong.

§2.6 (StopLatencyBucketDriftReport): does _STOP_LATENCY_BUCKET_CUTOFF_G_S
still sit in a genuine low-density gap between the two flow-rate
populations it splits shots into? Built from the same fixture set, using
runtime.py's own _observed_stop_latency (the exact formula
_update_learned_stop_latency itself nudges stop_latency_normal_s/elevated_s
with) so this can never drift out of sync with the real mechanism.

docs/todo/ADAPTIVE_LEARNING_PLAN.md §3 (GrindFlavorConsistencyReport): does
duration_ratio far from 1.0 actually correlate with the taste complaint a
real recorded shot's own flavor tag would predict - or not? Ground-truth
taste labels per shot didn't exist before flavor_correction.py's
taste-feedback notifications; now that fixtures can carry
flavor_extraction_tag/flavor_mouthfeel_tag (real_shot_fixtures.py), this
checks whether a shot classified too_fast/too_restrictive (or healthy) and
tagged with an under-/over-extraction-signature complaint actually agree on
direction.
"""

from __future__ import annotations

import dataclasses
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402
from real_shot_fixtures import FIXTURES_DIR, load_real_shot  # noqa: E402

flow_analysis = ha_stubs.import_barista_module("flow_analysis")
definitions = ha_stubs.import_barista_module("definitions")
runtime_module = ha_stubs.import_barista_module("runtime")
analyze_shot = flow_analysis.analyze_shot

# The real, sourced config - see test_flow_analysis.py's own CONFIG for why
# this isn't a hardcoded/default FlowAnalysisConfig.
CONFIG = flow_analysis.FlowAnalysisConfig(**definitions.load_definitions().flow_analysis_constants)

# How far to nudge each monitored constant when checking whether a fixture
# sits close to it - not a research-backed number, just enough to catch a
# near-miss without flagging everything (docs/DESIGN.md's Phase 3b).
# Confirmed this actually discriminates: at this value none of the 8
# fixtures on hand as of this writing flip (a legitimately empty report, not
# a bug - see module docstring); a much larger nudge (0.8) does produce
# flips, confirming the mechanism works.
PERTURBATION_FRACTION = 0.2

# docs/DESIGN.md's own constant names -> the
# matching FlowAnalysisConfig field.
MONITORED_CONSTANTS = {
    "_DISTURBANCE_SUSTAIN_MS": "disturbance_sustain_ms",
    "_MAX_PLAUSIBLE_WEIGHT_DROP_G": "max_plausible_weight_drop_g",
    "_DISTURBANCE_DETECTION_FLOOR_G": "disturbance_detection_floor_g",
    "_FIRST_FLOW_SUSTAIN_MS": "first_flow_sustain_ms",
    "FIRST_FLOW_THRESHOLD_G_S": "first_flow_threshold_g_s",
}


def _classify(shot, config) -> tuple:
    result = analyze_shot(
        shot.samples,
        target_yield_g=shot.target_yield_g,
        preinfusion_s=shot.preinfusion_s,
        baseline=None,
        expected_flow_g_s=config.expected_flow_g_s,
        config=config,
    )
    return (result.classification, result.invalid_reason)


class ConstantDriftReport(unittest.TestCase):
    """See module docstring - this never fails the suite, it only reports."""

    def test_report_fixtures_near_a_monitored_threshold(self) -> None:
        findings = []
        for path in sorted(FIXTURES_DIR.glob("*.txt")):
            shot = load_real_shot(path.stem)
            baseline_result = _classify(shot, CONFIG)
            for constant_name, field in MONITORED_CONSTANTS.items():
                original = getattr(CONFIG, field)
                for multiplier in (1 - PERTURBATION_FRACTION, 1 + PERTURBATION_FRACTION):
                    perturbed_config = dataclasses.replace(
                        CONFIG, **{field: original * multiplier}
                    )
                    perturbed_result = _classify(shot, perturbed_config)
                    if perturbed_result != baseline_result:
                        findings.append(
                            f"{path.stem}: {constant_name} "
                            f"({original!r} -> {original * multiplier:.4g}) "
                            f"flips classification from {baseline_result} to "
                            f"{perturbed_result}"
                        )
                        break

        if findings:
            print(
                "\n=== Constant drift report "
                "(docs/DESIGN.md's Phase 3b) ==="
            )
            for line in findings:
                print(f"  {line}")
            print(
                "  These are near-misses, not failures - review whether the "
                "constant still looks right against real data, per this "
                "project's own validate-constants-against-real-shot-fixtures "
                "rule.\n"
            )


class StopLatencyBucketDriftReport(unittest.TestCase):
    """See module docstring - this never fails the suite, it only reports."""

    def test_report_bucket_cutoff_against_real_fixtures(self) -> None:
        calibration = definitions.load_definitions().stop_latency_calibration
        fixture_paths = sorted(FIXTURES_DIR.glob("*.txt"))
        pairs = []
        for path in fixture_paths:
            shot = load_real_shot(path.stem)
            if shot.stop_command_elapsed_ms is None or shot.actual_yield_g is None:
                continue
            result = runtime_module.BaristaRuntime._observed_stop_latency(
                shot.samples,
                shot.stop_command_elapsed_ms,
                shot.actual_yield_g,
                window_ms=CONFIG.smoothing_window_ms,
                min_flow_g_s=calibration["min_flow_for_learning_g_s"],
            )
            if result is not None:
                pairs.append(result)

        cutoff = calibration["bucket_cutoff_g_s"]
        normal = sorted(flow for flow, _latency in pairs if flow < cutoff)
        elevated = sorted(flow for flow, _latency in pairs if flow >= cutoff)

        print(
            "\n=== Stop-latency bucket drift report "
            "(docs/DESIGN.md's Phase 3b) ==="
        )
        print(
            f"  {len(pairs)}/{len(fixture_paths)} fixtures usable "
            f"(normal={len(normal)}, elevated={len(elevated)}, cutoff={cutoff:g} g/s)"
        )
        if pairs:
            all_flows = sorted(flow for flow, _latency in pairs)
            bin_width = 0.5
            lo = int(all_flows[0] // bin_width)
            hi = int(all_flows[-1] // bin_width) + 1
            print("  flow_at_decision histogram (bin width 0.5 g/s):")
            for b in range(lo, hi):
                bin_lo, bin_hi = b * bin_width, (b + 1) * bin_width
                count = sum(1 for flow in all_flows if bin_lo <= flow < bin_hi)
                marker = " <-- cutoff" if bin_lo <= cutoff < bin_hi else ""
                print(f"    [{bin_lo:4.1f}, {bin_hi:4.1f}): {'#' * count} ({count}){marker}")
            for label, flows in (("normal", normal), ("elevated", elevated)):
                if flows:
                    latencies = [lat for flow, lat in pairs if (flow < cutoff) == (label == "normal")]
                    print(
                        f"  {label}: flow range [{flows[0]:.2f}, {flows[-1]:.2f}] g/s, "
                        f"observed latency mean={statistics.mean(latencies):.2f}s "
                        f"median={statistics.median(latencies):.2f}s (n={len(latencies)})"
                    )
            defaults = definitions.load_definitions().defaults["controller"]
            print(
                f"  current fixed defaults: stop_latency_normal_s="
                f"{defaults['stop_latency_normal_s']}s, stop_latency_elevated_s="
                f"{defaults['stop_latency_elevated_s']}s"
            )
        print(
            "  Not a pass/fail check - eyeball whether the cutoff still sits "
            "in a low-density gap, and whether there's enough data yet to "
            "consider a continuous flow->latency regression instead of the "
            "two-bucket model (docs/DESIGN.md's Phase 3b).\n"
        )


# A tag's expert_rules.flavor_correction.tags[tag] "lever"/"direction" ->
# which extraction direction it signals (see docs/data/DIAL_IN_RULES.md's
# own reasoning for sour_sharp/bitter_harsh/dry_astringent): a "yield"-lever
# tag asking to "increase" yield is an under-extraction complaint (predicts
# a fast/short shot, duration_ratio < 1.0); "decrease" is an over-extraction
# complaint (predicts a slow/restrictive shot, duration_ratio > 1.0).
# thin_weak (lever: dose) has no duration_ratio-relevant direction at all -
# excluded by the lever check below, not a special case. "balanced" has
# neither field set (definitions.yaml's own tags.balanced entry), so it's
# excluded the same way - nothing to check a "no complaint" report against.
_DIRECTION_BY_TAG_DIRECTION = {"increase": "under", "decrease": "over"}
_DIRECTION_BY_CLASSIFICATION = {"too_fast": "under", "too_restrictive": "over"}


class GrindFlavorConsistencyReport(unittest.TestCase):
    """See module docstring - this never fails the suite, it only reports."""

    def test_report_duration_ratio_disagreements_with_recorded_flavor_tags(self) -> None:
        tags_config = definitions.load_definitions().expert_rules["flavor_correction"]["tags"]

        def tag_direction(tag: str | None) -> str | None:
            if tag is None:
                return None
            config = tags_config.get(tag)
            if config is None or config.get("lever") != "yield":
                return None
            return _DIRECTION_BY_TAG_DIRECTION[config["direction"]]

        checked = 0
        findings = []
        for path in sorted(FIXTURES_DIR.glob("*.txt")):
            shot = load_real_shot(path.stem)
            for axis, tag in (
                ("extraction", shot.flavor_extraction_tag),
                ("mouthfeel", shot.flavor_mouthfeel_tag),
            ):
                expected = tag_direction(tag)
                if expected is None:
                    continue
                checked += 1
                result = analyze_shot(
                    shot.samples,
                    target_yield_g=shot.target_yield_g,
                    preinfusion_s=shot.preinfusion_s,
                    baseline=None,
                    expected_flow_g_s=CONFIG.expected_flow_g_s,
                    config=CONFIG,
                )
                actual = _DIRECTION_BY_CLASSIFICATION.get(str(result.classification))
                if actual is not None and actual != expected:
                    ratio = f"{result.duration_ratio:.3f}" if result.duration_ratio is not None else "n/a"
                    caveat = (
                        " (bitter_harsh's own fines_caveat: this can be a weaker signal - "
                        "bitter without watery/drying mouthfeel may itself be "
                        "under-extraction/channeling, not over-extraction)"
                        if tag == "bitter_harsh"
                        else ""
                    )
                    findings.append(
                        f"{path.stem} ({axis}={tag}, implies '{expected}'-extracted): "
                        f"classified {result.classification} (duration_ratio={ratio}){caveat}"
                    )

        print(
            "\n=== Grind-vs-flavor consistency report "
            "(docs/todo/ADAPTIVE_LEARNING_PLAN.md §3) ==="
        )
        print(
            f"  {checked} fixture/axis pair(s) carried a directional flavor "
            "tag (sour_sharp/bitter_harsh/dry_astringent) to check "
            "duration_ratio's implied direction against"
        )
        if findings:
            for line in findings:
                print(f"  {line}")
        print(
            "  Not a pass/fail check - a disagreement means timing and "
            "taste point opposite directions for this shot, worth a "
            "human's attention once enough tagged fixtures exist to see a "
            "real pattern rather than one noisy data point (docs/todo/"
            "ADAPTIVE_LEARNING_PLAN.md §3).\n"
        )


if __name__ == "__main__":
    unittest.main()
