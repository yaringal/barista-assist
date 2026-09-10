"""Phase 5 expert-system flavor correction (docs/DESIGN.md section 13).

Pure function consuming expert_rules.flavor_correction from definitions.yaml
- see that key's own comment there for the full sourcing/derivation of each
tag's lever/direction/delta_g. Kept separate from runtime.py on purpose,
matching grind_correction.py's own split: the policy encoded here (which
tag maps to which lever, how big a correction it implies) is data, not
hardcoded.

Unlike grind_correction, the signal this consumes (a taste tag) is
self-reported rather than measured. See docs/DESIGN.md's Phase 5 for the
full sequencing model implemented here:
resolve_flavor_state replays one bag+axis's own answered-response history
(no new persisted table - same derive-don't-persist convention as
runtime._all_flavor_field_recommendations) to reconstruct which lever is
currently "active" for that axis and what to do next:

- A tag's first report (or a different tag than whatever was being tracked)
  applies that tag's own primary lever immediately - no persistence gate on
  the *first* report (this replaces the old require_persistent_pattern_shots
  gate, which used to require the tag before recommending anything at all).
- The outcome of a just-applied intervention is checked with an explicit
  "did this improve?" follow-up (better/same/worse) rather than a fixed
  intensify-count - "better" repeats the same lever again; "same"/"worse"
  stop and re-ask the plain tag question as a "still <tag>?" confirmation;
  "worse" additionally reverses the next application once (an overshoot
  revert).
- If the confirmation still reports the same tag, escalate to that tag's
  own `escalation` lever (if defined - `thin_weak`/`dry_astringent` have
  none; there's nowhere further to escalate to for those, so confirmation
  just keeps re-nudging the same lever - a project implementation choice,
  not sourced, since no transcript covers this case).
- `balanced` fully resets the axis back to no active lever.

better/same/worse are recorded through the exact same
storage.record_flavor_tag/recent_flavor_tags mechanism as the original
tags (a plain TEXT column, no schema change) - see runtime.py's
_async_send_flavor_feedback_notifications/_handle_flavor_notification_action
for how the notification wording switches between the two question types.
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

# The three possible answers to the "did this improve <axis>?" follow-up -
# distinct from a problem tag or "balanced", which are the only other
# values ever recorded for an axis (see module docstring).
_OUTCOME_RESPONSES = frozenset({"better", "same", "worse"})

# resolve_flavor_state's two mutually-exclusive "what's pending for this
# axis" phases (see that function's own local `phase` variable).
_AWAITING_OUTCOME_REPORT = "awaiting_outcome_report"
_AWAITING_PERSISTENCE_CONFIRMATION = "awaiting_persistence_confirmation"


def _stage_recommendation(
    tag_config: dict[str, Any], stage: str, config: dict[str, Any], *, reversed_direction: bool
) -> dict[str, Any]:
    """{"lever","field","direction","delta"} for one tag's primary or
    escalated stage. Escalation never carries its own delta_g in
    definitions.yaml (only condition/lever/direction) - always falls back
    to minimum_meaningful_step for whatever field it maps to, same
    fallback rule primary stages use when they don't specify delta_g
    either."""
    if stage == "primary":
        lever = tag_config["lever"]
        direction = tag_config["direction"]
        stage_config = tag_config
    else:
        escalation = tag_config.get("escalation", {})
        lever = escalation.get("lever", tag_config["lever"])
        direction = escalation.get("direction", tag_config["direction"])
        stage_config = escalation
    field = _LEVER_TO_FIELD[lever]
    delta_g = stage_config.get("delta_g")
    delta = float(delta_g) if delta_g is not None else float(config["minimum_meaningful_step"][field])
    if reversed_direction:
        direction = "decrease" if direction == "increase" else "increase"
    return {"lever": lever, "field": field, "direction": direction, "delta": delta}


def resolve_flavor_state(history: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Replay one bag+axis's own answered-response history (oldest first -
    the reverse of storage.recent_flavor_tags's own newest-first order) to
    reconstruct the full escalation state for this axis right now. See
    module docstring for the full transition rules.

    Returns {"recommendation", "next_question", "active_tag"}:
    - "recommendation" is {"lever","field","direction","delta"} for the
      next shot, or None when there's nothing to apply right now (no
      history/reset, or between a same/worse outcome and its confirmation
      with nothing to revert).
    - "next_question" is "outcome" (ask "did this improve <axis>?") when an
      intervention was just (re-)applied and its result hasn't been
      reported yet, or "tag" (ask the plain 3-button tag question,
      possibly functioning as a "still <tag>?" confirmation) otherwise.
    - "active_tag" is whichever problem tag is currently being tracked for
      this axis, or None.
    """
    tags = config.get("tags", {})
    active_tag: str | None = None
    stage: str | None = None
    # What kind of response this axis is waiting on next - the two phases
    # are mutually exclusive (setting one always clears the other), so one
    # variable with named values reads clearer than two independent-looking
    # booleans:
    # - _AWAITING_OUTCOME_REPORT: an intervention was just (re-)applied;
    #   the next response should be a better/same/worse outcome answer.
    # - _AWAITING_PERSISTENCE_CONFIRMATION: a same/worse was just reported;
    #   the next response should be a plain tag, acting as "still <tag>?".
    # - None: nothing pending (reset, or no history yet).
    phase: str | None = None
    recommendation: dict[str, Any] | None = None

    for response in history:
        if response == "balanced":
            _LOGGER.debug("resolve_flavor_state: %r -> balanced, resetting to no active lever", response)
            active_tag = None
            stage = None
            phase = None
            recommendation = None
            continue
        if response in _OUTCOME_RESPONSES:
            if phase != _AWAITING_OUTCOME_REPORT:
                # Stale/out-of-order data (an outcome answer with nothing
                # pending) - ignore rather than misinterpret it.
                _LOGGER.debug(
                    "resolve_flavor_state: %r ignored - not currently awaiting an outcome", response
                )
                continue
            if response == "better":
                # Repeat the same stage's own delta again - direction
                # unchanged, never a revert.
                assert active_tag is not None and stage is not None
                recommendation = _stage_recommendation(
                    tags[active_tag], stage, config, reversed_direction=False
                )
                _LOGGER.debug(
                    "resolve_flavor_state: better -> repeating %s stage for %s: %s",
                    stage,
                    active_tag,
                    recommendation,
                )
                continue
            # "same" or "worse": stop awaiting the outcome - the next tag
            # response acts as the "still <tag>?" confirmation. "worse"
            # additionally reverts once; "same" holds steady with nothing
            # new to apply until confirmed.
            phase = _AWAITING_PERSISTENCE_CONFIRMATION
            if response == "worse":
                assert active_tag is not None and stage is not None
                recommendation = _stage_recommendation(
                    tags[active_tag], stage, config, reversed_direction=True
                )
                _LOGGER.debug(
                    "resolve_flavor_state: worse -> reverting %s stage for %s: %s",
                    stage,
                    active_tag,
                    recommendation,
                )
            else:
                recommendation = None
                _LOGGER.debug(
                    "resolve_flavor_state: same -> holding steady, awaiting confirmation for %s",
                    active_tag,
                )
            continue
        # response is a problem tag (or something outside config["tags"] -
        # ignored, matching the old function's own "unknown tag" handling).
        if response not in tags:
            _LOGGER.debug("resolve_flavor_state: %r ignored - not a configured tag", response)
            continue
        if phase == _AWAITING_PERSISTENCE_CONFIRMATION and response == active_tag:
            # Confirmation: the same tag persisted through the same/worse
            # check - escalate if this tag defines an escalation lever,
            # otherwise stay at the current stage (nowhere further defined
            # to go - see module docstring).
            if stage == "primary" and tags[active_tag].get("escalation") is not None:
                stage = "escalated"
                _LOGGER.debug(
                    "resolve_flavor_state: %s confirmed persisting -> escalating to %s stage",
                    active_tag,
                    stage,
                )
            else:
                _LOGGER.debug(
                    "resolve_flavor_state: %s confirmed persisting -> no escalation defined, "
                    "re-nudging %s stage",
                    active_tag,
                    stage,
                )
            recommendation = _stage_recommendation(
                tags[active_tag], stage, config, reversed_direction=False
            )
        else:
            # Fresh report: first ever, a different tag than before, or a
            # tag reported outside the confirmation flow entirely.
            active_tag = response
            stage = "primary"
            recommendation = _stage_recommendation(
                tags[active_tag], stage, config, reversed_direction=False
            )
            _LOGGER.debug(
                "resolve_flavor_state: fresh report of %s -> primary stage: %s",
                active_tag,
                recommendation,
            )
        phase = _AWAITING_OUTCOME_REPORT

    result = {
        "recommendation": recommendation,
        "next_question": "outcome" if phase == _AWAITING_OUTCOME_REPORT else "tag",
        "active_tag": active_tag,
    }
    _LOGGER.debug("resolve_flavor_state: final state %s", result)
    return result
