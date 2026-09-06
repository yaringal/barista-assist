"""Phase 5 expert-system flavor correction (docs/DESIGN.md section 13).

Pure function consuming expert_rules.flavor_correction from definitions.yaml
- see that key's own comment there for the full sourcing/derivation of each
tag's lever/direction/delta_g. Kept separate from runtime.py on purpose,
matching grind_correction.py's own split: the policy encoded here (which
tag maps to which lever, how big a correction it implies) is data, not
hardcoded.

Unlike grind_correction, the signal this consumes (a taste tag) is
self-reported rather than measured, and a single shot's report is treated as
noise, not evidence - config["require_persistent_pattern_shots"] gates every
recommendation on the same tag showing up across that many of the bag's most
recent *answered* shots in a row (see storage.recent_flavor_tags for what
"answered" means). expert_rules.flavor_correction.tags.*.escalation
(stepping up to a different lever once a prior correction's own lever has
already been tried and the tag still persists) is intentionally not
implemented yet.

TODO(revisit once real, verified shot data exists): escalation requires
knowing whether a previous recommendation on this tag was actually acted on
- detectable in principle by checking whether target_yield_g (or whichever
lever was recommended) actually moved in the recommended direction between
the shot that carried the recommendation and now, using only data already in
the shots table - but that is a second layer of judgment worth building once
the base (non-escalated) recommendation below has real usage behind it.
"""

from __future__ import annotations

from typing import Any

_LEVER_TO_FIELD = {
    "yield": "target_yield_g",
    "dose": "dose_g",
    "temperature": "temperature_offset_c",
    "preinfusion": "preinfusion_s",
}


def recommend_flavor_correction(
    tag: str | None,
    recent_tags: list[str],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a recommendation for one flavor-correction axis, or None.

    tag is the most recent answered tag for this axis+bag (or None if never
    answered). recent_tags is that same axis+bag's answered tags,
    most-recent-first (see storage.recent_flavor_tags) - used only to check
    persistence, not to pick which tag to act on.

    None is returned for tag in (None, "balanced"), for a tag not present in
    config["tags"], and whenever fewer than
    config["require_persistent_pattern_shots"] of the most recent answered
    shots all reported this same tag (a single report, or a broken streak,
    isn't persistence).

    Otherwise returns {"lever", "field", "direction", "delta"}: delta_g in
    config["tags"][tag] is used directly when present, or - when absent
    entirely - taken from config["minimum_meaningful_step"][field], exactly
    as that tag's own comment in definitions.yaml describes. Every tag's
    delta_g is a single number, decided once in definitions.yaml (data) even
    where the underlying sourcing gave a range rather than one figure - see
    e.g. sour_sharp's own comment there - rather than this function picking
    a policy (such as a midpoint) to collapse a stored range at read time.
    """
    if tag is None or tag == "balanced":
        return None
    tags = config.get("tags", {})
    tag_config = tags.get(tag)
    if tag_config is None:
        return None
    # "Persistent" means the required count *consecutively*, not merely that
    # the tag has occurred before - the same tag showing up twice with a
    # different tag (including "balanced") in between resets the streak.
    # recent_tags only ever contains answered shots (see
    # storage.recent_flavor_tags), so "consecutive" is relative to answered
    # shots for this axis, not literal back-to-back shot numbers - a shot
    # nobody responded to is invisible here, so it can neither break a
    # streak in progress nor count toward one.
    required = int(config.get("require_persistent_pattern_shots", 1))
    if len(recent_tags) < required or any(t != tag for t in recent_tags[:required]):
        return None

    lever = tag_config["lever"]
    field = _LEVER_TO_FIELD[lever]
    delta_g = tag_config.get("delta_g")
    delta = float(delta_g) if delta_g is not None else float(config["minimum_meaningful_step"][field])
    return {
        "lever": lever,
        "field": field,
        "direction": tag_config["direction"],
        "delta": delta,
    }
