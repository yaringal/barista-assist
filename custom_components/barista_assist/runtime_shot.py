"""The actual brew/stop/abort/finalize shot state machine, plus the
stop-latency-learning math it depends on to decide when to stop a shot.
Mixed into BaristaRuntime (see runtime.py) - not usable standalone."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .flow_analysis import (
    BaselineFeatures,
    FlowAnalysisConfig,
    ShotClassification,
    analyze_shot,
    blended_expected_flow_g_s,
)
from .grind_correction import recommend_grind_delta
from .runtime_shared import ActiveShot, ShotPhase, _recipe_snapshot
from .storage import ShotSample

_LOGGER = logging.getLogger(__name__)


class RuntimeShotMixin:
    """Mixed into BaristaRuntime - see that class for the shared __init__/state."""

    @staticmethod
    def _smoothed_flow_g_s(samples: list[ShotSample], window_ms: int) -> float:
        """Average flow_g_s (as reported by the scale itself) over the
        trailing window_ms of samples - smooths out single-reading noise
        without needing to numerically differentiate weight ourselves."""
        if not samples:
            return 0.0
        cutoff = samples[-1].elapsed_ms - window_ms
        recent: list[float] = []
        for sample in reversed(samples):
            if sample.elapsed_ms < cutoff:
                break
            recent.append(sample.flow_g_s)  # always >= 1 entry: the last sample itself
        return sum(recent) / len(recent)

    def _effective_stop_margin_g(self, shot: ActiveShot) -> float:
        """Live flow-projected stop margin - early_stop_margin_min_g's floor,
        raised when the shot's current flow rate implies more will land
        during the physical stop latency than that floor alone budgets for.

        Basically flow_now (eg 2 or 4g/s) * whichever learned latency bucket
        flow_now falls into (stop_latency_normal_s below
        stop_latency_calibration.bucket_cutoff_g_s, stop_latency_elevated_s
        at or above it - see that key's own comment in definitions.yaml for
        why two buckets instead of one shared value), clipped between
        early_stop_margin_min_g (the
        floor) and early_stop_margin_max_g (an explicit, independently-
        tunable cap - not a multiple of the floor, so raising the floor for a
        conservative baseline doesn't also silently raise how early a fast
        shot can be cut off, and vice versa).

        An earlier version derived an "implied latency" as
        early_stop_margin_min_g / flow_analysis_constants.expected_flow_g_s (a
        generic, cross-installation placeholder, not this bag's or this
        machine's actual typical flow rate) and multiplied that latency by
        live flow outright, replacing early_stop_margin_min_g entirely. For a
        real installation calibrated well above that generic reference, this
        implied a latency of 6.4s - physically absurd for a BLE press + pump
        stop - and triggered a real "good" shot's stop 6g/17% early with no
        actual problem to react to: a clear regression, not an improvement.

        Instead, early_stop_margin_min_g is always the floor (identical to
        pre-adaptive behavior at any normal flow rate - this can only ever
        raise the margin, never shrink it below what's already trusted), and
        the latency multiplied against live flow is a small, persisted,
        per-installation estimate that's *learned* from real completed shots
        (see _update_learned_stop_latency) rather than a fixed guess.
        """
        flow_now = max(
            0.0,
            self._smoothed_flow_g_s(
                shot.samples, self.definitions.flow_analysis_constants["smoothing_window_ms"]
            ),
        )
        latency_s = (
            self.stop_latency_elevated_s
            if flow_now >= self.definitions.stop_latency_calibration["bucket_cutoff_g_s"]
            else self.stop_latency_normal_s
        )
        projected_margin_g = flow_now * latency_s
        floor = shot.early_stop_margin_min_g
        ceiling = self.early_stop_margin_max_g
        return min(ceiling, max(floor, projected_margin_g))

    @staticmethod
    def _observed_stop_latency(
        samples: list[ShotSample],
        stop_command_elapsed_ms: int,
        final_weight: float,
        *,
        window_ms: int,
        min_flow_g_s: float,
    ) -> tuple[float, float] | None:
        """(flow_at_decision, observed_latency_s) for one completed shot with
        a recorded stop decision - the same computation
        _update_learned_stop_latency nudges stop_latency_normal_s/elevated_s
        with, factored out so tests/test_constant_drift.py's stop-latency
        bucket-drift report can reuse the exact formula rather than
        duplicating it. window_ms/min_flow_g_s are definitions.yaml's
        flow_analysis_constants.smoothing_window_ms/stop_latency_calibration.
        min_flow_for_learning_g_s - passed in explicitly since this is a
        staticmethod with no `self.definitions` of its own. None when
        there's nothing to learn from: no sample at or before the stop
        decision, or flow at that decision too slow to divide by
        meaningfully (below min_flow_g_s)."""
        decision_index = None
        for i, sample in enumerate(samples):
            if sample.elapsed_ms <= stop_command_elapsed_ms:
                decision_index = i
            else:
                break
        if decision_index is None:
            return None
        decision_sample = samples[decision_index]
        flow_at_decision = RuntimeShotMixin._smoothed_flow_g_s(
            samples[: decision_index + 1], window_ms
        )
        if flow_at_decision < min_flow_g_s:
            return None
        observed_latency_s = max(0.0, (final_weight - decision_sample.weight_g) / flow_at_decision)
        return (flow_at_decision, observed_latency_s)

    def _update_learned_stop_latency(self, shot: ActiveShot, final_weight: float) -> None:
        """Nudge whichever latency bucket this shot's own flow rate falls
        into (stop_latency_normal_s or stop_latency_elevated_s - see
        stop_latency_calibration.bucket_cutoff_g_s's own comment in
        definitions.yaml) toward what this shot's own tail actually needed.

        Called (see the call site in _async_finalize) for every completed
        shot with an analyzable trace, regardless of classification -
        unlike an earlier version, which only learned from healthy shots
        because a fast/channeling shot's flow doesn't hold roughly constant
        through the latency window this model assumes, and mixing its
        "observed latency" into one shared average risked dragging a single
        global estimate past the point where early_stop_margin_min_g's floor
        stops protecting an ordinary shot. Splitting into two buckets by the
        shot's own flow rate fixes that more directly than excluding shots
        by classification did: a fast shot's own flow rate is exactly the
        signal that tells this method which bucket it belongs to, so it
        becomes a valid, separate data point instead of a risk to filter
        out.

        Also skipped for a shot with no recorded stop decision (nothing to
        learn from) or where flow at that decision was too slow to divide by
        meaningfully - see _observed_stop_latency, which this delegates the
        actual computation to.
        """
        if shot.stop_command_elapsed_ms is None:
            return
        calibration = self.definitions.stop_latency_calibration
        result = self._observed_stop_latency(
            shot.samples,
            shot.stop_command_elapsed_ms,
            final_weight,
            window_ms=self.definitions.flow_analysis_constants["smoothing_window_ms"],
            min_flow_g_s=calibration["min_flow_for_learning_g_s"],
        )
        if result is None:
            return
        flow_at_decision, observed_latency_s = result
        elevated = flow_at_decision >= calibration["bucket_cutoff_g_s"]
        previous = self.stop_latency_elevated_s if elevated else self.stop_latency_normal_s
        updated = min(
            calibration["max_s"],
            max(
                calibration["min_s"],
                previous + calibration["learning_rate"] * (observed_latency_s - previous),
            ),
        )
        if elevated:
            self.stop_latency_elevated_s = updated
        else:
            self.stop_latency_normal_s = updated
        _LOGGER.info(
            "Shot %s: observed stop latency %.2fs (tail=%.2fg at flow=%.2fg/s at "
            "decision) -> stop_latency_%s_s %.2fs -> %.2fs",
            shot.id,
            observed_latency_s,
            observed_latency_s * flow_at_decision,  # tail_g = observed_latency_s * flow_at_decision, by definition
            flow_at_decision,
            "elevated" if elevated else "normal",
            previous,
            updated,
        )

    def _puck_prep_issue_streak_reached(self, streak: int) -> bool:
        """Whether `streak` consecutive puck_prep_issue-at-unchanged-recipe
        shots has reached self.puck_prep_issue_streak_threshold - the live,
        dashboard-editable value (see _PUCK_PREP_STREAK_CONTROLLER_FIELDS),
        not just the YAML default - shared by the actual grind-delta
        override (_puck_prep_streak_coarsen_override) and the display note
        (runtime_entities.py's _puck_prep_streak_note) so the two can never
        disagree."""
        return streak >= self.puck_prep_issue_streak_threshold

    async def _puck_prep_streak_coarsen_override(
        self, shot: ActiveShot, current_recipe: dict[str, Any]
    ) -> float | None:
        """None unless this shot's own puck_prep_issue-at-unchanged-recipe
        streak (including itself - the storage query only sees prior
        shots, since this shot's own row isn't persisted yet) has reached
        the streak threshold. A bag+recipe landing on puck_prep_issue this
        many shots in a row is no longer random bad technique - override
        "repeat, don't touch grind" with a coarsening recommendation
        instead."""
        prior_streak = await self.hass.async_add_executor_job(
            self.db.consecutive_puck_prep_issue_count,
            shot.bag.id,
            {**current_recipe, "grind": shot.bag.grind},
        )
        if not self._puck_prep_issue_streak_reached(prior_streak + 1):
            return None
        return self.puck_prep_issue_streak_coarsen_delta

    # -- brew / stop / abort / finalize --
    async def async_brew(self) -> str:
        _LOGGER.debug("async_brew called")
        async with self._shot_lock:
            if self.active_shot is not None:
                raise HomeAssistantError("A shot is already active")
            bag = self.selected_bag
            if bag is None:
                raise HomeAssistantError("Create/select an active bag before brewing")
            if not self.machine_limit_confirmed:
                raise HomeAssistantError(
                    "Confirm the Barista Express programmed maximum shot duration before brewing"
                )
            if self.safe_shot_deadline_s <= 5:
                raise HomeAssistantError(
                    "Machine maximum shot duration must exceed the safety margin by at least 5 seconds"
                )
            preinfusion_s = bag.preinfusion_s if self.adapt_pi else self.machine_pi_s
            if preinfusion_s >= self.safe_shot_deadline_s:
                raise HomeAssistantError(
                    "Pre-infusion must be shorter than the protected shot window"
                )
            if bag.target_yield_g <= self.early_stop_margin_min_g + 1.0:
                raise HomeAssistantError("Stop compensation is too large for target yield")

            self._set_phase(ShotPhase.CONNECTING_SCALE)
            await self.scale.async_ensure_connected()
            await self.scale.async_wait_for_fresh_reading()

            # Fixed once here, at brew time, rather than left to be computed
            # only after the shot finishes (analyze_shot's own use of this)
            # - the Live Shot/Shot History charts' idealized-curve overlay
            # (_shot_markers) needs it available from the very first sample.
            expected_flow_g_s = blended_expected_flow_g_s(
                await self._async_roast_level_flow_baseline(bag.roast_level, bag.id),
                FlowAnalysisConfig(**self.definitions.flow_analysis_constants),
            )

            started_at = datetime.now(timezone.utc).isoformat()
            shot_id = await self.hass.async_add_executor_job(
                lambda: self.db.create_shot(
                    bag=bag,
                    started_at=started_at,
                    stop_compensation_g=self.early_stop_margin_min_g,
                    preinfusion_s=preinfusion_s,
                    adapt_pi=self.adapt_pi,
                    expected_flow_g_s=expected_flow_g_s,
                )
            )
            self.active_shot = ActiveShot(
                id=shot_id,
                bag=bag,
                started_at=started_at,
                started_monotonic=time.monotonic(),
                target_yield_g=bag.target_yield_g,
                early_stop_margin_min_g=self.early_stop_margin_min_g,
                preinfusion_s=preinfusion_s,
                expected_flow_g_s=expected_flow_g_s,
                samples=[],
                # With Adapt PI off, the machine runs its own pre-infusion on
                # a single short tap - the Bot is never reprogrammed away
                # from its default instant tap, so the stop press can go out
                # immediately whenever it's needed.
                quick_press_ready=not self.adapt_pi,
            )
            _LOGGER.info(
                "Shot %s started: bag=%s dose=%sg target_yield=%sg preinfusion=%ss (adapt_pi=%s)",
                shot_id,
                bag.coffee_name,
                bag.dose_g,
                bag.target_yield_g,
                preinfusion_s,
                self.adapt_pi,
            )
            self._set_phase(ShotPhase.PREINFUSION)

            try:
                if self.adapt_pi:
                    # The app adapts pre-infusion itself: hold the button for
                    # this bag's configured preinfusion_s rather than using
                    # the machine's own fixed default.
                    await self._async_prepare_brew_bot(int(preinfusion_s))
                    await self._async_press_brew_bot()
                else:
                    # Machine-controlled: the Barista Express runs its own
                    # pre-infusion on a single short tap - no hold duration
                    # to program, so this skips the direct-BLE Bot-configure
                    # step entirely and presses through Home Assistant's
                    # switchbot integration only.
                    await self._async_press_brew_bot()
            except Exception:
                await self._async_finalize(ShotPhase.ERROR.value)
                raise
            self.active_shot.press_monotonic = time.monotonic()
            connect_delay_s = self.active_shot.press_monotonic - self.active_shot.started_monotonic
            _LOGGER.debug(
                "Brew Bot engaged %.2fs after brew was requested (BLE connect+program+press)",
                connect_delay_s,
            )

            self._phase_task = self.hass.async_create_background_task(
                self._mark_extracting_after_preinfusion(shot_id, preinfusion_s),
                "barista_assist_preinfusion_phase",
            )
            self._timeout_task = self.hass.async_create_background_task(
                self._shot_timeout(), "barista_assist_shot_timeout"
            )

            # Tare and start the scale's own physical timer only now that the
            # machine is actually engaged, so both its on-device display and
            # our own zero-weight reference reflect the real start of the
            # shot rather than however long the Bluetooth connection above
            # took. The machine is already pouring by this point - unlike a
            # failure earlier in this method, there's no "the shot never
            # happened" to fall back to, so this can only warn and continue
            # with an untared baseline rather than abort a shot that's
            # already physically running.
            try:
                await self.scale.async_set_flow_smoothing(False)
                await self.scale.async_tare_and_start_timer()
            except Exception as err:
                _LOGGER.warning(
                    "Could not tare the scale or start its timer after "
                    "pressing the brew Bot; continuing with an untared "
                    "weight baseline: %s",
                    err,
                )
            return shot_id

    async def _mark_extracting_after_preinfusion(
        self, shot_id: str, preinfusion_s: float
    ) -> None:
        try:
            await asyncio.sleep(preinfusion_s)
            shot = self.active_shot
            if shot is None or shot.id != shot_id or shot.stop_scheduled:
                return
            self._set_phase(ShotPhase.EXTRACTING)
            if not self.adapt_pi:
                # Never held the button in the first place (see async_brew) -
                # machine-controlled shots are always a short tap, so there's
                # nothing to reprogram back to an instant tap.
                return
            # Reprogram the Bot to an instant tap now, off the time-critical stop
            # path, so the eventual stop/abort press doesn't also hold for
            # preinfusion_s seconds like the start press did. If a real stop
            # needs the Bot before this finishes, it will cancel this attempt
            # (see _async_ensure_quick_stop_press) rather than wait for it.
            try:
                await self._async_prepare_brew_bot(0)
                shot.quick_press_ready = True
            except Exception as err:
                _LOGGER.warning(
                    "Could not reprogram brew Bot for an instant stop press ahead of time: %s",
                    err,
                )
        except asyncio.CancelledError:
            return

    async def _shot_timeout(self) -> None:
        try:
            await asyncio.sleep(self.safe_shot_deadline_s)
            if self.active_shot is not None:
                _LOGGER.warning(
                    "Barista Assist shot reached its protected %.1fs stop deadline "
                    "(machine maximum %.1fs, margin %.1fs)",
                    self.safe_shot_deadline_s,
                    self.machine_max_shot_s,
                    self.safety_margin_s,
                )
                await self.async_abort(reason=ShotPhase.TIMEOUT.value)
        except asyncio.CancelledError:
            return

    @staticmethod
    def _elapsed_since_press(shot: ActiveShot) -> float:
        """Time since the machine was actually engaged, for safety-deadline
        checks - not since brewing was requested (see
        ActiveShot.press_monotonic). If the initial press hasn't landed yet,
        the machine isn't running at all yet, so there's nothing to protect
        against; treat it as 0 rather than the (BLE-connect-inflated) time
        since the request.
        """
        if shot.press_monotonic is None:
            return 0.0
        return time.monotonic() - shot.press_monotonic

    async def _async_press_stop(self, shot: ActiveShot, verb: str) -> None:
        """Press the brew Bot to stop/abort the shot, or set STOP_ERROR and raise.

        Resets stop_triggered back to False on failure: both callers set it
        to True *before* attempting the press, to stop a second concurrent
        caller from also pressing - but left True after a failed press, it
        permanently blocks every future stop/abort attempt for this shot
        (including the protected-deadline timeout's own safety-net abort),
        since they all guard on "if shot.stop_triggered: return". A
        transient BLE failure (e.g. BleakOutOfConnectionSlotsError) would
        otherwise leave the shot stuck in STOP_ERROR forever - active_shot
        never clears, Brew never re-enables, and even clicking Abort again
        silently no-ops - with no way out short of reloading the integration.
        _actuation_lock (held by both callers for the whole call) still
        prevents a real concurrent double-press: nothing can observe
        stop_triggered as False again until this method has already returned
        and the lock has been released.
        """
        try:
            await self._async_ensure_quick_stop_press(shot)
            await self._async_press_brew_bot()
        except Exception as err:
            _LOGGER.exception("Failed to press brew Bot while trying to %s the shot", verb)
            self._set_phase(ShotPhase.STOP_ERROR)
            shot.stop_triggered = False
            raise HomeAssistantError(f"Failed to {verb} shot: {err}") from err

    async def async_stop_at_target(self, shot_id: str) -> None:
        """shot_id pins this to the exact shot _handle_reading scheduled it
        for - the same guard _mark_extracting_after_preinfusion already
        uses. Without it, a scheduled-but-not-yet-run stop from a shot
        that was instead finalized manually (e.g. a test pushing a final
        reading right at/after the auto-stop threshold, then calling
        _async_finalize directly rather than waiting out this task) would
        still fire later against whatever shot is active by the time the
        event loop gets to it - silently stopping a completely different,
        newer shot."""
        late = False
        async with self._actuation_lock:
            shot = self.active_shot
            if shot is None or shot.id != shot_id or shot.stop_triggered:
                return
            elapsed_s = self._elapsed_since_press(shot)
            _LOGGER.debug("async_stop_at_target called at elapsed=%.2fs", elapsed_s)
            if elapsed_s >= self.safe_shot_deadline_s:  # too close to the deadline to safely press again - see async_abort
                late = True
            else:
                shot.stop_triggered = True
                shot.stop_command_elapsed_ms = int(elapsed_s * 1000)
                self._set_phase(ShotPhase.STOPPING)
                await self._async_press_stop(shot, "stop")
                self._set_phase(ShotPhase.SETTLING)
                self._settle_task = self.hass.async_create_background_task(
                    self._settle_then_finalize(), "barista_assist_settle"
                )
        if late:
            await self.async_abort(reason=ShotPhase.TIMEOUT.value)

    def _settle_seconds(self) -> float:
        """How long to keep recording samples after a stop/abort press lands
        before finalizing - see stop_latency_calibration.settle_buffer_s/
        min_settle_s's own comment in definitions.yaml. Sized off the
        larger of the two learned latencies, since either bucket could
        apply to whatever shot just finished."""
        calibration = self.definitions.stop_latency_calibration
        larger_latency = max(self.stop_latency_normal_s, self.stop_latency_elevated_s)
        return max(calibration["min_settle_s"], larger_latency + calibration["settle_buffer_s"])

    async def _settle_then_finalize(self) -> None:
        try:
            await asyncio.sleep(self._settle_seconds())
            await self._async_finalize("complete")
        except asyncio.CancelledError:
            return

    async def async_abort(self, *, reason: str = ShotPhase.ABORTED.value) -> None:
        async with self._actuation_lock:
            shot = self.active_shot
            if shot is None or shot.stop_triggered:
                return
            shot.stop_triggered = True
            elapsed_s = self._elapsed_since_press(shot)
            _LOGGER.info("async_abort called (reason=%s) at elapsed=%.2fs", reason, elapsed_s)
            shot.stop_command_elapsed_ms = int(elapsed_s * 1000)
            if elapsed_s < self.safe_shot_deadline_s:  # don't try to stop if machine auto-termination will be within safety_margin_s
                await self._async_press_stop(shot, "abort")
                await asyncio.sleep(self._settle_seconds())  # settle
                await self._async_finalize(reason)
                return

            # Never press after the protected deadline: pressing after a
            # single-tap/programmed-volume shot naturally ended would start a
            # new one instead of stopping anything. Barista Assist always
            # holds the button for pre-infusion, which Breville's own manual
            # documents as a distinct held mode from that single-tap one -
            # so the current shot may in fact still be running rather than
            # having ended, and there's no reliable way to tell those two
            # situations apart from software alone, so this stays
            # conservative and never presses either way. The machine does
            # appear to have its own independent volume-based safety cutoff
            # that applies here too (see README's shot-duration safety
            # section), but its exact timing isn't something Barista Assist
            # can rely on with full confidence - the user is expected to
            # physically stop the machine themselves if it's still pouring
            # past this point; Barista Assist has no way to detect that.
            _LOGGER.warning(
                "Past the protected deadline (elapsed=%.2fs) - not pressing the "
                "brew Bot; entering manual_stop_required",
                elapsed_s,
            )
            self._set_phase(ShotPhase.MANUAL_STOP_REQUIRED)
            remaining_s = max(0.0, self.machine_max_shot_s - elapsed_s + 1.0)
            self._manual_finalize_task = self.hass.async_create_background_task(
                self._finalize_after_late_abort(remaining_s, reason),
                "barista_assist_late_abort_finalize",
            )

    async def _finalize_after_late_abort(self, delay_s: float, reason: str) -> None:
        try:
            # wait for the machine's own timer to plausibly have ended the shot before closing the DB record
            await asyncio.sleep(delay_s)
            await self._async_finalize(reason)
        except asyncio.CancelledError:
            return

    async def _async_finalize(self, status: str) -> None:
        shot = self.active_shot
        if shot is None:
            return
        _LOGGER.info(
            "Finalizing shot %s as %s (%d samples)", shot.id, status, len(shot.samples)
        )
        try:
            await self.scale.async_stop_timer()
        except Exception as err:
            _LOGGER.debug("Could not stop the scale's own timer: %s", err)
        for task in self._background_tasks():
            if task and task is not asyncio.current_task():
                task.cancel()
        last_weight = shot.samples[-1].weight_g if shot.samples else None

        baseline_features = await self.hass.async_add_executor_job(
            self.db.recent_healthy_features, shot.bag.id
        )
        previous_grind_correction_shot = await self.hass.async_add_executor_job(
            self.db.previous_grind_correction_shot, shot.bag.id, shot.id
        )
        flow_analysis_config = FlowAnalysisConfig(**self.definitions.flow_analysis_constants)
        analysis = analyze_shot(
            shot.samples,
            target_yield_g=shot.target_yield_g,
            preinfusion_s=shot.preinfusion_s,
            baseline=BaselineFeatures(**baseline_features) if baseline_features else None,
            # Reuse the exact value async_brew already computed and
            # persisted, rather than re-fetching/re-blending the roast-level
            # pool here - the pool can genuinely change between brew and
            # finalize now (a different bag's shot completing), and
            # classification must match what the live chart already showed
            # the user, not silently diverge from it.
            expected_flow_g_s=shot.expected_flow_g_s,
            config=flow_analysis_config,
        )
        if analysis.invalid_reason is not None:
            _LOGGER.warning(
                "Shot %s could not be flow-classified (%s); excluded from diagnostics and future baselines",
                shot.id,
                analysis.invalid_reason,
            )
        elif status == "complete" and last_weight is not None:
            self._update_learned_stop_latency(shot, last_weight)
            await self._async_save_state()

        # Phase 4 (docs/DESIGN.md section 28): a recommended DF54 delta for
        # this shot's flow-rate deviation, or None if this shot isn't a
        # grind-correction candidate at all (see grind_correction.py and
        # expert_rules.grind_correction's own comment in definitions.yaml).
        current_recipe = _recipe_snapshot(shot.bag)
        recommended_grind_delta = recommend_grind_delta(
            analysis.classification,
            analysis.duration_ratio,
            self._grind_correction_config(),
            current_recipe=current_recipe,
            previous_shot=previous_grind_correction_shot,
        )

        if analysis.classification == ShotClassification.PUCK_PREP_ISSUE:
            override = await self._puck_prep_streak_coarsen_override(shot, current_recipe)
            if override is not None:
                recommended_grind_delta = override

        await self.hass.async_add_executor_job(
            lambda: self.db.finalize_shot(
                shot.id,
                ended_at=datetime.now(timezone.utc).isoformat(),
                actual_yield_g=last_weight,
                status=status,
                stop_command_elapsed_ms=shot.stop_command_elapsed_ms,
                effective_stop_margin_g=shot.effective_stop_margin_g,
                samples=shot.samples,
                classification=str(analysis.classification),
                channeling_suspicion=analysis.channeling_suspicion,
                analysis_json=json.dumps(asdict(analysis)),
                recommended_grind_delta=recommended_grind_delta,
            )
        )
        self._last_shot_samples = shot.samples
        self.active_shot = None
        self._set_phase(ShotPhase.IDLE if status == "complete" else ShotPhase(status))
        await self.async_refresh_cache()
        # Stage 2 (taste) only engages once a shot is healthy - no point
        # asking about flavor on a shot that was still mechanically or
        # hydraulically broken.
        if status == "complete" and analysis.classification == ShotClassification.HEALTHY:
            self._schedule_flavor_feedback_notifications(shot.id, shot.bag.id)

    # -- shot history (backs websocket.py's export/list/samples/delete) --
    async def async_export_shots_text(self, shot_id: str | None = None) -> str:
        """Return every stored shot and raw time series as paste-friendly
        text, or just one shot's when shot_id is given."""
        _LOGGER.debug("Exporting shot text (shot_id=%s)", shot_id or "all")
        return await self.hass.async_add_executor_job(self.db.export_shots_text, shot_id)

    async def async_list_shots(self) -> list[dict[str, Any]]:
        """Every stored shot, most recent first, for the shot-history view."""
        return await self.hass.async_add_executor_job(lambda: self.db.recent_shots(limit=None))

    async def async_shot_samples(self, shot_id: str) -> list[dict[str, Any]]:
        """One shot's raw scale time series, for the shot-history view's graph."""
        return await self.hass.async_add_executor_job(self.db.shot_samples, shot_id)

    async def async_delete_shot(self, shot_id: str) -> bool:
        """Delete one stored shot. Refuses to delete the shot currently
        being brewed - that would corrupt its in-flight sample writes and
        the runtime's own active-shot state - everything else is fair game."""
        if self.active_shot is not None and self.active_shot.id == shot_id:
            raise HomeAssistantError("Cannot delete the shot that is currently brewing")
        deleted = await self.hass.async_add_executor_job(self.db.delete_shot, shot_id)
        _LOGGER.debug("Delete shot %s: %s", shot_id, "deleted" if deleted else "not found")
        if deleted:
            await self.async_refresh_cache()
        return deleted
