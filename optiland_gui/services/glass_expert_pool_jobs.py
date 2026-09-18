"""Explicit, bounded candidate-pool preparation on the shared worker."""

from __future__ import annotations

import pickle
import time
from dataclasses import dataclass

from optiland.materials.properties import MaterialKey, MaterialSummary, SourceStamp
from optiland.optimization.material_candidates import (
    MaterialCandidate,
    prepare_material_candidate,
)

from .glass_expert_records import MAX_CANDIDATES, MAX_RUN_BYTES
from .job_records import check_cancelled


@dataclass(frozen=True)
class PoolExclusion:
    """One exact material rejected during explicit pool preparation."""

    key: MaterialKey
    label: str
    reason: str


@dataclass(frozen=True)
class PreparedPool:
    """Frozen eligible entries and visible exclusions from an explicit action."""

    candidates: tuple[MaterialCandidate, ...]
    exclusions: tuple[PoolExclusion, ...]
    stamps: tuple[SourceStamp, ...]


def prepare_pool(snapshot, parameters, progress, cancelled):
    """Validate every explicitly selected identity, retaining exclusion reasons."""
    check_cancelled(cancelled)
    entries = parameters["entries"]
    if not isinstance(entries, tuple) or not 1 <= len(entries) <= MAX_CANDIDATES:
        raise ValueError(f"Select between 1 and {MAX_CANDIDATES} exact materials.")
    if len(pickle.dumps(parameters, protocol=5)) > MAX_RUN_BYTES:
        raise ValueError("The selected pool exceeds the 64 MiB run budget.")
    parameters["backend"].apply()
    candidates, exclusions, stamps, keys = [], [], {}, set()
    last = -float("inf")
    for index, entry in enumerate(entries):
        check_cancelled(cancelled)
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError("Pool entries require an exact record and source stamp.")
        record, stamp = entry
        if not isinstance(record, MaterialSummary) or not isinstance(
            stamp, SourceStamp
        ):
            raise ValueError("Pool entries require an exact record and source stamp.")
        if record.key in keys or record.key.source_id != stamp.source_id:
            raise ValueError("Pool identities must be distinct and source-qualified.")
        keys.add(record.key)
        if stamps.setdefault(stamp.source_id, stamp) != stamp:
            raise ValueError("Pool entries have conflicting source generations.")
        try:
            candidate = prepare_material_candidate(
                record,
                stamp,
                wavelengths_um=parameters["wavelengths"],
                checkpoint=lambda: check_cancelled(cancelled),
            )
            candidates.append(candidate)
        except (ValueError, TypeError, NotImplementedError) as error:
            exclusions.append(
                PoolExclusion(record.key, record.display_name, str(error)[:2048])
            )
        now = time.monotonic()
        if now - last >= 0.2 or index + 1 == len(entries):
            progress("Validating exact material pool", index + 1, len(entries))
            last = now
    check_cancelled(cancelled)
    return PreparedPool(tuple(candidates), tuple(exclusions), tuple(stamps.values()))
