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


if __name__ == "__main__":
    unittest.main()
