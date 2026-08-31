"""Stage-1 shot flow-curve analysis: smoothing, timing/flow features, and
classification.

Pure computation, no Home Assistant or database dependency, so it can be
unit-tested against synthetic mass curves. See docs/DESIGN.md sections 8-13
for the feature set and diagnostic-architecture rationale this implements.

The "too fast" / "too restrictive" duration thresholds and the mechanical-
suspicion thresholds below are deliberately simple placeholders — fixed
priors, not values derived from this project's own shot data. Replacing
them with data-driven thresholds is tracked as its own follow-up phase in
docs/DESIGN.md.

This module blends two different kinds of "prior" against a per-bag
baseline, and deliberately treats them differently:

- The expected flow rate (what pace counts as "too fast"/"too restrictive"
  for this bag) is a reference point, not a safety boundary: there's no
  problem in a bag genuinely pouring faster or slower than the generic
  global guess, so it's fine - correct, even - for the model to shift fully
  toward this bag's own observed pace as shots accumulate. `expected_s`
  below is a Bayesian shrinkage estimate: a weighted blend of the fixed
  prior and this bag's own median flow rate, sliding smoothly toward the
  bag's data as `baseline.shot_count` grows, with no hard cutover point.

- Mechanical-health suspicion (docs/DESIGN.md section 13's "did resistance
  appear to collapse unexpectedly?") is judged primarily against a fixed
  prior so it works from a bag's very first shot: under roughly constant
  pump pressure, flow accelerating upward mid/late shot is itself abnormal
  regardless of what this bag's own history looks like. Unlike the flow-rate
  reference above, this one is NOT allowed to fully self-normalize: a
  per-bag baseline can only ever raise the suspicion score, never lower it,
  because this is a classification boundary ("how much rise counts as a
  problem"), not just a reference point - letting it drift down would let a
  bag whose shots have consistently had a channeling problem "normalize"
  that pattern and stop flagging it, which is exactly the contamination
  docs/DESIGN.md section 12 warns against. The asymmetry is deliberate, not
  an inconsistency with the flow-rate blending above.

Not implemented: section 13 also asks "did the flow change smoothly?" /
"is the scale trace noisy or otherwise unreliable?" `flow_variance` and
`flow_curvature` are computed and returned for that purpose, but nothing
here classifies on them yet. Every variance-based "this trace is noisy"
threshold tried during development also fired on docs/DESIGN.md section
25's Example D (a genuine puck_prep_issue shot: flat, then a sustained
ramp) at least as strongly as on synthetic random jitter, because a
sustained trend and genuine noise both make a flow curve deviate from a
single smooth shape - only their pattern differs (persistent vs. erratic),
not their magnitude. Distinguishing them needs either a smarter noise
metric (e.g. run-length/sign-change based, not variance-based) or real
recorded scale noise to calibrate against; both are left to Phase 3b
(docs/DESIGN.md) rather than shipping a check that could misclassify a
real mechanical problem as an unrelated measurement fault.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import statistics

from .storage import ShotSample

MIN_SAMPLES = 5
MIN_BASELINE_SHOTS = 3
FIRST_FLOW_THRESHOLD_G_S = 0.3
SMOOTHING_WINDOW_MS = 500
SUSPICION_THRESHOLD = 0.6

# Rough expected total-flow rate (beverage mass / total shot time, including
# pre-infusion), used as the starting point before any bag-specific history
# exists. Anchored against docs/DESIGN.md section 25's Example A (18g -> 38g
# in 24s, explicitly "clearly fast"): 1.25 g/s puts that shot's t90 just
# inside the too-fast cutoff, which the previous placeholder (2.0 g/s) did not.
_EXPECTED_FLOW_G_S = 1.25
_TOO_FAST_FACTOR = 0.8
_TOO_RESTRICTIVE_FACTOR = 1.6

# How many "pseudo-shots" the global flow-rate prior is worth when blending
# with a bag's own healthy-shot history - shrinks toward the bag-specific
# median as shot_count grows, rather than switching over abruptly at some
# threshold. A placeholder like the other constants above.
_PRIOR_WEIGHT_SHOTS = 5.0

# Fixed prior: mid- or late-shot flow accelerating upward by this many g/s
# per second is treated as maximally suspicious, independent of any baseline.
_ABSOLUTE_ACCEL_LIMIT_G_S2 = 1.0

# A shot's first detected flow earlier than this fraction of its configured
# pre-infusion duration is implausible: pre-infusion is meant to be a
# low/no-flow soak, so flow starting well before it ends suggests the
# measurement (or the pre-infusion press itself) isn't trustworthy.
_EARLY_FLOW_FRACTION_OF_PREINFUSION = 0.5

# A raw weight reading this many grams below its own running peak so far is
# treated as the cup or scale having been disturbed, not real flow: weight
# can only rise while coffee is actually being collected, so any meaningful
# drop - however it happens, whenever it happens - means everything from
# that point on isn't trustworthy. Everything before it still is.
_MAX_PLAUSIBLE_WEIGHT_DROP_G = 0.5

# Disturbance detection only arms once the running peak has cleared this
# floor. Below it - typically pre-infusion, before any real coffee mass has
# accumulated - scale settling noise alone can span several tenths of a
# gram, comfortably exceeding _MAX_PLAUSIBLE_WEIGHT_DROP_G on its own with no
# real disturbance involved; a real shot found this live, misclassifying a
# genuine ~50g pour as invalid because of a sub-gram dip minutes before the
# real pour even began. Once a shot has genuinely accumulated this much
# weight, the same _MAX_PLAUSIBLE_WEIGHT_DROP_G drop is unambiguous
# interference, exactly as before.
_DISTURBANCE_DETECTION_FLOOR_G = 2.0

# A leading weight reading this far below zero is scale noise before it's
# settled/tared, not real data - weight is never meaningfully negative, at
# any point in a shot (ordinary scale jitter near a true zero baseline stays
# within a couple tenths of a gram either way). A live shot hit a single
# -48 g first sample (elapsed_ms=22, one reading before the scale had
# connected/tared) that dragged the whole smoothing/derivative computation
# with it, misclassifying an otherwise perfectly normal (if too-fast) shot as
# invalid_measurement. This must only ever reject on the negative side - a
# legitimately high leading *positive* reading (e.g. synthetic test data, or
# real samples that only start once a pour is already well underway) is not
# implausible the same way and must never be discarded.
_LEADING_GARBAGE_THRESHOLD_G = 2.0


class ShotClassification(str, Enum):
    """Stage-1 validation outcome for one shot (docs/DESIGN.md section 13)."""

    HEALTHY = "healthy"
    TOO_FAST = "too_fast"
    TOO_RESTRICTIVE = "too_restrictive"
    PUCK_PREP_ISSUE = "puck_prep_issue"
    INVALID = "invalid_measurement"

    def __str__(self) -> str:
        return self.value


class InvalidReason(str, Enum):
    """Why a shot came back invalid_measurement - logged and stored so an
    invalid shot can be diagnosed (e.g. a BLE dropout vs. a disturbed cup)
    instead of just showing up as an unexplained invalid_measurement."""

    TOO_FEW_SAMPLES = "too_few_samples"
    NON_POSITIVE_DURATION = "non_positive_duration"
    NEAR_ZERO_FINAL_WEIGHT = "near_zero_final_weight"
    NO_DETECTED_FLOW = "no_detected_flow"
    FLOW_STARTED_BEFORE_PREINFUSION_END = "flow_started_before_preinfusion_end"
    DISTURBANCE_LEFT_TOO_FEW_SAMPLES = "disturbance_left_too_few_samples"
    LEADING_GARBAGE_LEFT_TOO_FEW_SAMPLES = "leading_garbage_left_too_few_samples"

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True)
class BaselineFeatures:
    """Summary of a bag's recent healthy shots.

    median_flow_g_s feeds a symmetric Bayesian blend of the expected flow
    rate (see module docstring); median_late_accel feeds the asymmetric,
    escalation-only mechanical-suspicion check. They are updated differently
    on purpose - see _blended_expected_flow_g_s vs _baseline_deviation_suspicion.
    """

    shot_count: int
    median_late_accel: float
    median_flow_g_s: float


@dataclass(slots=True)
class ShotAnalysis:
    """Derived features and Stage-1 classification for one shot."""

    classification: ShotClassification
    channeling_suspicion: float | None
    baseline_eligible: bool
    invalid_reason: str | None
    t_first_flow_ms: int | None
    t10_ms: int | None
    t50_ms: int | None
    t90_ms: int | None
    early_flow_g_s: float | None
    mid_flow_g_s: float | None
    late_flow_g_s: float | None
    max_flow_g_s: float | None
    flow_slope: float | None
    flow_curvature: float | None
    flow_variance: float | None
    mid_accel: float | None
    late_accel: float | None


def _invalid(reason: InvalidReason) -> ShotAnalysis:
    return ShotAnalysis(
        classification=ShotClassification.INVALID,
        channeling_suspicion=None,
        baseline_eligible=False,
        invalid_reason=str(reason),
        t_first_flow_ms=None,
        t10_ms=None,
        t50_ms=None,
        t90_ms=None,
        early_flow_g_s=None,
        mid_flow_g_s=None,
        late_flow_g_s=None,
        max_flow_g_s=None,
        flow_slope=None,
        flow_curvature=None,
        flow_variance=None,
        mid_accel=None,
        late_accel=None,
    )


def _first_disturbance_index(raw_weights: list[float]) -> int | None:
    """First index where weight drops meaningfully below its own running
    peak - physically implausible during a real pour (weight only rises
    while coffee is being collected), so this reliably flags cup/scale
    interference rather than genuine flow, however and whenever it happens.

    Only armed once the running peak clears _DISTURBANCE_DETECTION_FLOOR_G -
    see that constant for why.
    """
    running_max = raw_weights[0]
    for i, weight in enumerate(raw_weights):
        if (
            running_max >= _DISTURBANCE_DETECTION_FLOOR_G
            and weight < running_max - _MAX_PLAUSIBLE_WEIGHT_DROP_G
        ):
            return i
        running_max = max(running_max, weight)
    return None


def _first_plausible_index(raw_weights: list[float]) -> int:
    """First index whose weight isn't implausibly negative (more than
    _LEADING_GARBAGE_THRESHOLD_G below zero) - i.e. how many leading samples
    to skip as pre-tare/pre-connect scale noise. Only ever rejects on the
    negative side: a legitimately high leading positive reading (e.g. real
    samples that only start once a pour is already underway) is left alone.

    Unlike _first_disturbance_index (a genuine mid-shot problem, judged
    relative to the shot's own running peak), this looks for implausible
    readings before any real peak has been established at all, so it can't
    use the same running-peak comparison - a garbage first sample would just
    become the (garbage) running peak itself.

    Returns len(raw_weights) if every sample is implausible.
    """
    for i, weight in enumerate(raw_weights):
        if weight >= -_LEADING_GARBAGE_THRESHOLD_G:
            return i
    return len(raw_weights)


def _moving_average(times_ms: list[int], values: list[float], window_ms: int) -> list[float]:
    """Centered moving average over a time window, O(n) via two pointers."""
    n = len(values)
    smoothed = [0.0] * n
    lo = 0
    hi = 0
    total = 0.0
    count = 0
    for i in range(n):
        window_lo = times_ms[i] - window_ms / 2
        window_hi = times_ms[i] + window_ms / 2
        while lo < n and times_ms[lo] < window_lo:
            total -= values[lo]
            count -= 1
            lo += 1
        while hi < n and times_ms[hi] <= window_hi:
            total += values[hi]
            count += 1
            hi += 1
        smoothed[i] = total / count if count else values[i]
    return smoothed


def _derivative(times_ms: list[int], values: list[float]) -> list[float]:
    """Backward-difference derivative in units/second; first sample copies the second."""
    n = len(values)
    if n < 2:
        return [0.0] * n
    flow = [0.0] * n
    for i in range(1, n):
        dt_s = (times_ms[i] - times_ms[i - 1]) / 1000.0
        flow[i] = (values[i] - values[i - 1]) / dt_s if dt_s > 0 else flow[i - 1]
    flow[0] = flow[1]
    return flow


def _first_crossing_ms(times_ms: list[int], values: list[float], threshold: float) -> int | None:
    for t, v in zip(times_ms, values):
        if v >= threshold:
            return t
    return None


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)
    denom = n * sum_xx - sum_x * sum_x
    return (n * sum_xy - sum_x * sum_y) / denom if denom else 0.0


def _mean_second_derivative(times_s: list[float], values: list[float]) -> float:
    """Average discrete second derivative, used as a simple flow-curvature proxy."""
    if len(values) < 3:
        return 0.0
    total = 0.0
    count = 0
    for i in range(1, len(values) - 1):
        dt1 = times_s[i] - times_s[i - 1]
        dt2 = times_s[i + 1] - times_s[i]
        if dt1 <= 0 or dt2 <= 0:
            continue
        d1 = (values[i] - values[i - 1]) / dt1
        d2 = (values[i + 1] - values[i]) / dt2
        total += (d2 - d1) / ((dt1 + dt2) / 2)
        count += 1
    return total / count if count else 0.0


def _blended_expected_flow_g_s(baseline: BaselineFeatures | None) -> float:
    """Bayesian shrinkage toward this bag's own observed flow rate.

    A bag's characteristic pace is a reference point, not a safety boundary -
    unlike mechanical suspicion below, there's nothing to protect against
    here, so the global prior is allowed to fully wash out as real shots
    accumulate rather than only ever being overridden, never replaced.
    """
    if baseline is None or baseline.shot_count <= 0:
        return _EXPECTED_FLOW_G_S
    return (
        _PRIOR_WEIGHT_SHOTS * _EXPECTED_FLOW_G_S + baseline.shot_count * baseline.median_flow_g_s
    ) / (_PRIOR_WEIGHT_SHOTS + baseline.shot_count)


def _absolute_mechanical_suspicion(mid_accel: float, late_accel: float) -> float:
    """Fixed-prior suspicion: only rising flow (mid or late) is concerning."""
    # Under roughly constant pump pressure, flow naturally staying flat or gently declining through a shot is normal and expected — resistance doesn't spontaneously drop on its own. Flow rising mid/late shot is a red flag (classic channeling signature: a gap opens in the puck, resistance drops, flow rate jumps). So a negative mid_accel/late_accel (flow slowing down, i.e. healthy) should contribute zero suspicion
    worst = max(mid_accel, late_accel, 0.0)
    return min(1.0, worst / _ABSOLUTE_ACCEL_LIMIT_G_S2) # 0 = flat/declining, 1.0 = at-or-past the limit


def _baseline_deviation_suspicion(late_accel: float, baseline: BaselineFeatures) -> float:
    """Only a rise above this bag's own normal late-shot flow is concerning -
    the same "rising flow only" rule _absolute_mechanical_suspicion uses.
    An unusually low/declining late_accel is not a channeling signal.

    TODO(revisit once real, verified shot data exists): this doesn't grow
    more bag-dependent as shot_count increases past MIN_BASELINE_SHOTS,
    unlike _blended_expected_flow_g_s. That's deliberate for now, not an
    oversight: median_late_accel is built only from shots THIS classifier
    already called "healthy" - self-labeled, not independently verified. If
    the fixed prior below is even slightly lenient, mildly-bad shots leak
    into that pool and pull the bag's own baseline toward tolerating exactly
    that badness, which then judges future shots - a closed loop with
    nothing to correct it (the docs/DESIGN.md section 12 contamination
    risk). Flow rate has no equivalent risk (a bag's pace is just a fact,
    not an evaluative judgment), which is why only it gets Bayesian
    shrinkage today. A safer path to more bag-dependence here, once we can
    check it against real outcomes: keep _ABSOLUTE_ACCEL_LIMIT_G_S2 as a
    permanent floor, but let growing shot_count increase how *sensitive*
    this deviation check is (smaller deviations start counting), rather
    than moving the floor itself.
    """
    reference = max(abs(baseline.median_late_accel), 0.1)
    rise_above_baseline = max(late_accel - baseline.median_late_accel, 0.0)
    return min(1.0, rise_above_baseline / (reference * 3.0))


def analyze_shot(
    samples: list[ShotSample],
    *,
    target_yield_g: float,
    preinfusion_s: float,
    baseline: BaselineFeatures | None,
) -> ShotAnalysis:
    """Classify one shot's flow curve (docs/DESIGN.md section 13, Stage 1)."""
    if len(samples) < MIN_SAMPLES:
        return _invalid(InvalidReason.TOO_FEW_SAMPLES)

    times_ms = [sample.elapsed_ms for sample in samples]
    raw_weights = [sample.weight_g for sample in samples]

    # Drop leading pre-tare/pre-connect scale noise (e.g. a stray -48g first
    # reading) before it can poison the smoothing/derivative computation
    # below - see _first_plausible_index and _LEADING_GARBAGE_THRESHOLD_G.
    leading_garbage = _first_plausible_index(raw_weights)
    if leading_garbage:
        samples = samples[leading_garbage:]
        times_ms = times_ms[leading_garbage:]
        raw_weights = raw_weights[leading_garbage:]
        if len(samples) < MIN_SAMPLES:
            return _invalid(InvalidReason.LEADING_GARBAGE_LEFT_TOO_FEW_SAMPLES)

    # Cup or scale disturbed (lifted, bumped, moved) at any point: weight can
    # only rise while coffee is actually being collected, so a meaningful
    # drop means nothing from that point on is trustworthy. Truncate to the
    # prefix before it and analyze only that - the user doesn't need to be
    # told when it's "safe" to touch the cup, because whatever happens after
    # a real disturbance is simply discarded rather than contaminating the
    # rest of the shot's stats.
    disturbance_index = _first_disturbance_index(raw_weights)
    disturbed = disturbance_index is not None
    if disturbed:
        samples = samples[:disturbance_index]
        times_ms = times_ms[:disturbance_index]
        raw_weights = raw_weights[:disturbance_index]

    if len(samples) < MIN_SAMPLES:
        reason = (
            InvalidReason.DISTURBANCE_LEFT_TOO_FEW_SAMPLES
            if disturbed
            else InvalidReason.TOO_FEW_SAMPLES
        )
        return _invalid(reason)

    if times_ms[-1] <= 0 or raw_weights[-1] < 1.0:
        return _invalid(InvalidReason.NON_POSITIVE_DURATION if times_ms[-1] <= 0 else InvalidReason.NEAR_ZERO_FINAL_WEIGHT)

    smoothed = _moving_average(times_ms, raw_weights, SMOOTHING_WINDOW_MS)
    flow = _derivative(times_ms, smoothed)
    times_s = [t / 1000.0 for t in times_ms]

    t_first_flow_ms = _first_crossing_ms(times_ms, flow, FIRST_FLOW_THRESHOLD_G_S)
    # Was time to first flow plausible? (docs/DESIGN.md section 13). Either the
    # scale never registered real flow despite a meaningful final weight, or
    # flow started well before the configured pre-infusion soak should have
    # ended - both mean this trace isn't trustworthy enough to classify further.
    if t_first_flow_ms is None:
        return _invalid(InvalidReason.NO_DETECTED_FLOW)
    if t_first_flow_ms < preinfusion_s * 1000 * _EARLY_FLOW_FRACTION_OF_PREINFUSION:
        return _invalid(InvalidReason.FLOW_STARTED_BEFORE_PREINFUSION_END)

    t10_ms = _first_crossing_ms(times_ms, smoothed, 0.10 * target_yield_g)
    t50_ms = _first_crossing_ms(times_ms, smoothed, 0.50 * target_yield_g)
    t90_ms = _first_crossing_ms(times_ms, smoothed, 0.90 * target_yield_g)  # time to 90% of yield

    early_flow: list[float] = []
    mid_flow: list[float] = []
    late_flow: list[float] = []
    mid_times: list[float] = []
    mid_values: list[float] = []
    late_times: list[float] = []
    late_values: list[float] = []
    for t_s, weight, f in zip(times_s, smoothed, flow):
        progress = min(1.0, max(0.0, weight / target_yield_g))
        if progress < 1 / 3:
            early_flow.append(f)
        elif progress < 2 / 3:
            mid_flow.append(f)
            mid_times.append(t_s)
            mid_values.append(f)
        else:
            late_flow.append(f)
            late_times.append(t_s)
            late_values.append(f)

    mid_accel = _linear_slope(mid_times, mid_values)
    late_accel = _linear_slope(late_times, late_values)

    duration_s = (t90_ms if t90_ms is not None else times_ms[-1]) / 1000.0
    expected_s = target_yield_g / _blended_expected_flow_g_s(baseline)

    absolute_score = _absolute_mechanical_suspicion(mid_accel, late_accel)
    baseline_score = (
        _baseline_deviation_suspicion(late_accel, baseline)
        if baseline is not None and baseline.shot_count >= MIN_BASELINE_SHOTS
        else 0.0
    )
    channeling_suspicion = max(absolute_score, baseline_score)

    # Mechanical validity (docs/DESIGN.md section 14: "was this mechanically a
    # valid shot?") is judged before the fast/slow hydraulic correction, not
    # after - a shot that both finishes fast and shows a channeling signature
    # is a puck-prep problem to fix before grind is even worth adjusting.
    if t90_ms is None:
        classification = ShotClassification.TOO_RESTRICTIVE
    elif channeling_suspicion >= SUSPICION_THRESHOLD:
        classification = ShotClassification.PUCK_PREP_ISSUE
    elif duration_s < expected_s * _TOO_FAST_FACTOR:
        classification = ShotClassification.TOO_FAST
    elif duration_s > expected_s * _TOO_RESTRICTIVE_FACTOR:
        classification = ShotClassification.TOO_RESTRICTIVE
    else:
        classification = ShotClassification.HEALTHY

    return ShotAnalysis(
        classification=classification,
        channeling_suspicion=channeling_suspicion,
        baseline_eligible=classification == ShotClassification.HEALTHY,
        invalid_reason=None,
        t_first_flow_ms=t_first_flow_ms,
        t10_ms=t10_ms,
        t50_ms=t50_ms,
        t90_ms=t90_ms,
        early_flow_g_s=statistics.median(early_flow) if early_flow else None,
        mid_flow_g_s=statistics.median(mid_flow) if mid_flow else None,
        late_flow_g_s=statistics.median(late_flow) if late_flow else None,
        max_flow_g_s=max(flow) if flow else None,
        flow_slope=_linear_slope(times_s, flow),
        flow_curvature=_mean_second_derivative(times_s, flow),
        flow_variance=statistics.pvariance(flow) if len(flow) > 1 else 0.0,
        mid_accel=mid_accel,
        late_accel=late_accel,
    )
