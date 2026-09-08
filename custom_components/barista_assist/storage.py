"""SQLite persistence for bags, shots and raw scale samples."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable
from uuid import uuid4

LATEST_SCHEMA_VERSION = 9
BAG_RECIPE_FIELDS = frozenset(
    {"dose_g", "grind", "target_yield_g", "temperature_offset_c", "preinfusion_s"}
)


@dataclass(slots=True)
class Bag:
    """A physical bag of coffee and its current recipe."""

    id: str
    slot: str
    coffee_name: str
    roaster: str | None
    roast_date: str | None
    opened_at: str
    starting_mass_g: float
    dose_g: float
    grind: float
    target_yield_g: float
    temperature_offset_c: int
    preinfusion_s: float
    roast_level: str | None
    active: bool = True


@dataclass(slots=True)
class ShotSample:
    """One raw scale sample during a shot."""

    seq: int
    elapsed_ms: int
    scale_ms: int
    weight_g: float
    flow_g_s: float
    battery_percent: int


_BAG_COLUMNS = tuple(field.name for field in fields(Bag))


class BaristaDatabase:
    """Small synchronous SQLite repository; call from Home Assistant's executor."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.migrations_dir = Path(__file__).with_name("storage_migrations")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self, *, legacy_preinfusion_s: float = 7.0) -> int:
        """Apply migrations and return the schema version found before migration."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            current = int(db.execute("PRAGMA user_version").fetchone()[0])
            for version in range(current + 1, LATEST_SCHEMA_VERSION + 1):
                matches = sorted(self.migrations_dir.glob(f"{version:03d}_*.sql"))
                if len(matches) != 1:
                    raise RuntimeError(
                        f"Expected exactly one database migration for version {version}"
                    )
                db.executescript(matches[0].read_text(encoding="utf-8"))
                if version == 2 and current == 1:
                    # Preserve the old integration-wide PI value on existing
                    # active bags only: v0.1 never tracked PI per bag, so
                    # there is no historical value to restore for bags that
                    # were already archived before this migration runs.
                    db.execute(
                        "UPDATE bags SET preinfusion_s=? WHERE active=1",
                        (float(legacy_preinfusion_s),),
                    )
                db.execute(f"PRAGMA user_version={version}")
        return current

    def legacy_selected_slot(self) -> str | None:
        """Read the v0.1 UI setting during upgrade; new code does not write it."""
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
            ).fetchone()
            if not exists:
                return None
            row = db.execute(
                "SELECT value FROM settings WHERE key='selected_slot'"
            ).fetchone()
        return str(row["value"]) if row else None

    @staticmethod
    def _row_to_bag(row: sqlite3.Row) -> Bag:
        return Bag(**{column: row[column] for column in _BAG_COLUMNS})

    def new_bag(
        self,
        *,
        slot: str,
        coffee_name: str,
        roaster: str | None,
        roast_date: str | None,
        starting_mass_g: float,
        dose_g: float,
        grind: float,
        target_yield_g: float,
        temperature_offset_c: int,
        preinfusion_s: float,
        roast_level: str | None,
    ) -> Bag:
        bag = Bag(
            id=uuid4().hex,
            slot=slot,
            coffee_name=coffee_name.strip(),
            roaster=(roaster or "").strip() or None,
            roast_date=roast_date or None,
            opened_at=datetime.now(timezone.utc).isoformat(),
            starting_mass_g=float(starting_mass_g),
            dose_g=float(dose_g),
            grind=float(grind),
            target_yield_g=float(target_yield_g),
            temperature_offset_c=int(temperature_offset_c),
            preinfusion_s=float(preinfusion_s),
            roast_level=roast_level or None,
        )
        with self._connect() as db:
            db.execute("UPDATE bags SET active=0 WHERE slot=? AND active=1", (slot,))
            db.execute(
                """
                INSERT INTO bags(
                    id, slot, coffee_name, roaster, roast_date, opened_at,
                    starting_mass_g, dose_g, grind, target_yield_g,
                    temperature_offset_c, preinfusion_s, roast_level, active
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                """,
                (
                    bag.id,
                    bag.slot,
                    bag.coffee_name,
                    bag.roaster,
                    bag.roast_date,
                    bag.opened_at,
                    bag.starting_mass_g,
                    bag.dose_g,
                    bag.grind,
                    bag.target_yield_g,
                    bag.temperature_offset_c,
                    bag.preinfusion_s,
                    bag.roast_level,
                ),
            )
        return bag

    def active_bags(self) -> dict[str, Bag]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM bags WHERE active=1 ORDER BY opened_at DESC"
            ).fetchall()
        return {row["slot"]: self._row_to_bag(row) for row in rows}

    def update_recipe_field(self, bag_id: str, field: str, value: float | int) -> None:
        """Update one whitelisted recipe field."""
        if field not in BAG_RECIPE_FIELDS:
            raise ValueError(f"Unknown recipe field: {field}")
        if field == "temperature_offset_c":
            value = int(value)
        else:
            value = float(value)
        with self._connect() as db:
            db.execute(
                f"UPDATE bags SET {field}=? WHERE id=? AND active=1",
                (value, bag_id),
            )

    def create_shot(
        self,
        *,
        bag: Bag,
        started_at: str,
        stop_compensation_g: float,
        preinfusion_s: float,
        adapt_pi: bool,
        expected_flow_g_s: float | None = None,
    ) -> str:
        """preinfusion_s is the shot's actual effective pre-infusion duration
        (whichever of bag.preinfusion_s / the machine's own default was truly
        used - see BaristaRuntime.async_brew), not necessarily bag.preinfusion_s
        itself: a bag's recipe field only applies when Adapt PI is on.
        expected_flow_g_s is flow_analysis.blended_expected_flow_g_s's own
        rate for this bag's roast_level (docs/todo/ADAPTIVE_LEARNING_PLAN.md
        §2.1 - not this bag's own history), fixed once here at brew time
        (see async_brew) so the Live Shot/Shot History charts' idealized-
        curve overlay stays consistent for this shot even if the roast-level
        pool changes before it finishes - None only for shots created
        without that computation (e.g. most direct storage-layer tests)."""
        shot_id = uuid4().hex
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO shots(
                    id, bag_id, started_at, dose_g, grind, target_yield_g,
                    temperature_offset_c, preinfusion_s, stop_compensation_g, status, adapt_pi,
                    expected_flow_g_s
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    shot_id,
                    bag.id,
                    started_at,
                    bag.dose_g,
                    bag.grind,
                    bag.target_yield_g,
                    bag.temperature_offset_c,
                    float(preinfusion_s),
                    float(stop_compensation_g),
                    "running",
                    int(adapt_pi),
                    expected_flow_g_s,
                ),
            )
        return shot_id

    def finalize_shot(
        self,
        shot_id: str,
        *,
        ended_at: str,
        actual_yield_g: float | None,
        status: str,
        stop_command_elapsed_ms: int | None,
        samples: Iterable[ShotSample],
        classification: str | None = None,
        channeling_suspicion: float | None = None,
        analysis_json: str | None = None,
        effective_stop_margin_g: float | None = None,
        recommended_grind_delta: float | None = None,
    ) -> None:
        """effective_stop_margin_g is the live-projected margin actually used
        at this shot's own automatic target-weight stop decision (see
        ActiveShot.effective_stop_margin_g) - None for a shot ended by manual
        abort/timeout instead, since no weight-triggered margin was ever
        computed for it. recommended_grind_delta is Phase 4's
        (docs/DESIGN.md section 28) expert-system grind recommendation for
        this shot (grind_correction.recommend_grind_delta) - None when the
        shot isn't a grind-correction candidate (not too_fast/too_restrictive,
        or excluded as puck_prep_issue/invalid_measurement)."""
        sample_list = list(samples)
        with self._connect() as db:
            db.execute(
                """
                UPDATE shots
                SET ended_at=?, actual_yield_g=?, status=?,
                    stop_command_elapsed_ms=?, sample_count=?,
                    classification=?, channeling_suspicion=?, analysis_json=?,
                    effective_stop_margin_g=?, recommended_grind_delta=?
                WHERE id=?
                """,
                (
                    ended_at,
                    actual_yield_g,
                    status,
                    stop_command_elapsed_ms,
                    len(sample_list),
                    classification,
                    channeling_suspicion,
                    analysis_json,
                    effective_stop_margin_g,
                    recommended_grind_delta,
                    shot_id,
                ),
            )
            db.executemany(
                """
                INSERT OR REPLACE INTO samples(
                    shot_id, seq, elapsed_ms, scale_ms, weight_g,
                    flow_g_s, battery_percent
                ) VALUES(?,?,?,?,?,?,?)
                """,
                [
                    (
                        shot_id,
                        sample.seq,
                        sample.elapsed_ms,
                        sample.scale_ms,
                        sample.weight_g,
                        sample.flow_g_s,
                        sample.battery_percent,
                    )
                    for sample in sample_list
                ],
            )

    def last_shot(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT s.*, b.coffee_name, b.slot, b.roaster
                FROM shots s JOIN bags b ON b.id=s.bag_id
                ORDER BY s.started_at DESC LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def recent_shots(self, limit: int | None = 10) -> list[dict[str, Any]]:
        """Shots most-recent-first. Pass limit=None for every stored shot
        (the shot-history view's list)."""
        query = """
            SELECT s.*, b.coffee_name, b.slot, b.roaster
            FROM shots s JOIN bags b ON b.id=s.bag_id
            ORDER BY s.started_at DESC
        """
        with self._connect() as db:
            if limit is None:
                rows = db.execute(query).fetchall()
            else:
                rows = db.execute(query + " LIMIT ?", (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def shot_samples(self, shot_id: str) -> list[dict[str, Any]]:
        """One shot's raw scale time series, in order - what the shot-history
        view's graph plots."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT seq, elapsed_ms, weight_g, flow_g_s
                FROM samples WHERE shot_id=? ORDER BY seq ASC
                """,
                (shot_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def full_shot_samples(self, shot_id: str) -> list[ShotSample]:
        """Same rows as shot_samples(), but as real ShotSample objects (all
        six columns, not just the four the shot-history view needs) - used
        to restore BaristaRuntime._last_shot_samples after a restart, since
        that field is otherwise only ever populated in-memory when a shot
        finishes during the current runtime session."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT seq, elapsed_ms, scale_ms, weight_g, flow_g_s, battery_percent
                FROM samples WHERE shot_id=? ORDER BY seq ASC
                """,
                (shot_id,),
            ).fetchall()
        return [ShotSample(**dict(row)) for row in rows]

    def delete_shot(self, shot_id: str) -> bool:
        """Delete one shot; its samples go with it via ON DELETE CASCADE.
        Returns whether a shot was actually found and deleted."""
        with self._connect() as db:
            cursor = db.execute("DELETE FROM shots WHERE id=?", (shot_id,))
        return cursor.rowcount > 0

    def export_shots_text(self, shot_id: str | None = None) -> str:
        """Export a paste-friendly text block for every stored shot and its
        raw scale samples, or - when shot_id is given - just that one shot
        (the shot-history card's per-row export button)."""
        with self._connect() as db:
            shots = db.execute(
                """
                SELECT
                    s.*,
                    b.coffee_name, b.slot, b.roaster, b.roast_date, b.roast_level,
                    b.opened_at, b.starting_mass_g
                FROM shots s
                JOIN bags b ON b.id=s.bag_id
                WHERE (? IS NULL OR s.id = ?)
                ORDER BY s.started_at ASC
                """,
                (shot_id, shot_id),
            ).fetchall()

            sample_rows = db.execute(
                """
                SELECT shot_id, seq, elapsed_ms, scale_ms, weight_g, flow_g_s, battery_percent
                FROM samples
                WHERE (? IS NULL OR shot_id = ?)
                ORDER BY shot_id ASC, seq ASC
                """,
                (shot_id, shot_id),
            ).fetchall()

        samples_by_shot: dict[str, list[sqlite3.Row]] = {}
        for row in sample_rows:
            samples_by_shot.setdefault(str(row['shot_id']), []).append(row)

        def clean(value: object) -> str:
            return str(value if value is not None else '').replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')

        lines = [
            '# Barista Assist raw shot export',
            '# One metadata block + raw scale time series per shot.',
            '# Sample columns: seq\telapsed_ms\tscale_ms\tweight_g\tflow_g_s\tbattery_percent\tpost_stop',
            '# post_stop=1 means the sample was recorded after the stop command.',
            '# preinfusion_s is the shot\'s actual effective pre-infusion duration - the bag\'s',
            '# own preinfusion_s recipe field when adapt_pi=True, the machine\'s own',
            '# configured default (machine_pi_s) when adapt_pi=False.',
            '',
        ]

        for shot in shots:
            shot_id = str(shot['id'])
            lines.extend([
                '[SHOT]',
                f"shot_id={shot_id}",
                f"bag_id={clean(shot['bag_id'])}",
                f"slot={clean(shot['slot'])}",
                f"coffee_name={clean(shot['coffee_name'])}",
                f"roaster={clean(shot['roaster'])}",
                f"roast_date={clean(shot['roast_date'])}",
                f"roast_level={clean(shot['roast_level'])}",
                f"bag_opened_at={clean(shot['opened_at'])}",
                f"started_at={clean(shot['started_at'])}",
                f"ended_at={clean(shot['ended_at'])}",
                f"status={clean(shot['status'])}",
                f"classification={clean(shot['classification'])}",
                f"channeling_suspicion={shot['channeling_suspicion'] if shot['channeling_suspicion'] is not None else ''}",
                f"recommended_grind_delta={shot['recommended_grind_delta'] if shot['recommended_grind_delta'] is not None else ''}",
                f"expected_flow_g_s={shot['expected_flow_g_s'] if shot['expected_flow_g_s'] is not None else ''}",
                f"flavor_extraction_tag={clean(shot['flavor_extraction_tag'])}",
                f"flavor_mouthfeel_tag={clean(shot['flavor_mouthfeel_tag'])}",
                f"analysis_json={clean(shot['analysis_json'])}",
                f"dose_g={shot['dose_g']}",
                f"grind={shot['grind']}",
                f"target_yield_g={shot['target_yield_g']}",
                f"actual_yield_g={shot['actual_yield_g'] if shot['actual_yield_g'] is not None else ''}",
                f"temperature_offset_c={shot['temperature_offset_c']}",
                f"preinfusion_s={shot['preinfusion_s']}",
                f"adapt_pi={bool(shot['adapt_pi'])}",
                f"stop_compensation_g={shot['stop_compensation_g']}",
                f"stop_command_elapsed_ms={shot['stop_command_elapsed_ms'] if shot['stop_command_elapsed_ms'] is not None else ''}",
                f"sample_count={shot['sample_count']}",
                '',
                'seq\telapsed_ms\tscale_ms\tweight_g\tflow_g_s\tbattery_percent\tpost_stop',
            ])

            stop_ms = shot['stop_command_elapsed_ms']
            for sample in samples_by_shot.get(shot_id, []):
                post_stop = (
                    1 if stop_ms is not None and int(sample['elapsed_ms']) >= int(stop_ms) else 0
                )
                lines.append(
                    f"{sample['seq']}\t{sample['elapsed_ms']}\t{sample['scale_ms']}\t"
                    f"{sample['weight_g']:.3f}\t{sample['flow_g_s']:.4f}\t"
                    f"{sample['battery_percent']}\t{post_stop}"
                )
            lines.extend(['', '[END_SHOT]', ''])

        return '\n'.join(lines).rstrip() + '\n'

    def bag_remaining_g(self, bag_id: str) -> float | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT b.starting_mass_g - COALESCE(SUM(s.dose_g), 0) AS remaining
                FROM bags b LEFT JOIN shots s
                  ON s.bag_id=b.id AND s.status IN ('complete','aborted','timeout')
                WHERE b.id=? GROUP BY b.id
                """,
                (bag_id,),
            ).fetchone()
        return float(row["remaining"]) if row and row["remaining"] is not None else None

    def recent_healthy_features(self, bag_id: str, limit: int = 5) -> dict[str, Any] | None:
        """Median channeling-suspicion features from a bag's own recent
        healthy shots (flow_analysis.BaselineFeatures - the current bag's
        median_late_accel only; the flow-rate reference is roast_level_baseline
        below, not scoped to this bag - see docs/todo/ADAPTIVE_LEARNING_PLAN.md
        §2.1/§2.8 for why). Returns None when the bag has no healthy shot
        history yet, matching analyze_shot's own handling of a missing
        baseline.
        """
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT analysis_json
                FROM shots
                WHERE bag_id=? AND classification='healthy' AND analysis_json IS NOT NULL
                ORDER BY started_at DESC LIMIT ?
                """,
                (bag_id, int(limit)),
            ).fetchall()
        if not rows:
            return None
        late_accels = [float(json.loads(row["analysis_json"])["late_accel"]) for row in rows]
        return {
            "shot_count": len(rows),
            "median_late_accel": statistics.median(late_accels),
        }

    def roast_level_baseline(
        self, roast_level: str | None, exclude_bag_id: str | None = None, limit: int = 200
    ) -> dict[str, Any] | None:
        """Pooled flow-rate/ratio/dose features from *other* bags' shots
        sharing roast_level - the shared roast-level-keyed aggregate
        `flow_analysis.RoastLevelFlowBaseline` and runtime.py's
        `_roast_level_seeded_target_yield_g`/`_roast_level_seeded_dose_g` all
        read from (docs/todo/ADAPTIVE_LEARNING_PLAN.md §2.1/§2.3/§2.4 - one
        aggregate, not three independent lookups). Deliberately not scoped
        to any one bag's own history, unlike recent_healthy_features above -
        exclude_bag_id only prevents a bag from feeding its own reference
        point when one exists (async_new_bag has no bag yet, so passes None).
        `limit=200` is a generous cap for query cost, not a tight recency
        window - this pool is a slow-moving installation-level prior, not a
        fast-reacting per-bag one, so it doesn't need recent_healthy_features'
        own tight default.

        Includes too_fast/too_restrictive shots alongside healthy ones (only
        puck_prep_issue/invalid_measurement excluded) - see
        docs/todo/ADAPTIVE_LEARNING_PLAN.md §3 for why healthy-only would risk
        a bootstrapping deadlock here. Returns None when roast_level is None
        (nothing to bucket by) or no matching shots exist yet.
        """
        if roast_level is None:
            return None
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT s.dose_g, s.target_yield_g, s.analysis_json
                FROM shots s JOIN bags b ON s.bag_id = b.id
                WHERE b.roast_level=? AND b.id IS NOT ?
                  AND s.classification IN ('healthy', 'too_fast', 'too_restrictive')
                  AND s.analysis_json IS NOT NULL
                ORDER BY s.started_at DESC LIMIT ?
                """,
                (roast_level, exclude_bag_id, int(limit)),
            ).fetchall()
        if not rows:
            return None
        flow_rates = []
        ratios = []
        doses = []
        for row in rows:
            data = json.loads(row["analysis_json"])
            flow_rates.append(float(row["target_yield_g"]) / (float(data["t90_ms"]) / 1000.0))
            ratios.append(float(row["target_yield_g"]) / float(row["dose_g"]))
            doses.append(float(row["dose_g"]))
        return {
            "shot_count": len(rows),
            "median_flow_g_s": statistics.median(flow_rates),
            "median_ratio": statistics.median(ratios),
            "median_dose_g": statistics.median(doses),
        }

    _FLAVOR_AXIS_COLUMNS = {
        "extraction": "flavor_extraction_tag",
        "mouthfeel": "flavor_mouthfeel_tag",
    }

    def record_flavor_tag(self, shot_id: str, axis: str, tag: str) -> bool:
        """Record one axis of taste feedback for a shot (see
        expert_rules.flavor_correction in definitions.yaml). axis is
        "extraction" (sour_sharp/bitter_harsh/balanced) or "mouthfeel"
        (thin_weak/dry_astringent/balanced) - the two are independent, so
        answering one never requires or blocks answering the other.
        Returns whether a matching shot was actually found and updated -
        mirrors delete_shot's own return convention - so a stale
        notification action (its shot since deleted) can be logged instead
        of silently doing nothing."""
        column = self._FLAVOR_AXIS_COLUMNS[axis]
        with self._connect() as db:
            cursor = db.execute(f"UPDATE shots SET {column}=? WHERE id=?", (tag, shot_id))
        return cursor.rowcount > 0

    def recent_flavor_tags(self, bag_id: str, axis: str, limit: int = 5) -> list[str]:
        """Most-recent-first answered tags for one axis of a bag's shot
        history. Shots never answered on this axis are skipped entirely
        (not counted as a pattern-break) rather than treated as "balanced" -
        an unanswered notification says nothing about how the shot tasted.

        Concretely: a shot nobody responded to at all is invisible to both
        axes here, same as if it never happened. A shot answered on only
        one axis (e.g. extraction tapped, mouthfeel notification ignored)
        is included for that axis exactly like a fully-answered shot, and
        skipped for the other - answering one axis never blocks, delays, or
        counts against the other axis's own persistent-pattern check."""
        column = self._FLAVOR_AXIS_COLUMNS[axis]
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT {column} AS tag FROM shots
                WHERE bag_id=? AND {column} IS NOT NULL
                ORDER BY started_at DESC LIMIT ?
                """,
                (bag_id, int(limit)),
            ).fetchall()
        return [row["tag"] for row in rows]
