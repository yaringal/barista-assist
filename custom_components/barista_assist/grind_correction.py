"""Phase 4 expert-system grind correction (docs/DESIGN.md section 28).

Pure function consuming expert_rules.grind_correction's grind_deltas from
definitions.yaml, plus flow_analysis_constants.duration_ratio_bands (the
band boundaries themselves - shared with flow_analysis.py's own Stage 1
classification, not redefined here) - see those keys' own comments for the
full sourcing/derivation. Kept separate from flow_analysis.py on purpose,
matching definitions.yaml's own stated split: the diagnostic math that
produces a shot's classification/duration_ratio stays in Python as pure
algorithm; the policy consumed here (which classifications are eligible,
how big a correction each duration_ratio band gets) is data, not
hardcoded.
"""

from __future__ import annotations

from typing import Any

from .flow_analysis import ShotClassification


def _is_grind_correction_candidate(classification_value: str, config: dict[str, Any]) -> bool:
    applies_to = config.get("applies_to_classification", ())
    excludes = config.get("excludes_classification", ())
    return classification_value in applies_to and classification_value not in excludes


def _matched_band_index(duration_ratio: float, duration_ratio_bands: list[dict[str, Any]]) -> int | None:
    for index, band in enumerate(duration_ratio_bands):
        maximum = band.get("duration_ratio_max")
        if maximum is None or duration_ratio <= maximum:
            return index
    return None


def recommend_grind_delta(
    classification: ShotClassification | str,
    duration_ratio: float | None,
    config: dict[str, Any],
    duration_ratio_bands: list[dict[str, Any]],
    *,
    current_recipe: dict[str, Any] | None = None,
    previous_shot: dict[str, Any] | None = None,
) -> float | None:
    """Return the recommended DF54 grind delta for one shot, or None if this
    shot isn't a candidate for a grind correction at all.

    A shot is not a candidate when its classification isn't in
    config["applies_to_classification"] or is explicitly listed in
    config["excludes_classification"] (docs/DESIGN.md section 13's Stage 1
    -> Stage 2 ordering: a puck_prep_issue or invalid_measurement shot gets
    "repeat the recipe", never a grind change - see grind_correction's own
    comment in definitions.yaml for why puck-prep and grind-too-fine
    channeling can't yet be told apart), or when duration_ratio itself
    couldn't be computed (shot too broken to classify at all).

    duration_ratio_bands (ordered ascending by duration_ratio_max) is the
    same shared, stage-agnostic severity ladder flow_analysis.py's own
    analyze_shot derives its too_fast/too_restrictive thresholds from (see
    that key's own comment in definitions.yaml) - not redefined here, so
    this can never disagree with Stage 1 about where "healthy" starts and
    ends. Walks it and returns the first band's own name's grind_delta
    (config["grind_deltas"][name]) whose max this shot's duration_ratio is
    at or under - the last band (typically grossly_restrictive) has no max
    and so always matches if reached.

    current_recipe/previous_shot (both optional - omitted only by tests
    exercising the base band lookup in isolation; runtime.py's
    _async_finalize always passes both) enable overshoot damping
    (docs/DESIGN.md's Phase 4): current_recipe is this shot's
    own dose_g/target_yield_g/temperature_offset_c/preinfusion_s;
    previous_shot is the matching row from
    storage.previous_grind_correction_shot for the same bag (same fields,
    plus classification/recommended_grind_delta). When both are given and
    show the previous recommended correction on this same recipe overshot
    past healthy into the opposite classification, the returned delta is
    damped one band-tier back toward healthy instead of the full magnitude
    - derived fresh from the last shot's own stored row each time, nothing
    new persisted (matching runtime.py's _all_flavor_field_recommendations
    convention).
    """
    classification_value = str(classification)
    if not _is_grind_correction_candidate(classification_value, config):
        return None
    if duration_ratio is None:
        return None
    bands = list(duration_ratio_bands)
    grind_deltas = config.get("grind_deltas", {})
    matched_index = _matched_band_index(duration_ratio, bands)
    if matched_index is None:
        return None
    matched_delta = float(grind_deltas[bands[matched_index]["name"]])

    if current_recipe is None or previous_shot is None:
        return matched_delta

    hold_constant = config.get("hold_constant", ())
    if any(previous_shot.get(field) != current_recipe.get(field) for field in hold_constant):
        return matched_delta

    previous_delta = previous_shot.get("recommended_grind_delta")
    if previous_delta is None or not _is_grind_correction_candidate(
        str(previous_shot.get("classification")), config
    ):
        return matched_delta

    overshot = (previous_delta < 0 < matched_delta) or (matched_delta < 0 < previous_delta)
    if not overshot:
        return matched_delta

    healthy_index = next(index for index, band in enumerate(bands) if band["name"] == "healthy")
    step = 1 if matched_index < healthy_index else -1
    damped_index = matched_index + step
    if damped_index == healthy_index:
        return matched_delta
    return float(grind_deltas[bands[damped_index]["name"]])
