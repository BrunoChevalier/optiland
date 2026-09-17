"""Detached material ingestion and filtering on the shared calculation worker."""

from __future__ import annotations

from optiland.materials.properties import query_materials
from optiland.materials.sources import load_source

from .job_records import check_cancelled


def load_material_source(snapshot, parameters, progress, cancelled):
    """Read one captured source request and return portable records and diagnostics."""
    check_cancelled(cancelled)
    parameters["backend"].apply()
    progress("Loading material source")
    result = load_source(
        parameters["source"], checkpoint=lambda: check_cancelled(cancelled)
    )
    check_cancelled(cancelled)
    return result


def filter_materials(snapshot, parameters, progress, cancelled):
    """Filter lightweight immutable summaries with their captured source stamps."""
    check_cancelled(cancelled)
    parameters["backend"].apply()
    progress("Finding materials")
    result = query_materials(
        parameters["materials"],
        parameters["query"],
        request_id=parameters["request_id"],
    )
    check_cancelled(cancelled)
    return result
