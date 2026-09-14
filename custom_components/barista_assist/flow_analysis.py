"""Stage-1 shot flow-curve analysis: smoothing, timing/flow features, and
classification.

Pure computation, no Home Assistant or database dependency, so it can be
unit-tested against synthetic mass curves. See docs/DESIGN.md sections 8-13
for the feature set and diagnostic-architecture rationale this implements.

Every tunable threshold this module uses lives in one place: FlowAnalysisConfig
below - a dataclass with no default values, on purpose. Consts live in
definitions.yaml, code is for logic, so this module holds no fixed priors of
its own at all; every real caller (runtime.py) builds a FlowAnalysisConfig
from definitions.yaml's flow_analysis_constants. See that key's own comment
in definitions.yaml for the sourcing/derivation behind each value - several
are themselves still simple placeholders there, not values derived from this
project's own shot data yet, and replacing them with data-driven thresholds
is tracked as its own follow-up phase in docs/DESIGN.md - but that's a
property of the YAML values, not of this module.

This module blends two different kinds of "prior," against two differently
scoped baselines, and deliberately treats them differently:

- The expected flow rate (what pace counts as "too fast"/"too restrictive")
  is a reference point, not a safety boundary: there's no problem in a
  roast level genuinely pouring faster or slower than the generic global
  guess, so it's fine - correct, even - for the model to shift fully toward
  this installation's own observed pace for that roast level as shots
  accumulate. `expected_s` below is a Bayesian shrinkage estimate: a
  weighted blend of the fixed prior and the median flow rate across *other*
  bags' shots sharing the same `roast_level`, sliding smoothly toward that
  pool's data as its shot count grows, with no hard cutover point. This is
  deliberately keyed on roast level, not on the current bag's own history -
  see `docs/DESIGN.md`'s Phase 3b: bean-aging drift within one
  bag's life is handled by grind correction chasing a fixed reference
  instead, so a bag's own shots never feed back into its own reference
  point. `roast_level_ratio_prior` (docs/DESIGN.md §19) uses the same
  mechanism.

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
  docs/DESIGN.md section 12 warns against. This one stays scoped to the
  current bag's own shot history (unlike flow-rate above) precisely because
  self-normalization is the failure mode being guarded against here. A
  safer, independently-verified form of bag-dependence here (e.g. gated on
  a later-confirmed `balanced` taste report, not just a self-labeled
  `healthy` classification) remains an open question, not yet built.

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


# =============================================================================
# Configuration and data contracts
# =============================================================================


@dataclass(frozen=True, slots=True)
class FlowAnalysisConfig:
    """Every tunable threshold flow_analysis.py uses, in one place - and, on
    purpose, with no default values: consts live in definitions.yaml, code
    is for logic. See definitions.yaml's flow_analysis_constants for what
    each field means and where its value came from; every real caller
    (runtime.py, tests) builds this from FlowAnalysisConfig(**load_definitions()
    .flow_analysis_constants) rather than constructing one from memory.
    """

    min_samples: int
    min_baseline_shots: int
    first_flow_threshold_g_s: float
    smoothing_window_ms: int
    suspicion_threshold: float
    first_flow_sustain_ms: int
    expected_flow_g_s: float  # Extraction-phase-only rate (pre-infusion excluded)
    too_fast_factor: float
    too_restrictive_factor: float
    prior_weight_shots: float
    absolute_accel_limit_g_s2: float
    early_flow_fraction_of_preinfusion: float
    max_plausible_weight_drop_g: float
    disturbance_detection_floor_g: float
    disturbance_sustain_ms: int
    leading_garbage_threshold_g: float
    stale_scale_clock_threshold_ms: int
    baseline_deviation_floor_g_s2: float
    baseline_deviation_scale: float
    near_zero_final_weight_threshold_g: float


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
    """Summary of a bag's own recent healthy shots, for channeling-suspicion
    scoring only (_baseline_deviation_suspicion) - see module docstring for
    why this stays a per-bag baseline while the flow-rate reference
    (RoastLevelFlowBaseline below) does not.
    """

    shot_count: int
    median_late_accel: float


@dataclass(slots=True)
class RoastLevelFlowBaseline:
    """Summary of other bags' shots sharing the current bag's roast_level,
    used only by blended_expected_flow_g_s (see module docstring). Deliberately
    not scoped to the current bag - see docs/DESIGN.md's Phase 3b for why.
    """

    shot_count: int
    median_flow_g_s: float  # Extraction-phase-only rate (pre-infusion excluded)


@dataclass(slots=True)
class ShotAnalysis:
    """Derived features and Stage-1 classification for one shot."""

    classification: ShotClassification
    channeling_suspicion: float | None
    baseline_eligible: bool
    invalid_reason: str | None
    # actual shot duration / expected duration for this bag (expected_s =
    # preinfusion_s + target_yield_g / expected_flow_g_s - expected_flow_g_s
    # is post-pre-infusion/extraction-only, itself Bayesian-shrunk toward
    # this bag's roast_level - other bags sharing it, not this bag's own
    # history, see blended_expected_flow_g_s). Below 1.0 = ran
    # fast, above 1.0 = ran slow/restrictive. None when a shot couldn't be
    # classified at all (t90 never reached and no samples to fall back on,
    # or too few samples). This is what expert_rules.grind_correction's
    # bands (definitions.yaml) key off of - see grind_correction.py.
    # expected_s scaling with target_yield_g is deliberate - see the
    # computation site below (analyze_shot) for why.
    duration_ratio: float | None
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


# =============================================================================
# The sections below are ordered to match analyze_shot's own execution
# sequence at the bottom of this file: leading-garbage trim, then mid-shot
# disturbance trim, then smoothing/derivative, then timing crossings, then
# trend/shape features, then expected-flow blending and suspicion scoring.
# =============================================================================


# --- Leading-garbage detection: dropped before any real analysis begins ----


def _first_plausible_index(raw_weights: list[float], config: FlowAnalysisConfig) -> int:
    """First index whose weight isn't implausibly negative (more than
    config.leading_garbage_threshold_g below zero) - i.e. how many leading
    samples to skip as pre-tare/pre-connect scale noise. Only ever rejects
    on the negative side: a legitimately high leading positive reading
    (e.g. real samples that only start once a pour is already underway) is
    left alone.

    Unlike _first_disturbance_index (a genuine mid-shot problem, judged
    relative to the shot's own running peak), this looks for implausible
    readings before any real peak has been established at all, so it can't
    use the same running-peak comparison - a garbage first sample would just
    become the (garbage) running peak itself.

    Returns len(raw_weights) if every sample is implausible.
    """
    for i, weight in enumerate(raw_weights):
        if weight >= -config.leading_garbage_threshold_g:
            return i
    return len(raw_weights)


def _first_synced_clock_index(
    times_ms: list[int], scale_ms_values: list[int], config: FlowAnalysisConfig
) -> int:
    """First index whose scale_ms is plausibly in sync with our own
    elapsed_ms - i.e. how many leading samples to skip as stale BLE
    notifications left over from before the scale's clock was reset. See
    config.stale_scale_clock_threshold_ms.

    Returns len(times_ms) if every sample's clock is out of sync.
    """
    for i, (t, scale_ms) in enumerate(zip(times_ms, scale_ms_values)):
        if scale_ms - t <= config.stale_scale_clock_threshold_ms:
            return i
    return len(times_ms)


# --- Mid-shot disturbance detection: cup/scale bumped, lifted, or moved ----


def _first_disturbance_index(
    times_ms: list[int], raw_weights: list[float], config: FlowAnalysisConfig
) -> int | None:
    """First index where weight drops meaningfully below its own running
    peak AND stays there for at least config.disturbance_sustain_ms -
    physically implausible during a real pour (weight only rises while
    coffee is being collected), so a drop that never recovers reliably
    flags cup/scale interference. A violent, splashy gush can bounce a
    sample or two below the running peak (droplets, crema settling, the cup
    rocking) without any real interference - that recovers within a couple
    hundred ms and keeps climbing, unlike a genuine disturbance, so only a
    drop that holds for the full sustain window (or runs out the rest of
    the shot without recovering) counts.

    Only armed once the running peak clears
    config.disturbance_detection_floor_g - see that field for why.
    """
    n = len(raw_weights)
    running_max = raw_weights[0]
    i = 0
    while i < n:
        weight = raw_weights[i]
        if (
            running_max >= config.disturbance_detection_floor_g
            and weight < running_max - config.max_plausible_weight_drop_g
        ):
            drop_level = running_max - config.max_plausible_weight_drop_g
            start_t = times_ms[i]
            j = i
            while j < n and raw_weights[j] < drop_level:
                j += 1
            if j == n or times_ms[j - 1] - start_t >= config.disturbance_sustain_ms:
                return i
            running_max = max(running_max, max(raw_weights[i:j]))
            i = j
            continue
        running_max = max(running_max, weight)
        i += 1
    return None


# --- Smoothing and flow derivation ------------------------------------------


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


# --- Timing / crossing helpers -----------------------------------------------


def _first_crossing_ms(
    times_ms: list[int], values: list[float], threshold: float, sustain_ms: int = 0
) -> int | None:
    """First time values crosses threshold. With the default sustain_ms=0,
    any single sample at or above threshold counts (used for t10/t50/t90
    against the already-smoothed weight curve, where a momentary dip back
    below threshold isn't a real concern). Pass sustain_ms to additionally
    require the crossing to hold for that long before counting - filters
    out a single noisy spike from being mistaken for the true start of flow
    (used for first-flow detection against the noisier raw derivative). A
    run still touching the last sample counts even if it hasn't reached
    sustain_ms yet, since there's no later data to prove it wouldn't have
    held."""
    n = len(times_ms)
    i = 0
    while i < n:
        if values[i] < threshold:
            i += 1
            continue
        start_t = times_ms[i]
        j = i
        while j < n and values[j] >= threshold:
            j += 1
        if times_ms[j - 1] - start_t >= sustain_ms or j == n:
            return start_t
        i = j
    return None


# --- Trend/shape helpers: acceleration and curvature ------------------------


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


# --- Expected-flow-rate blending and channeling-suspicion scoring ----------


def blend_toward_observed(prior: float, observed: float, shot_count: int, weight: float) -> float:
    """Bayesian shrinkage of a fixed prior toward an observed value, weighted
    by how much data backs the observed value - shared shrinkage-weight
    formula for blended_expected_flow_g_s below and, per
    docs/DESIGN.md §19, runtime.py's
    roast-level-seeded ratio/dose/temperature priors. `weight` is
    flow_analysis_constants.prior_weight_shots in every current caller - one
    shrinkage-weight constant shared across all of them, not a separate one
    per prior.
    """
    if shot_count <= 0:
        return prior
    return (weight * prior + shot_count * observed) / (weight + shot_count)


def blended_expected_flow_g_s(
    baseline: RoastLevelFlowBaseline | None, config: FlowAnalysisConfig
) -> float:
    """Bayesian shrinkage toward *other* bags' observed flow rate, pooled by
    roast_level - see module docstring for why this is roast-level-scoped,
    not scoped to the current bag.

    Public because runtime.py's async_brew calls this directly, once per
    shot at brew time, to seed ActiveShot.expected_flow_g_s - the
    idealized-curve overlay the Live Shot/Shot History charts draw needs
    this rate available immediately, not only after the shot finishes.
    analyze_shot itself does NOT call this - it takes the already-computed
    rate as its own expected_flow_g_s param instead, so classification at
    finalize time can never diverge from what async_brew already computed
    and the user already saw (see analyze_shot's own docstring for why
    re-deriving it here would be a real, not just redundant, bug).

    A roast level's characteristic pace is a reference point, not a safety
    boundary - unlike mechanical suspicion below, there's nothing to protect
    against here, so the global prior is allowed to fully wash out as real
    shots accumulate rather than only ever being overridden, never replaced.

    Both operands are post-pre-infusion/extraction-only rates -
    config.expected_flow_g_s by definition (see its own field comment) and
    baseline.median_flow_g_s because storage.roast_level_baseline subtracts
    each pooled shot's own preinfusion_s before dividing. Blending a
    pre-infusion-diluted observed rate against this pre-infusion-free prior
    would drag the result down as real shots accumulate, silently
    reintroducing the double-counted-pre-infusion bias expected_s's own
    `preinfusion_s +` term exists to avoid.
    """
    if baseline is None:
        return config.expected_flow_g_s
    return blend_toward_observed(
        config.expected_flow_g_s, baseline.median_flow_g_s, baseline.shot_count, config.prior_weight_shots
    )


def _absolute_mechanical_suspicion(mid_accel: float, late_accel: float, config: FlowAnalysisConfig) -> float:
    """Fixed-prior suspicion: only rising flow (mid or late) is concerning."""
    # Under roughly constant pump pressure, flow naturally staying flat or gently declining through a shot is normal and expected — resistance doesn't spontaneously drop on its own. Flow rising mid/late shot is a red flag (classic channeling signature: a gap opens in the puck, resistance drops, flow rate jumps). So a negative mid_accel/late_accel (flow slowing down, i.e. healthy) should contribute zero suspicion
    worst = max(mid_accel, late_accel, 0.0)
    return min(1.0, worst / config.absolute_accel_limit_g_s2)  # 0 = flat/declining, 1.0 = at-or-past the limit


def _baseline_deviation_suspicion(
    late_accel: float, baseline: BaselineFeatures, config: FlowAnalysisConfig
) -> float:
    """Only a rise above this bag's own normal late-shot flow is concerning -
    the same "rising flow only" rule _absolute_mechanical_suspicion uses.
    An unusually low/declining late_accel is not a channeling signal.

    Deliberately doesn't grow more bag-dependent as shot_count
    increases past config.min_baseline_shots, unlike blended_expected_flow_g_s
    - see `docs/DESIGN.md`'s Phase 3b for why (a contamination/closed-loop
    risk on this specific baseline). A safer path remains an open question,
    not yet built: widen sensitivity as shot_count grows (smaller deviations
    start counting) rather than moving the floor itself.
    """
    reference = max(abs(baseline.median_late_accel), config.baseline_deviation_floor_g_s2)
    rise_above_baseline = max(late_accel - baseline.median_late_accel, 0.0)
    return min(1.0, rise_above_baseline / (reference * config.baseline_deviation_scale))


# --- Early-exit construction, used throughout analyze_shot below -----------


def _invalid(reason: InvalidReason) -> ShotAnalysis:
    return ShotAnalysis(
        classification=ShotClassification.INVALID,
        channeling_suspicion=None,
        baseline_eligible=False,
        invalid_reason=str(reason),
        duration_ratio=None,
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


# =============================================================================
# Public entry point
# =============================================================================


def analyze_shot(
    samples: list[ShotSample],
    *,
    target_yield_g: float,
    preinfusion_s: float,
    baseline: BaselineFeatures | None,
    expected_flow_g_s: float,
    config: FlowAnalysisConfig,
) -> ShotAnalysis:
    """Classify one shot's flow curve (docs/DESIGN.md section 13, Stage 1).

    config is mandatory, on purpose: there is no built-in fallback, so every
    caller must build one from definitions.yaml's flow_analysis_constants
    (FlowAnalysisConfig(**load_definitions().flow_analysis_constants)) - see
    that key's own comment in definitions.yaml for the full derivation of
    each value, and FlowAnalysisConfig's own docstring for why this module
    holds no default numbers at all. baseline feeds channeling-suspicion
    scoring only. expected_flow_g_s feeds expected_s's flow-rate reference
    only (see module docstring for why these are two differently-scoped
    baselines, not one) - it's a post-pre-infusion/extraction-only rate
    (expected_s adds preinfusion_s on top, since duration_s below is
    measured from the brew press and already includes it), the caller's own
    already-computed
    blended_expected_flow_g_s(roast_level_baseline, config) result, not a
    baseline to blend here: runtime.py's async_brew computes and persists
    this once, at brew time, and _async_finalize must reuse that exact same
    value rather than re-fetching/re-blending the roast-level pool again at
    finalize time - the pool can genuinely change in between (a different
    bag's shot finishing), and classification must match what the Live
    Shot/Shot History charts' idealized-curve overlay already showed the
    user, not silently diverge from it.
    """
    if len(samples) < config.min_samples:
        return _invalid(InvalidReason.TOO_FEW_SAMPLES)

    times_ms = [sample.elapsed_ms for sample in samples]
    raw_weights = [sample.weight_g for sample in samples]

    # Drop leading pre-tare/pre-connect scale noise (e.g. a stray -48g first
    # reading, or a stale reading whose scale_ms shows it's left over from
    # before the scale's clock was reset) before it can poison the
    # smoothing/derivative computation below - see _first_plausible_index,
    # _first_synced_clock_index, and their respective config fields.
    leading_garbage = max(
        _first_plausible_index(raw_weights, config),
        _first_synced_clock_index(times_ms, [sample.scale_ms for sample in samples], config),
    )
    if leading_garbage:
        samples = samples[leading_garbage:]
        times_ms = times_ms[leading_garbage:]
        raw_weights = raw_weights[leading_garbage:]
        if len(samples) < config.min_samples:
            return _invalid(InvalidReason.LEADING_GARBAGE_LEFT_TOO_FEW_SAMPLES)

    # Cup or scale disturbed (lifted, bumped, moved) at any point: weight can
    # only rise while coffee is actually being collected, so a meaningful
    # drop means nothing from that point on is trustworthy. Truncate to the
    # prefix before it and analyze only that - the user doesn't need to be
    # told when it's "safe" to touch the cup, because whatever happens after
    # a real disturbance is simply discarded rather than contaminating the
    # rest of the shot's stats.
    disturbance_index = _first_disturbance_index(times_ms, raw_weights, config)
    disturbed = disturbance_index is not None
    if disturbed:
        samples = samples[:disturbance_index]
        times_ms = times_ms[:disturbance_index]
        raw_weights = raw_weights[:disturbance_index]

    if len(samples) < config.min_samples:
        reason = (
            InvalidReason.DISTURBANCE_LEFT_TOO_FEW_SAMPLES
            if disturbed
            else InvalidReason.TOO_FEW_SAMPLES
        )
        return _invalid(reason)

    if times_ms[-1] <= 0 or raw_weights[-1] < config.near_zero_final_weight_threshold_g:
        return _invalid(InvalidReason.NON_POSITIVE_DURATION if times_ms[-1] <= 0 else InvalidReason.NEAR_ZERO_FINAL_WEIGHT)

    smoothed = _moving_average(times_ms, raw_weights, config.smoothing_window_ms)
    flow = _derivative(times_ms, smoothed)
    times_s = [t / 1000.0 for t in times_ms]

    t_first_flow_ms = _first_crossing_ms(
        times_ms, flow, config.first_flow_threshold_g_s, sustain_ms=config.first_flow_sustain_ms
    )
    # Was time to first flow plausible? (docs/DESIGN.md section 13). Either the
    # scale never registered real flow despite a meaningful final weight, or
    # flow started well before the configured pre-infusion soak should have
    # ended - both mean this trace isn't trustworthy enough to classify further.
    if t_first_flow_ms is None:
        return _invalid(InvalidReason.NO_DETECTED_FLOW)
    if t_first_flow_ms < preinfusion_s * 1000 * config.early_flow_fraction_of_preinfusion:
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
    # expected_s's target_yield_g/expected_flow_g_s term scales with
    # target_yield_g on purpose - this is a statement about the bag's
    # characteristic flow RATE, not a fixed personal time preference. "How I
    # Dial-In Espresso" Episode 1 holds grind (hence rate) constant while
    # deliberately pushing yield up (38g->42g, "keep the grind where it is
    # and just push a little bit more liquid through it") and never
    # compensates to keep the shot's absolute time fixed - a mechanically
    # unchanged shot (same rate) that's deliberately pulled to a larger
    # yield should take longer and still read as healthy. If expected_s
    # didn't scale with target_yield_g, that same shot would be wrongly
    # flagged too_restrictive purely for running longer, with nothing
    # actually wrong. preinfusion_s is added on top rather than folded into
    # the rate term: duration_s (t90_ms) is measured from press_monotonic,
    # i.e. it already includes the full pre-infusion hold, so expected_s
    # must budget that same dead time or every shot reads as slower than it
    # actually poured - matching the idealized-curve overlay the dashboard
    # already draws (flat through pre-infusion, then ramping to
    # target_yield_g over expected_s). Open caveat, not yet addressed: flow
    # isn't necessarily constant across the pour itself (a real ramp-up
    # period between first-flow and a roughly-steady rate), and this
    # formula doesn't budget that ramp-up as separate dead time on top of
    # preinfusion_s - needs real data to fit the ramp-up shape before
    # changing it.
    expected_s = (
        preinfusion_s + target_yield_g / expected_flow_g_s if expected_flow_g_s > 0 else 0.0
    )
    duration_ratio = duration_s / expected_s if expected_s > 0 else None

    absolute_score = _absolute_mechanical_suspicion(mid_accel, late_accel, config)
    baseline_score = (
        _baseline_deviation_suspicion(late_accel, baseline, config)
        if baseline is not None and baseline.shot_count >= config.min_baseline_shots
        else 0.0
    )
    channeling_suspicion = max(absolute_score, baseline_score)

    # Mechanical validity (docs/DESIGN.md section 14: "was this mechanically a
    # valid shot?") is judged before the fast/slow hydraulic correction, not
    # after - a shot that both finishes fast and shows a channeling signature
    # is a puck-prep problem to fix before grind is even worth adjusting.
    #
    # This module's own classification treats every PUCK_PREP_ISSUE shot
    # identically, regardless of how many consecutive shots at this same
    # recipe landed here - runtime.py is where a repeated streak overrides
    # the resulting grind recommendation (docs/DESIGN.md §12/Phase 4), not
    # here; the classification itself never changes based on streak length.
    if t90_ms is None:
        classification = ShotClassification.TOO_RESTRICTIVE
    elif channeling_suspicion >= config.suspicion_threshold:
        classification = ShotClassification.PUCK_PREP_ISSUE
    elif duration_s < expected_s * config.too_fast_factor:
        classification = ShotClassification.TOO_FAST
    elif duration_s > expected_s * config.too_restrictive_factor:
        classification = ShotClassification.TOO_RESTRICTIVE
    else:
        classification = ShotClassification.HEALTHY

    return ShotAnalysis(
        classification=classification,
        channeling_suspicion=channeling_suspicion,
        baseline_eligible=classification == ShotClassification.HEALTHY,
        invalid_reason=None,
        duration_ratio=duration_ratio,
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
