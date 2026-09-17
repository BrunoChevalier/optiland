"""Detached glass-map coordinates, spectral comparisons and geometric neighbors.

Reference coordinates use cached native d/F/C/g indices. Spectra restore only
the requested owned records and never resolve a display name or catalog file.
Unknown support remains distinct from known validity; table clamping does not
extend a comparison's declared domain. No GUI framework is imported here.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Literal

import optiland.backend as be
from optiland.materials.data import DataMaterial
from optiland.materials.definition import FormulaDispersion, TransmissionTable
from optiland.materials.properties import (
    MaterialKey,
    MaterialMatch,
    MaterialSnapshot,
    MaterialSummary,
    PropertyValue,
    ReferenceConditions,
    SourceStamp,
    reconstruct_material,
)

if TYPE_CHECKING:
    from collections.abc import Callable

MapView = Literal["nd-vd", "nd-dispersion", "partial-fd", "partial-gf", "partial-dc"]
Quantity = Literal["n", "transmission"]
MAX_PINS = 4
MAX_SAMPLES = 2001
MAX_DETAIL_FIELDS = 64
MAX_DETAIL_TEXT = 8192

_VIEWS = {
    "nd-vd": ("V_d = (n_d − 1)/(n_F − n_C)", "n_d", True),
    "nd-dispersion": ("n_F − n_C", "n_d", False),
    "partial-fd": ("V_d", "P_F,d = (n_F − n_d)/(n_F − n_C)", True),
    "partial-gf": ("V_d", "P_g,F = (n_g − n_F)/(n_F − n_C)", True),
    "partial-dc": ("V_d", "P_d,C = (n_d − n_C)/(n_F − n_C)", True),
}


@dataclass(frozen=True)
class MapCoordinate:
    """One qualified point, including explicit unavailable axis values."""

    key: MaterialKey
    x: PropertyValue
    y: PropertyValue


@dataclass(frozen=True)
class MapViewResult:
    """Coordinates and axis conventions tied to exact source generations."""

    stamps: tuple[SourceStamp, ...]
    view: MapView
    xlabel: str
    ylabel: str
    descending_x: bool
    coordinates: tuple[MapCoordinate, ...]


@dataclass(frozen=True)
class SpectralRequest:
    """A bounded ordered comparison; ``None`` requests a common known interval.

    Explicit intervals permit unknown-domain index models with a warning.
    Transmission always requires actual owned extinction/transmission data.
    Wavelengths are in micrometers; thickness is in millimeters.
    """

    keys: tuple[MaterialKey, ...]
    quantity: Quantity = "n"
    interval_um: tuple[float, float] | None = None
    sample_count: int = 201
    thickness_mm: float = 10.0

    def __post_init__(self):
        object.__setattr__(self, "keys", tuple(self.keys))
        if (
            len(self.keys) > MAX_PINS
            or any(not isinstance(key, MaterialKey) for key in self.keys)
            or len(set(self.keys)) != len(self.keys)
        ):
            raise ValueError(
                "Compare requires at most four distinct material identities"
            )
        if self.quantity not in {"n", "transmission"}:
            raise ValueError("Unknown comparison quantity")
        if (
            type(self.sample_count) is not int
            or not 2 <= self.sample_count <= MAX_SAMPLES
        ):
            raise ValueError(f"Sample count must be between 2 and {MAX_SAMPLES}")
        if not math.isfinite(self.thickness_mm) or self.thickness_mm < 0:
            raise ValueError("Thickness must be finite and nonnegative in mm")
        object.__setattr__(self, "thickness_mm", float(self.thickness_mm))
        if self.interval_um is not None:
            limits = tuple(self.interval_um)
            if (
                len(limits) != 2
                or not all(math.isfinite(value) and value > 0 for value in limits)
                or limits[0] >= limits[1]
            ):
                raise ValueError(
                    "Comparison interval must be finite, positive and increasing"
                )
            object.__setattr__(self, "interval_um", tuple(float(v) for v in limits))


@dataclass(frozen=True)
class MaterialDetails:
    """Bounded plain-text native model fields, with attributed raw metadata."""

    model: str
    fields: tuple[tuple[str, str], ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class SpectralSeries:
    """A pin's shared plot/table values; unavailable pins retain their identity."""

    key: MaterialKey
    display_name: str
    conditions: ReferenceConditions
    declared_range_um: tuple[float, float] | None
    values: tuple[PropertyValue, ...]
    details: MaterialDetails | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SpectralResult:
    """Immutable shared wavelength grid and ordered spectra without owned payloads."""

    stamps: tuple[SourceStamp, ...]
    request: SpectralRequest
    interval_um: tuple[float, float] | None
    wavelengths_um: tuple[float, ...]
    series: tuple[SpectralSeries, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class NearbyResult:
    """Geometric distances with explicit scales; not optical performance or merit."""

    stamps: tuple[SourceStamp, ...]
    target_key: MaterialKey
    nd_scale: float
    vd_scale: float
    matches: tuple[MaterialMatch, ...]
    excluded_counts: tuple[tuple[str, int], ...]


def _check(checkpoint):
    if checkpoint is not None:
        checkpoint()


def _unavailable(reason, status="missing"):
    return PropertyValue(None, status, reason=str(reason)[:512])


def _difference(first, second):
    for value in (first, second):
        if value.status != "available":
            return _unavailable(
                value.reason or "Reference index is unavailable", value.status
            )
    value = first.value - second.value
    return (
        PropertyValue(value, "available")
        if math.isfinite(value)
        else _unavailable("Index difference is not finite", "invalid")
    )


def _partial(material, first, second):
    numerator = _difference(first, second)
    denominator = _difference(material.n_f, material.n_c)
    for value in (numerator, denominator):
        if value.status != "available":
            return value
    if denominator.value == 0:
        return _unavailable(
            "Relative partial dispersion is undefined at zero dispersion"
        )
    value = numerator.value / denominator.value
    return (
        PropertyValue(value, "available")
        if math.isfinite(value)
        else _unavailable("Relative partial dispersion is not finite", "invalid")
    )


def map_coordinates(
    snapshot: MaterialSnapshot,
    view: MapView,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> MapViewResult:
    """Recompute axes using cached scalars, without optical evaluation or payloads."""
    if view not in _VIEWS:
        raise ValueError("Unknown material map view")
    coordinates = []
    for record in snapshot.materials:
        _check(checkpoint)
        x, y = record.vd, record.nd
        if view == "nd-dispersion":
            x = _difference(record.n_f, record.n_c)
        elif view == "partial-fd":
            y = _partial(record, record.n_f, record.nd)
        elif view == "partial-gf":
            y = _partial(record, record.n_g, record.n_f)
        elif view == "partial-dc":
            y = _partial(record, record.nd, record.n_c)
        coordinates.append(MapCoordinate(record.key, x, y))
    _check(checkpoint)
    return MapViewResult(snapshot.stamps, view, *_VIEWS[view], tuple(coordinates))


def _details(material):
    rows, notes = [], []
    if isinstance(material, DataMaterial):
        definition = material.definition
        dispersion = definition.dispersion
        rows.extend(
            (
                ("Native bounds policy", material.bounds),
                ("Declared n support (µm)", str(material.spectral_range("n"))),
                ("Declared k/T support (µm)", str(material.spectral_range("k"))),
            )
        )
        if isinstance(dispersion, FormulaDispersion):
            model = dispersion.formula
            rows.append(
                ("Formula convention", "refractiveindex.info; wavelength λ in µm")
            )
            rows.extend(
                (f"Coefficient c{i}", repr(value))
                for i, value in enumerate(dispersion.coefficients[:MAX_DETAIL_FIELDS])
            )
            if len(dispersion.coefficients) > MAX_DETAIL_FIELDS:
                notes.append(
                    "The coefficient list exceeds the presentation field limit."
                )
        else:
            model = "Tabulated refractive index"
            rows.append(("Index samples", str(len(dispersion.indices))))
            rows.extend(
                (f"n at {wave!r} µm", repr(value))
                for wave, value in zip(
                    dispersion.wavelengths_um[:12], dispersion.indices[:12], strict=True
                )
            )
            if len(dispersion.indices) > 12:
                notes.append("Only the first 12 native index samples are shown.")
        table = definition.extinction
        if table is None:
            rows.append(
                (
                    "Extinction data",
                    "Unavailable; native zero-k fallback is not measured data",
                )
            )
        else:
            rows.append(("Extinction definition", type(table).__name__))
            rows.append(("Extinction/transmission samples", str(len(table.values))))
            if isinstance(table, TransmissionTable):
                rows.extend(
                    (
                        ("Source thickness (mm)", repr(table.thickness_mm)),
                        ("Transmission interpolation", table.interpolation),
                    )
                )
        if definition.thermal is not None:
            rows.append(("Thermal model", definition.thermal.kind))
            rows.append(
                (
                    "Thermal reference temperature (°C)",
                    repr(definition.thermal.reference_temperature_c),
                )
            )
        metadata = material.metadata
        if metadata:
            rows.append(
                (
                    "Raw source metadata (descriptive)",
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                )
            )
    else:
        model = type(material).__name__
        rows.append(
            (
                "Native model parameters",
                json.dumps(material.to_dict(), ensure_ascii=False, sort_keys=True),
            )
        )
        notes.append(
            "Analytic model parameters do not establish a measured validity range."
        )
    bounded, remaining = [], MAX_DETAIL_TEXT
    for label, value in rows[:MAX_DETAIL_FIELDS]:
        text = str(value)
        shown = text[:remaining]
        bounded.append((label, shown))
        remaining -= len(shown)
        if len(shown) < len(text) or remaining == 0:
            notes.append("Native details are truncated at the presentation text limit.")
            break
    if len(rows) > MAX_DETAIL_FIELDS:
        notes.append(
            f"Only the first {MAX_DETAIL_FIELDS} native detail fields are shown."
        )
    return MaterialDetails(model, tuple(bounded), tuple(notes))


def material_details(summary: MaterialSummary) -> MaterialDetails:
    """Parse bounded exact owned-definition details without file/catalog lookup."""
    return _details(reconstruct_material(summary))


def _scalar(material, wavelength, request):
    try:
        raw = (
            material.n(wavelength)
            if request.quantity == "n"
            else material.internal_transmittance(wavelength, request.thickness_mm)
        )
        if isinstance(raw, complex):
            return _unavailable(
                "The native model did not return a real scalar", "unsupported"
            )
        value = be.to_numpy(raw)
        if value.size != 1 or value.dtype.kind == "c":
            return _unavailable(
                "The native model did not return a real scalar", "unsupported"
            )
        scalar = float(value.reshape(-1)[0])
        if not math.isfinite(scalar) or (
            scalar <= 0 if request.quantity == "n" else not 0 <= scalar <= 1
        ):
            return _unavailable(
                "The native model returned an invalid physical value", "invalid"
            )
        return PropertyValue(scalar, "available")
    except NotImplementedError as error:
        return _unavailable(
            str(error) or "The native model cannot evaluate this quantity",
            "unsupported",
        )
    except (ValueError, TypeError, ArithmeticError) as error:
        return _unavailable(
            str(error) or "Native spectral evaluation failed", "invalid"
        )


def compare_materials(
    snapshot: MaterialSnapshot,
    request: SpectralRequest,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> SpectralResult:
    """Evaluate one quantity for at most four pins on one shared bounded grid.

    Default intervals intersect the known domains of eligible records, excluding
    unknown support. Explicit intervals expose out-of-domain gaps and permit
    unknown-domain index models with a visible warning. Transmission uses the
    native internal-transmittance function, including its interpolation-before-
    thickness-scaling behavior; absent extinction is never promoted to 100%.
    """
    records = {record.key: record for record in snapshot.materials}
    prepared, known = [], []
    for key in request.keys:
        _check(checkpoint)
        record = records.get(key)
        material, support, details, unavailable = None, None, None, None
        warnings = []
        if record is None:
            unavailable = _unavailable("This pinned source identity is unavailable")
        else:
            try:
                material = reconstruct_material(record)
                details = _details(material)
                if request.quantity == "transmission" and (
                    not isinstance(material, DataMaterial)
                    or material.definition.extinction is None
                ):
                    unavailable = _unavailable(
                        "Internal transmission requires actual owned extinction "
                        "or transmission data"
                    )
                else:
                    support = material.spectral_range(
                        "n" if request.quantity == "n" else "k"
                    )
                    if support is None:
                        if request.interval_um is None:
                            unavailable = _unavailable(
                                "Declared support is unknown; enter an explicit "
                                "wavelength interval"
                            )
                        else:
                            warnings.append(
                                "Declared validity is unknown; values are native "
                                "model evaluations within the requested interval."
                            )
                    else:
                        known.append(support)
            except (
                ValueError,
                TypeError,
                NotImplementedError,
                ArithmeticError,
            ) as error:
                unavailable = _unavailable(
                    str(error) or "Owned material cannot be reconstructed",
                    "unsupported",
                )
        prepared.append(
            (key, record, material, support, details, unavailable, warnings)
        )
    interval, notes = request.interval_um, []
    if request.quantity == "transmission":
        notes.append(
            "Internal transmission excludes interface and scattering losses; "
            "thickness is in mm."
        )
    if interval is None:
        if known:
            lower, upper = (
                max(value[0] for value in known),
                min(value[1] for value in known),
            )
            if lower < upper:
                interval = (lower, upper)
        if interval is None:
            notes.append(
                "No common declared wavelength interval is available; "
                "enter an explicit bounded interval."
            )
    waves = _wavelength_grid(interval, request.sample_count) if interval else ()
    series = []
    for key, record, material, support, details, unavailable, warnings in prepared:
        _check(checkpoint)
        values = []
        if unavailable is not None:
            warnings.append(unavailable.reason)
        for wavelength in waves:
            _check(checkpoint)
            if unavailable is not None:
                value = unavailable
            elif support is not None and not support[0] <= wavelength <= support[1]:
                value = _unavailable(
                    "Wavelength is outside declared support", "unsupported"
                )
            else:
                value = _scalar(material, wavelength, request)
            values.append(value)
        series.append(
            SpectralSeries(
                key,
                record.display_name if record is not None else key.record_id,
                record.conditions if record is not None else ReferenceConditions(),
                support,
                tuple(values),
                details,
                tuple(warnings),
            )
        )
    _check(checkpoint)
    return SpectralResult(
        snapshot.stamps, request, interval, waves, tuple(series), tuple(notes)
    )


def _wavelength_grid(interval, count):
    # Decimal input spellings prevent ordinary nodes such as 0.7 µm becoming
    # 0.7000000000000001 and falsely exceeding a native table endpoint. Round
    # each node to binary float once; do not relax the declared support checks.
    with localcontext() as context:
        context.prec = 34
        lower, upper = (Decimal(str(value)) for value in interval)
        step = (upper - lower) / (count - 1)
        return (
            float(lower),
            *(float(lower + step * i) for i in range(1, count - 1)),
            float(upper),
        )


def nearby_materials(
    snapshot: MaterialSnapshot,
    target_key: MaterialKey,
    *,
    nd_scale: float,
    vd_scale: float,
    limit: int = 10,
    checkpoint: Callable[[], None] | None = None,
) -> NearbyResult:
    """Rank geometric neighbors using explicit positive n_d/V_d scales.

    Signed deltas are candidate minus target; ties use qualified identity.
    The target is excluded. Proximity is never an optical-equivalence claim.
    """
    if any(not math.isfinite(scale) or scale <= 0 for scale in (nd_scale, vd_scale)):
        raise ValueError("Nearby scales must be finite and positive")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Nearby limit must be between 1 and 100")
    target = next(
        (record for record in snapshot.materials if record.key == target_key), None
    )
    if target is None or any(
        value.status != "available" for value in (target.nd, target.vd)
    ):
        raise ValueError("Nearby target requires available n_d and V_d coordinates")
    matches, excluded = [], {}
    for record in snapshot.materials:
        _check(checkpoint)
        if record.key == target_key:
            continue
        reason = None
        if record.nd.status != "available" or record.vd.status != "available":
            reason = "unavailable_coordinates"
        else:
            delta_n, delta_v = (
                record.nd.value - target.nd.value,
                record.vd.value - target.vd.value,
            )
            distance = math.hypot(delta_n / nd_scale, delta_v / vd_scale)
            if not math.isfinite(distance):
                reason = "nonfinite_distance"
            else:
                matches.append(MaterialMatch(record.key, delta_n, delta_v, distance))
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
    _check(checkpoint)
    return NearbyResult(
        snapshot.stamps,
        target_key,
        float(nd_scale),
        float(vd_scale),
        tuple(sorted(matches, key=lambda match: (match.distance, match.key))[:limit]),
        tuple(sorted(excluded.items())),
    )
