"""Exact, portable material pools for categorical optimization.

Labels and reference coordinates are descriptive. Only a source-qualified key
resolves a candidate, and every assignment restores its owned native definition.
No registry aliases, catalog lookups or approximate dispersion fits are used.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import optiland.backend as be
from optiland.materials import DataMaterial, IdealMaterial
from optiland.materials.portability import capture_material, restore_material
from optiland.materials.properties import (
    MaterialKey,
    MaterialSummary,
    PropertyValue,
    ReferenceConditions,
    SourceStamp,
    describe_material,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from optiland.materials.base import BaseMaterial


def _check(checkpoint):
    if checkpoint is not None:
        checkpoint()


def _payload(value):
    if not isinstance(value, bytes | bytearray) or not value:
        raise ValueError("A candidate requires nonempty native material JSON bytes")
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("A candidate requires a native material JSON object")
    # Normalize whitespace/key order without evaluating optical data. The strict
    # encoder also rejects JSON's nonstandard NaN/Infinity spellings.
    return _native_json(data)


def _native_json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True)
class MaterialCandidate:
    """One exact candidate with computed native d/F/C coordinates.

    Construction validates metadata and normalizes finite native JSON structurally
    without optical evaluation. Call :func:`prepare_material_candidate`
    to evaluate a source record; :meth:`validate` repeats that gate for loaded
    pools. ``owned_data`` is canonical native JSON, never executable code.
    """

    key: MaterialKey
    stamp: SourceStamp
    owned_data: bytes
    nd: float
    vd: float
    display_name: str
    conditions: ReferenceConditions

    def __post_init__(self):
        if not isinstance(self.key, MaterialKey) or not isinstance(
            self.stamp, SourceStamp
        ):
            raise ValueError("A candidate requires an exact key and source stamp")
        if self.key.source_id != self.stamp.source_id:
            raise ValueError("Candidate key and source generation disagree")
        if not isinstance(self.display_name, str) or not isinstance(
            self.conditions, ReferenceConditions
        ):
            raise ValueError("Candidate label and native conditions are invalid")
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                for value in (self.nd, self.vd)
            )
            or self.nd <= 0
        ):
            raise ValueError("Candidate n_d/V_d must be finite with positive n_d")
        object.__setattr__(self, "owned_data", _payload(self.owned_data))
        object.__setattr__(self, "nd", float(self.nd))
        object.__setattr__(self, "vd", float(self.vd))

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the exact native bytes, independent of the display label."""
        return hashlib.sha256(self.owned_data).hexdigest()

    def restore(self) -> BaseMaterial:
        """Return a fresh native material without consulting any registry/file."""
        material = restore_material(self.owned_data)
        if capture_material(material) != self.owned_data:
            raise ValueError("Candidate payload is not canonical native material data")
        return material

    def validate(self, wavelengths_um=(), *, checkpoint=None) -> tuple[str, ...]:
        """Verify exact native restoration, model coordinates and spectral support."""
        _check(checkpoint)
        material = self.restore()
        summary = describe_material(material, self.key)
        _check(checkpoint)
        for name in ("nd", "vd"):
            value = getattr(summary, name)
            if value.status != "available" or not math.isclose(
                value.value, getattr(self, name), rel_tol=1e-10, abs_tol=1e-12
            ):
                raise ValueError(f"Candidate {self.key!r} has incorrect model {name}")
        if summary.conditions != self.conditions:
            raise ValueError("Candidate reference conditions differ from its model")
        return validate_material_spectrum(
            material, wavelengths_um, checkpoint=checkpoint
        )

    def to_dict(self) -> dict:
        """Return plain public data suitable for a strict finite JSON manifest."""
        return {
            "key": asdict(self.key),
            "stamp": asdict(self.stamp),
            "material": json.loads(self.owned_data),
            "nd": self.nd,
            "vd": self.vd,
            "display_name": self.display_name,
            "conditions": asdict(self.conditions),
        }

    @classmethod
    def from_dict(cls, value: dict) -> MaterialCandidate:
        """Restore the public record; numerical validation still precedes a run."""
        fields = {"key", "stamp", "material", "nd", "vd", "display_name", "conditions"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Invalid material candidate record")
        try:
            return cls(
                MaterialKey(**value["key"]),
                SourceStamp(**value["stamp"]),
                _native_json(value["material"]),
                value["nd"],
                value["vd"],
                value["display_name"],
                ReferenceConditions(**value["conditions"]),
            )
        except (TypeError, KeyError) as error:
            raise ValueError("Invalid material candidate record") from error


@dataclass(frozen=True)
class MaterialPool:
    """A frozen nonempty selection of distinct source-qualified candidates."""

    candidates: tuple[MaterialCandidate, ...]

    def __post_init__(self):
        candidates = tuple(self.candidates)
        if not candidates or any(
            not isinstance(c, MaterialCandidate) for c in candidates
        ):
            raise ValueError("A material pool requires at least one exact candidate")
        keys, stamps = set(), {}
        for candidate in candidates:
            if candidate.key in keys:
                raise ValueError("A material pool cannot contain duplicate exact keys")
            keys.add(candidate.key)
            previous = stamps.setdefault(candidate.stamp.source_id, candidate.stamp)
            if previous != candidate.stamp:
                raise ValueError(
                    "A material pool contains conflicting source generations"
                )
        object.__setattr__(self, "candidates", candidates)

    def resolve(self, key: MaterialKey) -> MaterialCandidate:
        """Resolve one exact key, never a label or a nearest-coordinate substitute."""
        for candidate in self.candidates:
            if candidate.key == key:
                return candidate
        raise ValueError(f"Material key {key!r} is outside the frozen pool")

    def to_dict(self) -> dict:
        """Return the supported standalone public pool representation."""
        return {"candidates": [candidate.to_dict() for candidate in self.candidates]}

    @classmethod
    def from_dict(cls, value: dict) -> MaterialPool:
        """Restore a pool without loading files or registering synthetic names."""
        if (
            not isinstance(value, dict)
            or set(value) != {"candidates"}
            or not isinstance(value["candidates"], list | tuple)
        ):
            raise ValueError("Invalid material pool record")
        return cls(tuple(MaterialCandidate.from_dict(c) for c in value["candidates"]))


@dataclass(frozen=True)
class MaterialReference:
    """An inspectable baseline which supplies no candidate membership authority."""

    owned_data: bytes
    display_name: str
    nd: PropertyValue
    vd: PropertyValue
    conditions: ReferenceConditions

    def __post_init__(self):
        object.__setattr__(self, "owned_data", _payload(self.owned_data))

    @classmethod
    def capture(cls, material: BaseMaterial) -> MaterialReference:
        """Capture native reference state without assigning a pool identity."""
        payload = capture_material(material)
        summary = describe_material(restore_material(payload), MaterialKey("", "", ""))
        return cls(
            payload, material.display_name, summary.nd, summary.vd, summary.conditions
        )


def validate_material_spectrum(
    material: BaseMaterial,
    wavelengths_um,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[str, ...]:
    """Evaluate positive n and nonnegative k within known native support.

    Unknown extinction retains the native zero fallback with an explicit warning.
    Known support is respected even for tables configured to clamp. An empty
    wavelength sequence requests reference-coordinate validation only.
    """
    wavelengths = tuple(wavelengths_um)
    if any(
        isinstance(w, bool)
        or not isinstance(w, int | float)
        or not math.isfinite(w)
        or w <= 0
        for w in wavelengths
    ):
        raise ValueError("Material validation needs finite positive wavelengths")
    warnings = []
    for quantity in ("n", "k"):
        _check(checkpoint)
        support = material.spectral_range(quantity)
        if support is not None and any(
            not support[0] <= w <= support[1] for w in wavelengths
        ):
            raise ValueError(
                f"A configured wavelength lies outside known {quantity} "
                f"support {support} µm"
            )
        if support is None and wavelengths:
            if (
                quantity == "k"
                and isinstance(material, DataMaterial)
                and material.definition.extinction is None
            ):
                warnings.append(
                    "Extinction data are unknown; the native zero-k fallback was used."
                )
            elif isinstance(material, IdealMaterial):
                warnings.append(f"{quantity} support is analytic.")
            else:
                warnings.append(
                    f"{quantity} support is unknown; "
                    "configured wavelengths were evaluated."
                )
        for wavelength in wavelengths:
            _check(checkpoint)
            values = be.to_numpy(getattr(material, quantity)(wavelength)).reshape(-1)
            if (
                len(values) != 1
                or not math.isfinite(float(values[0]))
                or (values[0] <= 0 if quantity == "n" else values[0] < 0)
            ):
                raise ValueError(f"Material {quantity} is invalid at {wavelength:g} µm")
            _check(checkpoint)
    return tuple(warnings)


def prepare_material_candidate(
    summary: MaterialSummary,
    stamp: SourceStamp,
    *,
    wavelengths_um: tuple[float, ...],
    checkpoint: Callable[[], None] | None = None,
) -> MaterialCandidate:
    """Preflight a source summary through the exact native optimizer resolver."""
    if summary.owned_data is None:
        raise ValueError("This material has no portable owned definition")
    for name in ("nd", "vd"):
        if getattr(summary, name).status != "available":
            raise ValueError(f"This material has no computed {name} coordinate")
    candidate = MaterialCandidate(
        summary.key,
        stamp,
        summary.owned_data,
        summary.nd.value,
        summary.vd.value,
        summary.display_name,
        summary.conditions,
    )
    candidate.validate(wavelengths_um, checkpoint=checkpoint)
    return candidate
