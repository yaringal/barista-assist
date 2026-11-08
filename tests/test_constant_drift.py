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

StopLatencySplitReport: the stop logic learns one stop_latency_s for every
pour. Does fast pours' real latency differ enough from normal pours' to
justify a separate fast-flow latency again? Built from the same fixture
set, using runtime.py's own _observed_stop_latency (the exact formula
_update_learned_stop_latency itself learns from) so this can never drift
out of sync with the real mechanism.

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
from real_shot_fixtures import actuator_delay_s, FIXTURES_DIR, load_real_shot  # noqa: E402

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
        actuator_delay_s=actuator_delay_s(shot),
        stopped_by_target=shot.stopped_by_target,
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


# Report-only thresholds, not runtime settings: a pour counts as "fast" at
# or above _FAST_FLOW_G_S (flow averaged over flow_window_ms at the stop
# decision); a split looks worth bringing back once fast pours' mean real
# latency differs from normal pours' by at least _SPLIT_MIN_DIFFERENCE_S,
# backed by at least _SPLIT_MIN_FAST_SHOTS fast pours and a Welch t of at
# least _SPLIT_MIN_T (roughly "unlikely to be noise").
_FAST_FLOW_G_S = 3.0
_SPLIT_MIN_DIFFERENCE_S = 0.5
_SPLIT_MIN_FAST_SHOTS = 5
_SPLIT_MIN_T = 2.0


def _welch_t(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    se = (statistics.variance(a) / len(a) + statistics.variance(b) / len(b)) ** 0.5
    return (statistics.mean(a) - statistics.mean(b)) / se if se else None


class StopLatencySplitReport(unittest.TestCase):
    """See module docstring - this never fails the suite, it only reports."""

    def test_report_whether_fast_pours_need_their_own_latency(self) -> None:
        calibration = definitions.load_definitions().stop_latency_calibration
        normal: list[tuple[str, float, float]] = []
        fast: list[tuple[str, float, float]] = []
        for path in sorted(FIXTURES_DIR.glob("*.txt")):
            shot = load_real_shot(path.stem)
            if not shot.stopped_by_target or shot.stop_command_elapsed_ms is None:
                continue
            if shot.actual_yield_g is None:
                continue
            analysis = analyze_shot(
                shot.samples,
                target_yield_g=shot.target_yield_g,
                preinfusion_s=shot.preinfusion_s,
                baseline=None,
                expected_flow_g_s=CONFIG.expected_flow_g_s,
                config=CONFIG,
                actuator_delay_s=actuator_delay_s(shot),
                stopped_by_target=shot.stopped_by_target,
            )
            if analysis.invalid_reason is not None:
                continue  # mirrors _async_finalize: invalid shots never feed learning
            result = runtime_module.BaristaRuntime._observed_stop_latency(
                shot.samples,
                shot.stop_command_elapsed_ms,
                shot.actual_yield_g,
                window_ms=calibration["flow_window_ms"],
                min_flow_g_s=calibration["min_flow_for_learning_g_s"],
            )
            if result is None:
                continue
            flow, latency = result
            (fast if flow >= _FAST_FLOW_G_S else normal).append((path.stem, flow, latency))

        print("\n=== Stop-latency split report (one learned latency vs. normal/fast) ===")
        for label, rows in (("normal", normal), (f"fast (>= {_FAST_FLOW_G_S:g} g/s)", fast)):
            if rows:
                latencies = [lat for _name, _flow, lat in rows]
                print(
                    f"  {label}: n={len(rows)} real latency mean={statistics.mean(latencies):.2f}s "
                    f"median={statistics.median(latencies):.2f}s, flow "
                    f"{min(f for _n, f, _l in rows):.2f}-{max(f for _n, f, _l in rows):.2f} g/s"
                )
            else:
                print(f"  {label}: n=0")
        for name, flow, latency in fast:
            print(f"    fast: {name} flow={flow:.2f} g/s latency={latency:.2f}s")

        normal_lat = [lat for _n, _f, lat in normal]
        fast_lat = [lat for _n, _f, lat in fast]
        if len(fast) < _SPLIT_MIN_FAST_SHOTS or not normal:
            verdict = (
                f"not enough fast pours yet ({len(fast)} of {_SPLIT_MIN_FAST_SHOTS} needed) "
                "- keep one latency"
            )
        else:
            difference = statistics.mean(fast_lat) - statistics.mean(normal_lat)
            t = _welch_t(fast_lat, normal_lat)
            t_text = f"{t:.2f}" if t is not None else "n/a"
            if abs(difference) >= _SPLIT_MIN_DIFFERENCE_S and t is not None and abs(t) >= _SPLIT_MIN_T:
                verdict = (
                    f"fast pours differ by {difference:+.2f}s (Welch t={t_text}) - a separate "
                    "fast-flow latency may be worth bringing back (replay the live trigger "
                    "before deciding)"
                )
            else:
                verdict = (
                    f"no meaningful difference ({difference:+.2f}s, Welch t={t_text}) "
                    "- keep one latency"
                )
        print(f"  Verdict: {verdict}")
        print("  Not a pass/fail check - see definitions.yaml's stop_latency_calibration comment.\n")


# Report-only threshold, not a runtime setting: a fixed flow_start_lag_s
# looks worth revisiting (re-tuning, or learning it per installation) once
# the fixtures' median real lag drifts this far from it.
_FLOW_START_LAG_DRIFT_S = 0.5


class FlowStartLagReport(unittest.TestCase):
    """See module docstring - this never fails the suite, it only reports.
    preinfusion_timing.flow_start_lag_s is a fixed value (the median gap
    between a Bot-held pre-infusion's end and the first real flow); this
    re-measures that gap across every valid Bot-held fixture."""

    def test_report_flow_start_lag_against_real_fixtures(self) -> None:
        timing = definitions.load_definitions().preinfusion_timing
        band_start_s = float(timing["band_start_s"])
        configured_s = float(timing["flow_start_lag_s"])
        gaps: list[tuple[str, float]] = []
        for path in sorted(FIXTURES_DIR.glob("*.txt")):
            shot = load_real_shot(path.stem)
            if not shot.adapt_pi:
                continue
            analysis = analyze_shot(
                shot.samples,
                target_yield_g=shot.target_yield_g,
                preinfusion_s=shot.preinfusion_s,
                baseline=None,
                expected_flow_g_s=CONFIG.expected_flow_g_s,
                config=CONFIG,
                actuator_delay_s=actuator_delay_s(shot),
                stopped_by_target=shot.stopped_by_target,
            )
            if analysis.invalid_reason is not None or analysis.t_first_flow_ms is None:
                continue
            gaps.append((path.stem, analysis.t_first_flow_ms / 1000 - (band_start_s + shot.preinfusion_s)))

        print("\n=== Flow-start lag report (preinfusion_timing.flow_start_lag_s) ===")
        if not gaps:
            print("  no valid Bot-held fixtures\n")
            return
        values = sorted(gap for _name, gap in gaps)
        median = statistics.median(values)
        q1, _q2, q3 = statistics.quantiles(values, n=4) if len(values) >= 2 else (median, median, median)
        print(
            f"  n={len(values)} median={median:.2f}s IQR=[{q1:.2f}, {q3:.2f}]s "
            f"range=[{values[0]:.2f}, {values[-1]:.2f}]s; configured {configured_s:g}s"
        )
        for name, gap in gaps:
            print(f"    {name}: {gap:+.2f}s")
        if abs(median - configured_s) >= _FLOW_START_LAG_DRIFT_S:
            verdict = (
                f"median drifted {median - configured_s:+.2f}s from the configured value - "
                "re-tune it, or consider learning it per installation"
            )
        else:
            verdict = "median still matches the configured value - keep it fixed"
        print(f"  Verdict: {verdict}")
        print(
            "  Not a pass/fail check - long gaps are usually restrictive/choked pucks, which "
            "is why the median (not the mean) is compared.\n"
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
                    actuator_delay_s=actuator_delay_s(shot),
                    stopped_by_target=shot.stopped_by_target,
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
