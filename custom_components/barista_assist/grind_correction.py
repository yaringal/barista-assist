"""Phase 4 expert-system grind correction (docs/DESIGN.md section 28).

Pure function consuming expert_rules.grind_correction from definitions.yaml
- see that key's own comment there for the full sourcing/derivation of its
band boundaries and grind_delta magnitudes. Kept separate from
flow_analysis.py on purpose, matching definitions.yaml's own stated split:
the diagnostic math that produces a shot's classification/duration_ratio
stays in Python as pure algorithm; the policy consumed here (which
classifications are eligible, how big a correction each duration_ratio band
gets) is data, not hardcoded.
"""

from __future__ import annotations

from typing import Any

from .flow_analysis import ShotClassification


def recommend_grind_delta(
    classification: ShotClassification | str,
    duration_ratio: float | None,
    config: dict[str, Any],
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

    Otherwise walks config["bands"] (ordered ascending by
    duration_ratio_max) and returns the first band's grind_delta whose max
    this shot's duration_ratio is at or under - the last band (typically
    grossly_restrictive) has no max and so always matches if reached.
    """
    classification_value = str(classification)
    applies_to = config.get("applies_to_classification", ())
    excludes = config.get("excludes_classification", ())
    if classification_value not in applies_to or classification_value in excludes:
        return None
    if duration_ratio is None:
        return None
    for band in config.get("bands", ()):
        maximum = band.get("duration_ratio_max")
        if maximum is None or duration_ratio <= maximum:
            return float(band["grind_delta"])
    return None
