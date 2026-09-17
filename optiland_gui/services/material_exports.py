"""Capture immutable material output with exact identity and optical provenance."""

from __future__ import annotations

import json
from dataclasses import asdict

from .file_artifacts import CsvArtifact, RgbaArtifact


def json_text(value):
    """Use lossless numeric JSON, independent of the display locale."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def browse_provenance(query, stamps, view, bounds):
    """Describe the accepted filters, sources and actual axes of a map."""
    return {
        "kind": "material-map",
        "query": asdict(query),
        "sources": [asdict(stamp) for stamp in stamps],
        "view": view,
        "plot_bounds": bounds,
        "conditions": "Native source conditions; no normalization",
    }


def condition_groups(records):
    """Attribute all displayed native conditions without repeating model data."""
    groups, seen = {}, set()
    for record in records:
        if record.key in seen:
            continue
        seen.add(record.key)
        conditions = asdict(record.conditions)
        identity = json_text(conditions)
        group = groups.setdefault(
            identity, {"conditions": conditions, "count": 0, "sources": set()}
        )
        group["count"] += 1
        group["sources"].add(record.key.source_id)
    return tuple(
        {**group, "sources": sorted(group["sources"])} for group in groups.values()
    )


def browse_csv(records, coordinates, labels, provenance, matches=()):
    """Freeze the visible shortlist, including records without map coordinates."""
    generations = {
        stamp["source_id"]: stamp["generation"] for stamp in provenance["sources"]
    }
    matched = {match.key: match for match in matches}
    rows = []
    for record in records:
        point = coordinates[record.key]
        match = matched.get(record.key)
        rows.append(
            (
                record.key.source_id,
                record.key.catalog,
                record.key.record_id,
                record.display_name,
                labels.get(record.key.source_id, record.key.source_id),
                generations[record.key.source_id],
                record.nd.value,
                record.nd.reason,
                record.vd.value,
                record.vd.reason,
                match.delta_nd if match is not None else None,
                match.delta_vd if match is not None else None,
                match.distance if match is not None else None,
                point.x.value,
                point.x.status,
                point.x.reason,
                point.y.value,
                point.y.status,
                point.y.reason,
                json_text(asdict(record.conditions)),
                json_text(record.n_range_um),
                json_text(record.k_range_um),
                record.bounds,
            )
        )
    return CsvArtifact(
        (
            "source_id",
            "catalog",
            "record_id",
            "material",
            "source_label",
            "source_generation",
            "n_d",
            "n_d_reason",
            "V_d",
            "V_d_reason",
            "delta_nd",
            "delta_vd",
            "match_distance",
            "plot_x",
            "plot_x_status",
            "plot_x_reason",
            "plot_y",
            "plot_y_status",
            "plot_y_reason",
            "native_conditions",
            "n_support_um",
            "k_support_um",
            "bounds_policy",
        ),
        tuple(rows),
        (("material_view", json_text(provenance)),),
    )


def spectral_provenance(result, bounds):
    """Describe requested samples and native models without live optical objects."""
    return {
        "kind": "material-comparison",
        "request": asdict(result.request),
        "sources": [asdict(stamp) for stamp in result.stamps],
        "interval_um": result.interval_um,
        "plot_bounds": bounds,
        "notes": result.notes,
        "materials": [
            {
                "key": asdict(series.key),
                "name": series.display_name,
                "conditions": asdict(series.conditions),
                "declared_range_um": series.declared_range_um,
                "warnings": series.warnings,
                "details": asdict(series.details) if series.details else None,
            }
            for series in result.series
        ],
    }


def spectral_csv(result, labels, bounds):
    """Export exactly the shared curve/table sample grid, retaining every gap."""
    generations = {stamp.source_id: stamp.generation for stamp in result.stamps}
    rows = tuple(
        (
            number,
            series.key.source_id,
            series.key.catalog,
            series.key.record_id,
            series.display_name,
            labels.get(series.key.source_id, series.key.source_id),
            generations.get(series.key.source_id),
            wave,
            result.request.quantity,
            value.value,
            value.unit,
            value.status,
            value.reason,
            json_text(asdict(series.conditions)),
            json_text(series.declared_range_um),
            json_text(series.warnings),
            result.request.thickness_mm
            if result.request.quantity == "transmission"
            else None,
        )
        for number, series in enumerate(result.series, 1)
        for wave, value in zip(result.wavelengths_um, series.values, strict=True)
    )
    # Details can be substantial; include their attribution in PNG metadata and
    # the Details tab rather than repeat up to 64 KiB of coefficients per sample.
    provenance = spectral_provenance(result, bounds)
    provenance.pop("materials")
    return CsvArtifact(
        (
            "pin",
            "source_id",
            "catalog",
            "record_id",
            "material",
            "source_label",
            "source_generation",
            "wavelength_um",
            "quantity",
            "value",
            "unit",
            "status",
            "reason",
            "native_conditions",
            "declared_support_um",
            "series_warnings",
            "thickness_mm",
        ),
        rows,
        (("comparison", json_text(provenance)),),
    )


def plot_png(capture, provenance):
    """Attach canonical provenance to detached canvas pixels for worker encoding."""
    width, height, pixels = capture
    return RgbaArtifact(
        width, height, 4 * width, pixels, (("Optiland", json_text(provenance)),)
    )
