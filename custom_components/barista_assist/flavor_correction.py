"""Phase 5 expert-system flavor correction (docs/DESIGN.md section 13).

Pure function consuming expert_rules.flavor_correction from definitions.yaml
- see that key's own comment there for the full sourcing/derivation of each
tag's lever/direction/delta_g. Kept separate from runtime.py on purpose,
matching grind_correction.py's own split: the policy encoded here (which
tag maps to which lever, how big a correction it implies) is data, not
hardcoded.

See docs/DESIGN.md's Phase 5 for the full sequencing model implemented
here: resolve_flavor_state replays one bag+axis's own answered-response
history (no new persisted table - same derive-don't-persist convention as
runtime._all_flavor_field_recommendations) to reconstruct which lever is
currently "active" for that axis, at what step size, and what to
recommend next. Every notification asks the exact same question - the
axis's own two tags plus "balanced" - so every recorded response is one of
those three things:

- A tag report while no tag is tracked, or while it repeats the tag
  already being tracked, applies (or re-applies) the current stage's
  *current* step - there's no separate "did this help?" round to wait
  for, and persisting doesn't grow or shrink the step on its own.
- A tag report for the axis's *other* tag is either an unrelated,
  independent problem (when the two tags map to different levers - an
  "uncoupled" pair, e.g. thin_weak/dry_astringent - the new tag starts
  fresh at its own stage's base step) or an overshoot signal on the very
  lever just pushed (when they map to the same lever, just opposite
  directions - a "coupled" pair, e.g. sour_sharp/bitter_harsh). An
  overshoot is corrected the way grind_correction corrects its own
  overshoots (docs/DESIGN.md's Phase 4): the *current* step, damped
  (halved), in the new tag's own direction - never a same-sized reversal,
  and never re-derived from the reporting tag's own nominal step, since
  coupled tags share one physical lever and a step already known to be
  too coarse for it doesn't stop being too coarse just because the other
  tag reported next. Once the damped step would be smaller than the
  lever's own minimum_meaningful_step, the lever has converged as far as
  it can go - escalate to the tag's `escalation` lever instead (if one is
  defined), at that stage's own base step, the same way grind_correction's
  own convergence to "healthy" is what hands off to flavor correction in
  the first place. With nowhere further to escalate to, no more automatic
  recommendation is offered for that axis - a same-lever tug-of-war with
  no way out belongs in front of the user, not something to keep silently
  re-nudging.
- `balanced` fully resets the axis back to no active lever.

`active_tag` always tracks whichever tag was *most recently* reported,
never the tag that started the sequence - escalating has to use the
lever mapping of whichever problem is actually current, or it moves the
escalated lever in the wrong direction (see this module's own tests for a
worked example).

Every response is recorded through the same storage.record_flavor_tag/
recent_flavor_tags mechanism used before (a plain TEXT column, no schema
change) - see runtime.py's _async_send_flavor_feedback_notifications for
how the notification is built and _handle_flavor_notification_action for
how a tapped response is recorded.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

_LEVER_TO_FIELD = {
    "yield": "target_yield_g",
    "dose": "dose_g",
    "temperature": "temperature_offset_c",
    "preinfusion": "preinfusion_s",
}

# How much smaller each overshoot correction is than the step that caused
# it - a project-level implementation choice, not sourced from any
# transcript (the sourced evidence only says "smaller," not "half").
# grind_correction's own overshoot damping (docs/DESIGN.md's Phase 4)
# encodes the same evidence as a discrete "one band-tier back" instead,
# for a different reason (grinders themselves aren't uniform, so its
# corrections have to be relative positions on a per-grinder ladder) -
# yield/dose/temperature are universal physical units, so a continuous
# halving is the simpler analog here. Both ratios should be checked
# against real accumulated shot fixtures once there's enough data, per
# this project's standing rule against inventing tuned constants without
# validating them.
_DAMPING_RATIO = 0.5


def _lever_for(tag_config: dict[str, Any], stage: str) -> str | None:
    """Which lever `tag_config` uses at `stage`, or None if it has no
    `escalation` block and stage is "escalated" - used to decide whether
    two tags are "coupled" (same lever at the current stage) or
    "uncoupled" (different levers), and to look up that lever's own
    field/direction."""
    if stage == "primary":
        return tag_config["lever"]
    escalation = tag_config.get("escalation")
    if escalation is None:
        return None
    return escalation.get("lever", tag_config["lever"])


def _direction_for(tag_config: dict[str, Any], stage: str) -> str:
    """tag_config's own configured direction at `stage` - never reversed;
    an overshoot correction is a smaller step in the *reported* tag's own
    direction, not a flip of the previous tag's direction."""
    if stage == "primary":
        return tag_config["direction"]
    return tag_config.get("escalation", {}).get("direction", tag_config["direction"])


def _base_step(tag_config: dict[str, Any], stage: str, config: dict[str, Any]) -> float:
    """The nominal (undamped) step for tag_config's own lever at `stage` -
    delta_g, or the field's own minimum_meaningful_step fallback.
    Escalation never carries its own delta_g in definitions.yaml (only
    condition/lever/direction), so an escalated stage always falls back to
    minimum_meaningful_step, same as a primary stage that doesn't specify
    delta_g either."""
    if stage == "primary":
        delta_g = tag_config.get("delta_g")
    else:
        delta_g = tag_config.get("escalation", {}).get("delta_g")
    if delta_g is not None:
        return float(delta_g)
    field = _LEVER_TO_FIELD[_lever_for(tag_config, stage)]
    return float(config["minimum_meaningful_step"][field])


def _recommendation(tag_config: dict[str, Any], stage: str, step: float) -> dict[str, Any]:
    lever = _lever_for(tag_config, stage)
    return {
        "lever": lever,
        "field": _LEVER_TO_FIELD[lever],
        "direction": _direction_for(tag_config, stage),
        "delta": step,
    }


def resolve_flavor_state(history: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Replay one bag+axis's own answered-response history (oldest first -
    the reverse of storage.recent_flavor_tags's own newest-first order) to
    reconstruct the current escalation state for this axis. See module
    docstring for the full transition rules.

    Returns {"recommendation", "active_tag"}:
    - "recommendation" is {"lever","field","direction","delta"} for the
      next shot, or None when there's nothing to apply - either because
      nothing is being tracked (a fresh/reset axis - "active_tag" is also
      None then), or because the axis has converged as far as it can go
      with nowhere further to escalate to (a real, informative state -
      "active_tag" is still set then, and callers should surface that
      distinction to the user rather than treat it like a plain reset).
    - "active_tag" is whichever problem tag is currently being tracked
      for this axis, or None.
    """
    tags = config.get("tags", {})
    active_tag: str | None = None
    stage: str | None = None
    # The current stage's own step size - persists across a run of the
    # same tag (a "persisting" report neither grows nor shrinks it), halves
    # on each further coupled overshoot, and only ever resets to a fresh
    # base step when active_tag/stage genuinely restarts (a first-ever
    # report, an uncoupled switch, or an escalation into a new stage).
    step: float | None = None
    recommendation: dict[str, Any] | None = None

    for response in history:
        if response == "balanced":
            _LOGGER.debug("resolve_flavor_state: %r -> balanced, resetting to no active lever", response)
            active_tag = None
            stage = None
            step = None
            recommendation = None
            continue
        if response not in tags:
            _LOGGER.debug("resolve_flavor_state: %r ignored - not a configured tag", response)
            continue
        if active_tag is None:
            # Fresh report - first ever, or the first since a reset.
            active_tag = response
            stage = "primary"
            step = _base_step(tags[active_tag], stage, config)
            recommendation = _recommendation(tags[active_tag], stage, step)
            _LOGGER.debug(
                "resolve_flavor_state: fresh %s -> %s stage, step=%s: %s",
                active_tag, stage, step, recommendation,
            )
            continue
        if response == active_tag:
            # Persisting - confirmed correct direction, just not sufficient
            # yet. Keep going at the current step, unchanged.
            recommendation = _recommendation(tags[active_tag], stage, step)
            _LOGGER.debug(
                "resolve_flavor_state: %s persists at %s stage, step=%s: %s",
                active_tag, stage, step, recommendation,
            )
            continue
        # A different tag than active_tag was just reported.
        previous_lever = _lever_for(tags[active_tag], stage)
        new_lever = _lever_for(tags[response], stage)
        if new_lever is None or previous_lever != new_lever:
            # Uncoupled - not a correction of the lever just pushed, just
            # this axis's other, independent problem showing up. Starts
            # fresh at its own base step, same as a first-ever report.
            active_tag = response
            stage = "primary"
            step = _base_step(tags[active_tag], stage, config)
            recommendation = _recommendation(tags[active_tag], stage, step)
            _LOGGER.debug(
                "resolve_flavor_state: %s uncoupled from previous tag -> primary stage, "
                "step=%s: %s",
                active_tag, step, recommendation,
            )
            continue
        # Coupled - an overshoot signal. active_tag reassigns to the tag
        # actually reported: escalating has to use *this* tag's own lever
        # mapping, not whichever tag started the sequence, or the
        # escalated lever could move the wrong direction. The step being
        # damped is the *current* one (shared across this coupled pair,
        # since they're two labels for the same physical lever), not
        # re-derived from the newly-reported tag's own nominal step.
        active_tag = response
        damped_step = step * _DAMPING_RATIO
        floor = config["minimum_meaningful_step"][_LEVER_TO_FIELD[_lever_for(tags[active_tag], stage)]]
        if damped_step >= floor:
            step = damped_step
            recommendation = _recommendation(tags[active_tag], stage, step)
            _LOGGER.debug(
                "resolve_flavor_state: %s overshoot -> damped %s stage, step=%s: %s",
                active_tag, stage, step, recommendation,
            )
        elif stage == "primary" and tags[active_tag].get("escalation") is not None:
            stage = "escalated"
            step = _base_step(tags[active_tag], stage, config)
            recommendation = _recommendation(tags[active_tag], stage, step)
            _LOGGER.debug(
                "resolve_flavor_state: %s converged -> escalating to %s stage, step=%s: %s",
                active_tag, stage, step, recommendation,
            )
        else:
            recommendation = None
            _LOGGER.debug(
                "resolve_flavor_state: %s converged with nowhere further to escalate to - "
                "no automatic recommendation",
                active_tag,
            )

    result = {"recommendation": recommendation, "active_tag": active_tag}
    _LOGGER.debug("resolve_flavor_state: final state %s", result)
    return result
