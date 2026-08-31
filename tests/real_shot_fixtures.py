"""Loads real, hand-annotated shot exports from tests/fixtures/real_shots/
for regression-testing flow_analysis.analyze_shot against actual recorded
data (as opposed to the synthetic curves in test_flow_analysis.py).

Each fixture is the raw text pasted from the "Copy all shot data" export
(see storage.py's export_shots_text, whose format this mirrors), wrapped in
[SHOT]/[END_SHOT] markers, with a leading '#'-prefixed comment line holding
the human's own judgment of the shot (e.g. "Good shot", "channeling
suspected") - this is what each test's assertions are checked against.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ha_stubs

storage = ha_stubs.import_barista_module("storage")
ShotSample = storage.ShotSample

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_shots"


@dataclass(slots=True)
class RealShot:
    """One parsed real-shot fixture."""

    comment: str
    fields: dict[str, str]
    samples: list[ShotSample]

    @property
    def target_yield_g(self) -> float:
        return float(self.fields["target_yield_g"])

    @property
    def preinfusion_s(self) -> float:
        """The effective pre-infusion duration analyze_shot should be called
        with - storage.py's export_shots_text always logs a shot's own
        `preinfusion_s=` as the true effective value used for that shot (the
        bag's recipe value when Adapt PI was on, or the machine's own
        configured pre-infusion when it was off). Fixtures captured before
        that fix have had this field corrected to the true value by hand, so
        every fixture's `preinfusion_s=` can be trusted at face value."""
        return float(self.fields["preinfusion_s"])

    @property
    def recorded_classification(self) -> str:
        return self.fields["classification"]

    @property
    def recorded_channeling_suspicion(self) -> float | None:
        raw = self.fields.get("channeling_suspicion", "")
        return float(raw) if raw else None


def load_real_shot(name: str) -> RealShot:
    """Parse tests/fixtures/real_shots/{name}.txt."""
    text = (FIXTURES_DIR / f"{name}.txt").read_text(encoding="utf-8")
    lines = text.splitlines()

    comment = " ".join(line[1:].strip() for line in lines if line.startswith("#")).strip()

    try:
        shot_marker = lines.index("[SHOT]")
    except ValueError as err:
        raise ValueError(f"{name}: missing [SHOT] marker") from err
    start = shot_marker + 1
    end = lines.index("[END_SHOT]") if "[END_SHOT]" in lines else len(lines)
    body = lines[start:end]

    fields: dict[str, str] = {}
    header_index = None
    # A fixture that predates a since-added export field (e.g. adapt_pi) can
    # still annotate it here by hand - any key=value line works, whether or
    # not storage.py's export_shots_text actually emitted that key.
    for i, line in enumerate(body):
        if line.startswith("seq\t"):
            header_index = i
            break
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key] = value

    if header_index is None:
        raise ValueError(f"{name}: missing sample table header")

    samples: list[ShotSample] = []
    for line in body[header_index + 1 :]:
        if not line.strip():
            continue
        seq, elapsed_ms, scale_ms, weight_g, flow_g_s, battery_percent, _post_stop = line.split("\t")
        samples.append(
            ShotSample(
                seq=int(seq),
                elapsed_ms=int(elapsed_ms),
                scale_ms=int(scale_ms),
                weight_g=float(weight_g),
                flow_g_s=float(flow_g_s),
                battery_percent=int(battery_percent),
            )
        )

    return RealShot(comment=comment, fields=fields, samples=samples)
