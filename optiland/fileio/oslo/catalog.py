"""Read explicitly selected OSLO text glass catalogs without registry side effects.

Record groups follow the OSLO Program Reference pp. 189-191 and the 2026 help
topics userint/database/glass.htm and lens/glass/update.htm. CDB is browsing data
and is never used to override GLC records. Unsupported optical groups fail closed.
"""

from __future__ import annotations

import hashlib
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from optiland.materials import BaseMaterial, DataMaterial

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

SUPPORTED_GLC_VERSIONS = {
    "5.00",
    "5.11",
    "5.32",
    "6.31",
    "6.4",
    "6.63",
    "6.65",
    "7.02",
    "24.1",
    "26.1",
}
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_CATALOG_RECORDS = 50_000
MAX_RECORD_CHARS = 64 * 1024


@dataclass(frozen=True)
class CatalogEntry:
    """One validated material and a transient location for diagnostics."""

    material: DataMaterial
    location: str


class _Record:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.position = 0

    def take(self) -> str:
        if self.position >= len(self.tokens):
            raise ValueError("truncated catalog record")
        value = self.tokens[self.position]
        self.position += 1
        return value

    def number(self) -> float:
        token = self.take()
        value = float(token.replace("D", "E").replace("d", "e"))
        if not math.isfinite(value):
            raise ValueError(f"non-finite catalog value {token!r}")
        return value

    def integer(self) -> int:
        token = self.take()
        try:
            return int(token)
        except ValueError as exc:
            raise ValueError(f"expected catalog integer, got {token!r}") from exc

    def group(self, max_count: int, width: int = 1) -> tuple[int, list[float]]:
        model, count = self.integer(), self.integer()
        if not 0 <= count <= max_count:
            raise ValueError(f"catalog group count must be between 0 and {max_count}")
        return model, [self.number() for _ in range(count * width)]

    @property
    def remaining(self) -> bool:
        return self.position < len(self.tokens)


def _parse_record(line: str, provenance: dict[str, str]) -> DataMaterial:
    if len(line) > MAX_RECORD_CHARS:
        raise ValueError("catalog record exceeds size limit")
    record = _Record(line.split())
    name = record.take()
    if len(name) > 10:
        raise ValueError("GLC glass names must be at most 10 characters")
    fields = [record.number() for _ in range(9)]
    nd, vd, density, dndt, tce, trans25, cost, hardness, chemical = fields
    # Anomalous dispersion can have a negative Abbe number; this summary must
    # not constrain the authoritative coefficient model.
    if nd <= 0 or density < 0 or not 0 <= trans25 <= 1:
        raise ValueError("invalid catalog index, density or transmittance")
    bubble, availability = record.take(), record.take()
    model, c = record.group(9)
    if model in {1, 2} and len(c) == 6:
        if model == 1:
            formula = "formula 3"
            coeff = [c[0], c[1], 2, c[2], -2, c[3], -4, c[4], -6, c[5], -8]
        else:
            formula = "formula 2"
            coeff = [0, c[0], c[3], c[1], c[4], c[2], c[5]]
    elif model == 3 and len(c) == 3:
        formula = "formula 5"
        coeff = [c[0], c[1], -1, c[2], -3.5]
    elif model == 6 and len(c) == 9:
        formula = "formula 3"
        powers = [2, 4, -2, -4, -6, -8, -10, -12]
        coeff = [c[0]] + [
            value for pair in zip(c[1:], powers, strict=True) for value in pair
        ]
    elif model == 7 and len(c) == 6:
        formula, coeff = "formula 7", c
    else:
        raise ValueError(f"unsupported dispersion model/count {model}/{len(c)}")

    thermal_model, thermal_coeff = record.group(6)
    thermal = None
    if thermal_model == 0:
        # The explicit model flag says there is no thermal law. Some supplied
        # catalogs retain one unused coefficient (including nonzero summaries).
        # Preserve that value in metadata, without treating it as a dn/dT model.
        if len(thermal_coeff) > 1:
            raise ValueError("unsupported no-thermal-model placeholder")
    elif thermal_model in {1, 2, 3}:
        model_name, count = {1: ("schott", 6), 2: ("constant", 1), 3: ("corning", 4)}[
            thermal_model
        ]
        if len(thermal_coeff) != count:
            raise ValueError(
                f"thermal model {thermal_model} requires {count} coefficients"
            )
        thermal = {
            "kind": model_name,
            # The database help specifies single-value and Corning coefficients
            # in units of 1e-6/K. Schott coefficients are already unscaled.
            "coefficients": [v * 1e-6 for v in thermal_coeff]
            if thermal_model in {2, 3}
            else thermal_coeff,
            "reference_temperature_c": 20.0,
        }
    else:
        raise ValueError(f"unsupported thermal model {thermal_model}")

    transmission_model, samples = record.group(50, width=2)
    transmission = None
    if transmission_model in {0, 1} and not samples:
        pass
    elif transmission_model == 1 and len(samples) >= 6:
        pairs = sorted(zip(samples[::2], samples[1::2], strict=True))
        transmission = {
            "kind": "internal_transmittance",
            "wavelengths_um": [w for w, _ in pairs],
            "values": [t for _, t in pairs],
            "thickness_mm": 5.0,
            "interpolation": "local_quadratic",
        }
    else:
        raise ValueError("unsupported transmission model/count")

    limits, reference_id = None, None
    if record.remaining:
        range_model, limits = record.group(2)
        if range_model == 0 and not limits:
            limits = None
        elif range_model != 1 or len(limits) != 2:
            raise ValueError("unsupported wavelength-range model/count")
    if record.remaining:
        ref_model, ref_count = record.integer(), record.integer()
        if ref_model == 1 and ref_count == 1:
            reference_id = record.integer()
        elif ref_model != 0 or ref_count != 0:
            raise ValueError("unsupported reference-data model/count")
    if record.remaining:
        raise ValueError("unknown trailing catalog data")

    return DataMaterial.from_coefficients(
        formula,
        coeff,
        name=name,
        wavelength_range=limits,
        thermal=thermal,
        extinction=transmission,
        metadata={
            "oslo": {
                "nd": nd,
                "vd": vd,
                "density_g_cm3": density,
                "dndt_1e6": dndt,
                "tce_1e7": tce,
                "transmission_25mm_400nm": trans25,
                "cost": cost,
                "hardness": hardness,
                "chemical_code": chemical,
                "bubble_group": bubble,
                "availability": availability,
                "dispersion_model": model,
                "dispersion_coefficients": c,
                "thermal_model": thermal_model,
                "thermal_coefficients": thermal_coeff,
                "transmission_model": transmission_model,
                "reference_id": reference_id,
            },
            "provenance": [provenance],
        },
    )


def read_glc_catalog(path: str | Path) -> list[CatalogEntry]:
    """Validate an entire GLC file and return portable materials with locations.

    Malformed or unsupported selected definitions raise ValueError in every mode.
    Provenance embeds the header label, format version and content SHA-256 only.
    """
    path = Path(path)
    if path.suffix.casefold() != ".glc":
        raise ValueError(f"{path.name}: expected a GLC text catalog")
    with path.open("rb") as stream:
        raw = stream.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError(f"{path.name}: catalog exceeds size limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    lines = text.splitlines()
    if not lines:
        raise ValueError(f"{path.name}:1: missing GLC header")
    try:
        header = lines[0].split(maxsplit=2)
        version, count = header[0], int(header[1])
        if version not in SUPPORTED_GLC_VERSIONS:
            raise ValueError(f"unsupported GLC version {version!r}")
        if not 0 <= count <= MAX_CATALOG_RECORDS:
            raise ValueError("invalid GLC record count")
        records = [(i, line) for i, line in enumerate(lines[1:], 2) if line.strip()]
        if len(records) != count:
            raise ValueError(f"declared {count} records, found {len(records)}")
    except (ValueError, IndexError) as exc:
        raise ValueError(f"{path.name}:1: invalid GLC header: {exc}") from exc
    provenance = {
        "catalog": header[2] if len(header) == 3 else path.stem,
        "format_version": version,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    result = []
    for number, line in records:
        # Locations are transient diagnostics, not embedded provenance. Keep the
        # directory so identically named catalogs remain distinguishable on error.
        location = f"{path}:{number}"
        try:
            material = _parse_record(line, provenance)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"{location}: {line.split()[0]}: {exc}") from exc
        result.append(CatalogEntry(material, location))
    return result


def load_glc_catalogs(
    paths: Iterable[str | Path],
    overrides: Mapping[str, BaseMaterial],
) -> dict[str, DataMaterial]:
    """Merge explicit catalogs, requiring bindings for conflicting definitions."""
    materials: dict[str, DataMaterial] = {}
    locations: dict[str, str] = {}
    # Sort paths and provenance for deterministic results, independent of chooser
    # order. Resolve paths only for deduplication; never save them in materials.
    if isinstance(paths, (str, Path)):
        raise TypeError("glass_catalogs must be a collection of GLC paths")
    for path in sorted(
        {Path(p).resolve() for p in paths}, key=lambda p: (str(p).casefold(), str(p))
    ):
        for entry in read_glc_catalog(path):
            material = entry.material
            key = material.name.casefold()
            if key in overrides:
                continue
            if key in materials:
                old, new = materials[key].to_dict(), material.to_dict()
                for data in (old, new):
                    data["name"] = key
                    data["metadata"].pop("provenance")
                if old != new:
                    raise ValueError(
                        f"Conflicting GLC definitions for {material.name!r}: "
                        f"{locations[key]} and {entry.location}; "
                        "provide material_overrides"
                    )
                provenance = materials[key].metadata["provenance"]
                for source in material.metadata["provenance"]:
                    if source not in provenance:
                        provenance.append(deepcopy(source))
                provenance.sort(key=lambda p: (p["catalog"], p["sha256"]))
                merged = materials[key].to_dict()
                merged["metadata"]["provenance"] = provenance
                materials[key] = BaseMaterial.from_dict(merged)
            else:
                materials[key], locations[key] = material, entry.location
    return materials
