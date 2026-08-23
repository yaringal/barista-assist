"""SQLite persistence for bags, shots and raw scale samples."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

LATEST_SCHEMA_VERSION = 2
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
                    # Preserve the old integration-wide PI value on existing bags.
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
        )
        with self._connect() as db:
            db.execute("UPDATE bags SET active=0 WHERE slot=? AND active=1", (slot,))
            db.execute(
                """
                INSERT INTO bags(
                    id, slot, coffee_name, roaster, roast_date, opened_at,
                    starting_mass_g, dose_g, grind, target_yield_g,
                    temperature_offset_c, preinfusion_s, active
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)
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

    def create_shot(self, *, bag: Bag, started_at: str, stop_compensation_g: float) -> str:
        shot_id = uuid4().hex
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO shots(
                    id, bag_id, started_at, dose_g, grind, target_yield_g,
                    temperature_offset_c, preinfusion_s, stop_compensation_g, status
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    shot_id,
                    bag.id,
                    started_at,
                    bag.dose_g,
                    bag.grind,
                    bag.target_yield_g,
                    bag.temperature_offset_c,
                    bag.preinfusion_s,
                    float(stop_compensation_g),
                    "running",
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
    ) -> None:
        sample_list = list(samples)
        with self._connect() as db:
            db.execute(
                """
                UPDATE shots
                SET ended_at=?, actual_yield_g=?, status=?,
                    stop_command_elapsed_ms=?, sample_count=?
                WHERE id=?
                """,
                (
                    ended_at,
                    actual_yield_g,
                    status,
                    stop_command_elapsed_ms,
                    len(sample_list),
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
                SELECT s.*, b.coffee_name, b.slot
                FROM shots s JOIN bags b ON b.id=s.bag_id
                ORDER BY s.started_at DESC LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def recent_shots(self, limit: int = 10) -> list[dict[str, Any]]:
        """Retained as a small repository API for future diagnostics/history UI."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT s.*, b.coffee_name, b.slot
                FROM shots s JOIN bags b ON b.id=s.bag_id
                ORDER BY s.started_at DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def export_shots_text(self) -> str:
        """Export every stored shot and raw scale sample as paste-friendly text."""
        with self._connect() as db:
            shots = db.execute(
                """
                SELECT
                    s.*,
                    b.coffee_name, b.slot, b.roaster, b.roast_date,
                    b.opened_at, b.starting_mass_g, b.preinfusion_s AS bag_preinfusion_s
                FROM shots s
                JOIN bags b ON b.id=s.bag_id
                ORDER BY s.started_at ASC
                """
            ).fetchall()

            sample_rows = db.execute(
                """
                SELECT shot_id, seq, elapsed_ms, scale_ms, weight_g, flow_g_s, battery_percent
                FROM samples
                ORDER BY shot_id ASC, seq ASC
                """
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
                f"bag_opened_at={clean(shot['opened_at'])}",
                f"started_at={clean(shot['started_at'])}",
                f"ended_at={clean(shot['ended_at'])}",
                f"status={clean(shot['status'])}",
                f"dose_g={shot['dose_g']}",
                f"grind={shot['grind']}",
                f"target_yield_g={shot['target_yield_g']}",
                f"actual_yield_g={shot['actual_yield_g'] if shot['actual_yield_g'] is not None else ''}",
                f"temperature_offset_c={shot['temperature_offset_c']}",
                f"preinfusion_s={shot['bag_preinfusion_s']}",
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
