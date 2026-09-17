"""Immutable material summaries and queries, independent of GUI frameworks.

Reference-line coordinates use d/F/C wavelengths in micrometers. They describe
the material's native reference conditions; this module does not convert between
absolute and air-relative index conventions. Declared support is checked before
evaluation even when a material's table policy permits clamping.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import optiland.backend as be
from optiland.materials.base import BaseMaterial
from optiland.materials.data import DataMaterial
from optiland.materials.definition import TransmissionTable
from optiland.materials.material_file import MaterialFile
from optiland.propagation.homogeneous import HomogeneousPropagation

if TYPE_CHECKING:
    from collections.abc import Iterable

D_LINE_UM = 0.5875618
F_LINE_UM = 0.4861327
C_LINE_UM = 0.6562725
PropertyStatus = Literal["available", "missing", "unsupported", "invalid"]


@dataclass(frozen=True, order=True)
class MaterialKey:
    """Exact source-qualified identity; labels are never resolution keys."""

    source_id: str
    catalog: str
    record_id: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str)
            for value in (self.source_id, self.catalog, self.record_id)
        ):
            raise ValueError("Material identity components must be strings")


@dataclass(frozen=True)
class SourceStamp:
    """A source identity and its caller-owned immutable generation token."""

    source_id: str
    generation: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not isinstance(self.generation, str):
            raise ValueError("Source identity and generation must be strings")


@dataclass(frozen=True)
class PropertyValue:
    """A scalar coordinate or an explicit reason it cannot be supplied."""

    value: float | None
    status: PropertyStatus
    origin: str = "computed"
    unit: str = "1"
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"available", "missing", "unsupported", "invalid"}:
            raise ValueError("Unknown material property status")
        if self.status == "available":
            if self.value is None or not math.isfinite(self.value):
                raise ValueError("Available properties require a finite value")
            object.__setattr__(self, "value", float(self.value))
        elif self.value is not None:
            raise ValueError("Unavailable properties must not contain a value")


@dataclass(frozen=True)
class MaterialSummary:
    """Reference coordinates, support and optional self-contained owned data.

    ``None`` availability means the adapter cannot establish measured-data
    presence. Missing support does not mean an unlimited measurement range.
    ``owned_data`` is native JSON bytes, never a pickle or file lookup record.
    """

    key: MaterialKey
    display_name: str
    nd: PropertyValue
    vd: PropertyValue
    n_range_um: tuple[float, float] | None
    k_range_um: tuple[float, float] | None
    bounds: str | None
    has_extinction_data: bool | None
    has_transmission_data: bool | None
    owned_data: bytes | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "n_range_um", _range(self.n_range_um))
        object.__setattr__(self, "k_range_um", _range(self.k_range_um))
        if self.owned_data is not None:
            if not isinstance(self.owned_data, bytes | bytearray):
                raise ValueError("Owned material data must be native JSON bytes")
            object.__setattr__(self, "owned_data", bytes(self.owned_data))


@dataclass(frozen=True)
class MaterialSnapshot:
    """An immutable set of distinct records and the exact sources they describe."""

    stamps: tuple[SourceStamp, ...]
    materials: tuple[MaterialSummary, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "stamps", tuple(self.stamps))
        object.__setattr__(self, "materials", tuple(self.materials))
        source_ids = [stamp.source_id for stamp in self.stamps]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("A snapshot requires one generation per source")
        keys = [material.key for material in self.materials]
        if len(keys) != len(set(keys)):
            raise ValueError("A snapshot cannot contain duplicate material keys")
        if any(key.source_id not in source_ids for key in keys):
            raise ValueError("Every material must have a matching source stamp")


def _range(values: Iterable[float] | None) -> tuple[float, float] | None:
    if values is None:
        return None
    limits = tuple(values)
    if (
        len(limits) != 2
        or not all(math.isfinite(value) and value > 0 for value in limits)
        or limits[0] > limits[1]
    ):
        raise ValueError("Wavelength limits must be finite, positive and ordered")
    return float(limits[0]), float(limits[1])


def _index_at(
    material: BaseMaterial, wavelength: float, support: tuple[float, float] | None
) -> PropertyValue:
    if support is not None and not support[0] <= wavelength <= support[1]:
        return PropertyValue(
            None, "unsupported", reason="Reference line is outside declared n support"
        )
    try:
        raw = material.n(wavelength)
        if isinstance(raw, complex):
            return PropertyValue(None, "unsupported", reason="Index is not real")
        value = be.to_numpy(raw)
        if value.size == 0:
            return PropertyValue(None, "missing", reason="No refractive-index value")
        if value.size != 1:
            return PropertyValue(None, "unsupported", reason="Index is not scalar")
        if value.dtype.kind == "c":
            return PropertyValue(None, "unsupported", reason="Index is not real")
        scalar = float(value.reshape(-1)[0])
        if not math.isfinite(scalar) or scalar <= 0:
            return PropertyValue(
                None, "invalid", reason="Glass-map index must be finite and positive"
            )
        return PropertyValue(scalar, "available")
    except NotImplementedError as error:
        return PropertyValue(None, "unsupported", reason=str(error) or "No index model")
    except (ValueError, TypeError, ArithmeticError) as error:
        return PropertyValue(
            None, "invalid", reason=str(error) or "Index evaluation failed"
        )


def describe_material(material: BaseMaterial, key: MaterialKey) -> MaterialSummary:
    """Evaluate native d-line index and Abbe coordinates without changing a material.

    A finite negative Abbe coordinate is retained. Zero dispersion has no Abbe
    coordinate and is still inspectable. Each line is checked against declared
    support before calling ``n``; an out-of-support clamp is never a match.
    Unknown support permits model evaluation but remains unknown to range filters.

    Only an exact built-in ``DataMaterial`` with ordinary homogeneous propagation
    receives an owned reconstruction payload in this initial adapter. File-backed
    materials remain useful for inspection without claiming portable persistence.
    """
    support = _range(material.spectral_range("n"))
    k_support = _range(material.spectral_range("k"))
    nd = _index_at(material, D_LINE_UM, support)
    nf = _index_at(material, F_LINE_UM, support)
    nc = _index_at(material, C_LINE_UM, support)
    unavailable = next(
        (value for value in (nd, nf, nc) if value.status != "available"), None
    )
    if unavailable is not None:
        vd = PropertyValue(None, unavailable.status, reason=unavailable.reason)
    elif nf.value == nc.value:
        vd = PropertyValue(
            None, "missing", reason="Abbe number is undefined at zero dispersion"
        )
    else:
        assert nd.value is not None and nf.value is not None and nc.value is not None
        ratio = (nd.value - 1) / (nf.value - nc.value)
        vd = (
            PropertyValue(ratio, "available")
            if math.isfinite(ratio)
            else PropertyValue(None, "invalid", reason="Abbe number is not finite")
        )

    extinction, transmission = None, None
    owned_data = None
    if isinstance(material, DataMaterial):
        extinction = material.definition.extinction is not None
        transmission = isinstance(material.definition.extinction, TransmissionTable)
        if (
            type(material) is DataMaterial
            and type(material.propagation_model) is HomogeneousPropagation
            and not any(
                name in vars(material)
                for name in ("n", "k", "_calculate_n", "_calculate_k", "to_dict")
            )
            and "propagate" not in vars(material.propagation_model)
        ):
            owned_data = json.dumps(
                material.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
    elif isinstance(material, MaterialFile):
        extinction = material.spectral_range("k") is not None
        transmission = False

    return MaterialSummary(
        key,
        material.display_name,
        nd,
        vd,
        support,
        k_support,
        getattr(material, "bounds", None),
        extinction,
        transmission,
        owned_data,
    )


def reconstruct_material(summary: MaterialSummary) -> DataMaterial:
    """Restore independent owned native data without file or catalog resolution."""
    if summary.owned_data is None:
        raise ValueError("This material has no portable owned definition")
    data = json.loads(summary.owned_data)
    if data.get("type") != "DataMaterial":
        raise ValueError("Expected an owned DataMaterial definition")
    return BaseMaterial.from_dict(data)


@dataclass(frozen=True)
class MaterialQuery:
    """Optional filters and independent patent-matching bounds.

    Each requested coordinate requires its own positive finite tolerance. Both
    bounds must pass; normalized Euclidean distance only ranks passing records.
    Unknown wavelength support is excluded by default when a range is requested.
    """

    source_ids: tuple[str, ...] = ()
    catalogs: tuple[str, ...] = ()
    name: str = ""
    target_nd: float | None = None
    nd_tolerance: float | None = None
    target_vd: float | None = None
    vd_tolerance: float | None = None
    wavelength_range_um: tuple[float, float] | None = None
    unknown_policy: Literal["include", "exclude"] = "exclude"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "catalogs", tuple(self.catalogs))
        if not isinstance(self.name, str) or not all(
            isinstance(value, str) for value in (*self.source_ids, *self.catalogs)
        ):
            raise ValueError("Source, catalog and name filters must be strings")
        object.__setattr__(
            self, "wavelength_range_um", _range(self.wavelength_range_um)
        )
        if self.unknown_policy not in {"include", "exclude"}:
            raise ValueError("Unknown wavelength-support policy")
        for target, tolerance in (
            (self.target_nd, self.nd_tolerance),
            (self.target_vd, self.vd_tolerance),
        ):
            if (target is None) != (tolerance is None):
                raise ValueError("Each coordinate target requires its own tolerance")
            if target is not None and (
                not math.isfinite(target)
                or not math.isfinite(tolerance)
                or tolerance <= 0
            ):
                raise ValueError(
                    "Targets must be finite and tolerances finite and positive"
                )
        if self.target_nd is not None and self.target_nd <= 0:
            raise ValueError("Target refractive index must be positive")
        for name in ("target_nd", "nd_tolerance", "target_vd", "vd_tolerance"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, float(getattr(self, name)))


@dataclass(frozen=True)
class MaterialMatch:
    """A passing record, signed coordinate differences and normalized distance."""

    key: MaterialKey
    delta_nd: float | None
    delta_vd: float | None
    distance: float


@dataclass(frozen=True)
class MaterialQueryResult:
    """Results tied to the request and every catalog/document source generation."""

    request_id: int
    stamps: tuple[SourceStamp, ...]
    matches: tuple[MaterialMatch, ...]
    excluded_counts: tuple[tuple[str, int], ...]


def _match(
    material: MaterialSummary, query: MaterialQuery
) -> tuple[MaterialMatch | None, str | None]:
    if query.source_ids and material.key.source_id not in query.source_ids:
        return None, "source"
    if query.catalogs and material.key.catalog.casefold() not in {
        catalog.casefold() for catalog in query.catalogs
    }:
        return None, "catalog"
    text = query.name.strip().casefold()
    if text and not any(
        text in name.casefold()
        for name in (material.display_name, material.key.record_id)
    ):
        return None, "name"
    if query.wavelength_range_um is not None:
        support = material.n_range_um
        if support is None and query.unknown_policy == "exclude":
            return None, "unknown_support"
        if support is not None and (
            support[0] > query.wavelength_range_um[0]
            or support[1] < query.wavelength_range_um[1]
        ):
            return None, "wavelength_support"
    differences, normalized = [], []
    for label, prop, target, tolerance in (
        ("nd", material.nd, query.target_nd, query.nd_tolerance),
        ("vd", material.vd, query.target_vd, query.vd_tolerance),
    ):
        if target is None:
            differences.append(None)
            continue
        if prop.status != "available":
            return None, f"{label}_{prop.status}"
        assert prop.value is not None and tolerance is not None
        delta = prop.value - target
        if not target - tolerance <= prop.value <= target + tolerance:
            return None, f"{label}_tolerance"
        differences.append(delta)
        normalized.append(delta / tolerance)
    return MaterialMatch(material.key, *differences, math.hypot(*normalized)), None


def query_materials(
    snapshot: MaterialSnapshot, query: MaterialQuery, *, request_id: int = 0
) -> MaterialQueryResult:
    """Filter and rank immutable records; preserve unavailable entries when unfiltered.

    Exclusion counts report the first failed predicate per record. Results retain
    all snapshot stamps, including a current-design stamp in mixed-source queries.
    Callers must compare these stamps before presenting late asynchronous results.
    """
    matches, excluded = [], {}
    for material in snapshot.materials:
        match, reason = _match(material, query)
        if match is not None:
            matches.append(match)
        else:
            excluded[reason] = excluded.get(reason, 0) + 1
    return MaterialQueryResult(
        request_id,
        snapshot.stamps,
        tuple(sorted(matches, key=lambda match: (match.distance, match.key))),
        tuple(sorted(excluded.items())),
    )
