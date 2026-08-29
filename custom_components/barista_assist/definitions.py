"""Load and validate package-owned declarative definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, kw_only=True, slots=True)
class EntityDefinition:
    """One user-facing Home Assistant entity definition."""

    platform: str
    key: str
    source: str | None = None
    field: str | None = None
    translation_key: str | None = None
    token: str | None = None
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    precision: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    mode: str | None = None
    options: tuple[tuple[str, Any], ...] = ()
    action: str | None = None
    attributes: tuple[str, ...] = ()
    requires_bag: bool = False
    requires_scale: bool = False

    def option_labels(self) -> list[str]:
        return [label for label, _value in self.options]

    def option_value(self, label: str) -> Any:
        for option_label, value in self.options:
            if option_label == label:
                return value
        raise ValueError(f"Unknown option {label!r} for {self.key}")

    def option_label(self, value: Any) -> str:
        for label, option_value in self.options:
            if option_value == value:
                return label
        raise ValueError(f"Unknown value {value!r} for {self.key}")


@dataclass(frozen=True, kw_only=True, slots=True)
class Definitions:
    """Validated package definitions."""

    version: int
    slots: tuple[str, ...]
    defaults: dict[str, Any]
    entities: dict[str, tuple[EntityDefinition, ...]] = field(default_factory=dict)

    def platform(self, platform: str) -> tuple[EntityDefinition, ...]:
        return self.entities.get(platform, ())

    def entity(self, platform: str, key: str) -> EntityDefinition:
        for definition in self.platform(platform):
            if definition.key == key:
                return definition
        raise KeyError(f"Unknown {platform} entity {key}")

    @property
    def dashboard_tokens(self) -> dict[str, tuple[str, str]]:
        tokens: dict[str, tuple[str, str]] = {}
        for platform, definitions in self.entities.items():
            for definition in definitions:
                if definition.token:
                    tokens[definition.token] = (platform, definition.key)
        return tokens


def _validate_number(raw: dict[str, Any], path: str) -> None:
    minimum = float(raw["min"])
    maximum = float(raw["max"])
    step = float(raw["step"])
    if minimum >= maximum:
        raise ValueError(f"{path}: min must be less than max")
    if step <= 0:
        raise ValueError(f"{path}: step must be positive")


def _parse_entity(platform: str, key: str, raw: dict[str, Any]) -> EntityDefinition:
    if platform == "number":
        _validate_number(raw, f"entities.{platform}.{key}")
    options = tuple(
        (str(item["label"]), item["value"]) for item in raw.get("options", ())
    )
    if platform == "select" and not options:
        raise ValueError(f"entities.{platform}.{key}: select must have options")
    return EntityDefinition(
        platform=platform,
        key=key,
        source=raw.get("source"),
        field=raw.get("field"),
        translation_key=raw.get("translation_key", key),
        token=raw.get("token"),
        unit=raw.get("unit"),
        device_class=raw.get("device_class"),
        state_class=raw.get("state_class"),
        precision=raw.get("precision"),
        minimum=float(raw["min"]) if "min" in raw else None,
        maximum=float(raw["max"]) if "max" in raw else None,
        step=float(raw["step"]) if "step" in raw else None,
        mode=raw.get("mode"),
        options=options,
        action=raw.get("action"),
        attributes=tuple(raw.get("attributes", ())),
        requires_bag=bool(raw.get("requires_bag", False)),
        requires_scale=bool(raw.get("requires_scale", False)),
    )


def _parse_definitions(path: Path) -> Definitions:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("definitions.yaml must contain a mapping")

    slots = tuple(raw.get("slots", ()))
    if not slots or len(slots) != len(set(slots)):
        raise ValueError("definitions.yaml slots must be unique and non-empty")

    entities: dict[str, tuple[EntityDefinition, ...]] = {}
    seen_tokens: set[str] = set()
    for platform, raw_entities in raw.get("entities", {}).items():
        parsed: list[EntityDefinition] = []
        for key, entity_raw in raw_entities.items():
            definition = _parse_entity(platform, key, entity_raw)
            if definition.token:
                if definition.token in seen_tokens:
                    raise ValueError(f"Duplicate dashboard token {definition.token}")
                seen_tokens.add(definition.token)
            parsed.append(definition)
        entities[platform] = tuple(parsed)

    defaults = raw.get("defaults", {})
    recipe = defaults.get("recipe", {})
    for required in (
        "dose_g",
        "grind",
        "target_yield_g",
        "temperature_offset_c",
        "preinfusion_s",
    ):
        if required not in recipe:
            raise ValueError(f"Missing defaults.recipe.{required}")

    # Validate defaults against matching number/select definitions.
    field_defs = {
        definition.field: definition
        for platform in ("number", "select")
        for definition in entities.get(platform, ())
        if definition.source == "bag" and definition.field
    }
    for field_name, value in recipe.items():
        definition = field_defs.get(field_name)
        if definition is None:
            continue
        if definition.platform == "number":
            assert definition.minimum is not None and definition.maximum is not None
            if not definition.minimum <= float(value) <= definition.maximum:
                raise ValueError(f"Default {field_name} is outside entity range")
        elif definition.platform == "select":
            valid = {option_value for _label, option_value in definition.options}
            if value not in valid:
                raise ValueError(f"Default {field_name} is not a valid select option")

    return Definitions(
        version=int(raw.get("version", 1)),
        slots=slots,
        defaults=defaults,
        entities=entities,
    )


_definitions_cache: dict[str, Any] = {"mtime": None, "data": None}


def load_definitions() -> Definitions:
    """Load package definitions, re-parsing whenever definitions.yaml changes
    on disk rather than only once per process - so a HACS update (or, during
    development, an edit) takes effect on the next call instead of needing a
    full Home Assistant restart."""
    path = Path(__file__).with_name("definitions.yaml")
    mtime = path.stat().st_mtime
    if _definitions_cache["mtime"] != mtime:
        _definitions_cache["data"] = _parse_definitions(path)
        _definitions_cache["mtime"] = mtime
    return _definitions_cache["data"]


load_definitions.cache_clear = lambda: _definitions_cache.update(mtime=None, data=None)
