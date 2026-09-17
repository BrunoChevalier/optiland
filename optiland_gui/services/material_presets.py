"""Bounded named view preferences; never optical payloads or Apply authority."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from optiland.materials.properties import MaterialKey, MaterialQuery

VIEWS = (
    ("n_d versus V_d", "nd-vd"),
    ("n_d versus n_F − n_C", "nd-dispersion"),
    ("P_F,d versus V_d", "partial-fd"),
    ("P_g,F versus V_d", "partial-gf"),
    ("P_d,C versus V_d", "partial-dc"),
)


@dataclass(frozen=True)
class MaterialViewPreset:
    """Exact browsing choices, ordered pin labels and the retained viewport."""

    view: str
    query: MaterialQuery
    pins: tuple[tuple[MaterialKey, str, str], ...]
    bounds: tuple[float, float, float, float]
    show_context: bool = True

    def __post_init__(self):
        try:
            pins = tuple(tuple(pin) for pin in self.pins)
            bounds = tuple(self.bounds)
        except TypeError as error:
            raise ValueError("Preset pins and bounds must be sequences") from error
        object.__setattr__(self, "pins", pins)
        object.__setattr__(self, "bounds", bounds)
        if self.view not in {view for _, view in VIEWS}:
            raise ValueError("Unknown material map view")
        if not isinstance(self.query, MaterialQuery) or self.query.catalog_keys is None:
            raise ValueError("Presets require explicit catalog choices")
        if len(self.query.catalog_keys) > 256:
            raise ValueError("Preset has too many catalog choices")
        if any(
            len(pin) != 3
            or not isinstance(pin[0], MaterialKey)
            or any(not isinstance(label, str) or len(label) > 4096 for label in pin[1:])
            for pin in self.pins
        ):
            raise ValueError("Invalid comparison pin")
        if len(self.pins) > 4 or len({pin[0] for pin in self.pins}) != len(self.pins):
            raise ValueError("Preset requires at most four unique pins")
        if len(self.bounds) != 4 or not all(
            type(value) in (int, float) and math.isfinite(value) and abs(value) < 1e100
            for value in self.bounds
        ):
            raise ValueError("Preset bounds must be four finite numbers")
        for low, high in (self.bounds[:2], self.bounds[2:]):
            if abs(high - low) <= max(abs(low), abs(high), 1.0) * 1e-12:
                raise ValueError("Preset axes must have a nonzero span")
        if type(self.show_context) is not bool:
            raise ValueError("Invalid context preference")


class MaterialPresetStore:
    """One scoped QSettings value; malformed data never broadens a query."""

    KEY = "Materials/namedViews"
    MAX_BYTES = 256 * 1024

    def __init__(self, settings):
        self.settings = settings

    def _read(self):
        raw = self.settings.value(self.KEY, "{}")
        if type(raw) is not str or len(raw.encode("utf-8")) > self.MAX_BYTES:
            raise ValueError("Saved material views exceed the preference limit")
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError) as error:
            raise ValueError("Saved material views are not valid JSON") from error
        if not isinstance(data, dict) or len(data) > 32:
            raise ValueError("Saved material views require at most 32 names")
        for name in data:
            self._name(name)
        return data

    @staticmethod
    def _name(name):
        if type(name) is not str or not name.strip() or len(name) > 128:
            raise ValueError("Use a view name of 1–128 characters")

    def names(self):
        return tuple(sorted(self._read(), key=str.casefold))

    def load(self, name):
        data = self._read()
        if name not in data:
            raise ValueError("Choose a saved material view")
        try:
            value = data[name]
            return MaterialViewPreset(
                view=value["view"],
                query=MaterialQuery(**value["query"]),
                pins=tuple(
                    (MaterialKey(**key), label, source)
                    for key, label, source in value["pins"]
                ),
                bounds=tuple(value["bounds"]),
                show_context=value["show_context"],
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            RecursionError,
        ) as error:
            raise ValueError("Saved material view has invalid settings") from error

    def save(self, name, preset):
        self._name(name)
        if not isinstance(preset, MaterialViewPreset):
            raise ValueError("A material view preset is required")
        data = self._read()
        if name not in data and len(data) >= 32:
            raise ValueError("Remove a saved view before adding more than 32")
        data[name] = asdict(preset)
        self._write(data)

    def remove(self, name):
        data = self._read()
        if name not in data:
            raise ValueError("Choose a saved material view")
        del data[name]
        self._write(data)

    def _write(self, data):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(raw.encode("utf-8")) > self.MAX_BYTES:
            raise ValueError("Saved material views exceed the preference limit")
        self.settings.setValue(self.KEY, raw)
