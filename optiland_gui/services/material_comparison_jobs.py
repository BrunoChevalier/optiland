"""Shared-worker material maps, spectral comparisons and nearby alternatives."""

from __future__ import annotations

import pickle

from optiland.materials.comparison import (
    MAX_PINS,
    compare_materials,
    map_coordinates,
    nearby_materials,
)
from optiland.materials.properties import MaterialSnapshot

from .job_records import check_cancelled


def _snapshot(parameters, cancelled):
    # Trusted internal source-summary transport produced by encode_query_source;
    # never an external file format. Native owned payloads are absent here.
    records = []
    for blob in parameters["material_blobs"]:
        check_cancelled(cancelled)
        records.extend(pickle.loads(blob))
    check_cancelled(cancelled)
    return MaterialSnapshot(parameters["stamps"], tuple(records))


def compute_map_view(snapshot, parameters, progress, cancelled):
    """Derive alternate coordinates from cached reference-line scalars."""
    check_cancelled(cancelled)
    parameters["backend"].apply()
    progress("Computing material map coordinates")
    result = map_coordinates(
        _snapshot(parameters, cancelled),
        parameters["view"],
        checkpoint=lambda: check_cancelled(cancelled),
    )
    check_cancelled(cancelled)
    return result


def compute_comparison(snapshot, parameters, progress, cancelled):
    """Restore and evaluate only the at-most-four requested owned pin records."""
    check_cancelled(cancelled)
    parameters["backend"].apply()
    progress("Computing material spectra")
    if len(parameters["material_snapshot"].materials) > MAX_PINS:
        raise ValueError("Comparison worker accepts at most four owned records")
    result = compare_materials(
        parameters["material_snapshot"],
        parameters["request"],
        checkpoint=lambda: check_cancelled(cancelled),
    )
    check_cancelled(cancelled)
    return result


def compute_nearby(snapshot, parameters, progress, cancelled):
    """Rank lightweight records with user-visible geometric coordinate scales."""
    check_cancelled(cancelled)
    parameters["backend"].apply()
    progress("Finding nearby materials")
    result = nearby_materials(
        _snapshot(parameters, cancelled),
        parameters["target_key"],
        nd_scale=parameters["nd_scale"],
        vd_scale=parameters["vd_scale"],
        limit=parameters.get("limit", 10),
        checkpoint=lambda: check_cancelled(cancelled),
    )
    check_cancelled(cancelled)
    return result
