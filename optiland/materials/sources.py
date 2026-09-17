"""Captured material sources and atomic, Qt-free ingestion for material browsers.

File requests are local locators. Ingestion reads each selected file once, hashes
those bytes and produces self-contained summaries. Subsequent queries never
reopen files or resolve material names. Source identifiers are caller-owned;
content generations describe the data actually read, not filesystem timestamps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from optiland.materials.material_file import MaterialFile
from optiland.materials.portability import capture_material, restore_material
from optiland.materials.properties import (
    MaterialKey,
    MaterialSnapshot,
    MaterialSummary,
    PropertyValue,
    SourceStamp,
    describe_material,
)
from optiland.materials.registry import RegistryEntry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from optiland.materials.base import BaseMaterial

MAX_SOURCE_RECORDS = 50_000
MAX_RII_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024


def _text(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("Source and record identities must be nonempty strings")


def _entries(owner: object, entries: Iterable, kind: type) -> None:
    values = tuple(entries)
    if len(values) > MAX_SOURCE_RECORDS or any(not isinstance(e, kind) for e in values):
        raise ValueError("Invalid source entries or source record limit exceeded")
    if len({e.record_id for e in values}) != len(values):
        raise ValueError("Source record identities must be unique")
    object.__setattr__(owner, "entries", values)


@dataclass(frozen=True)
class RegistrySource:
    """Exact installed or user RII records, captured before job submission."""

    source_id: str
    entries: tuple[RegistryEntry, ...]

    def __post_init__(self) -> None:
        _text(self.source_id)
        _entries(self, self.entries, RegistryEntry)
        if any(entry.source_id != self.source_id for entry in self.entries):
            raise ValueError("Registry entries must belong to the requested source")


@dataclass(frozen=True)
class GlcSource:
    """A selected GLC locator; equal filenames need distinct caller source IDs."""

    source_id: str
    path: str

    def __post_init__(self) -> None:
        _text(self.source_id)
        _text(self.path)


@dataclass(frozen=True)
class OwnedEntry:
    """A detached native definition or an explicit unsupported capture reason."""

    record_id: str
    display_name: str
    payload: bytes | None
    reason: str | None = None

    def __post_init__(self) -> None:
        _text(self.record_id)
        if not isinstance(self.display_name, str):
            raise ValueError("Display name must be text")
        if self.payload is not None:
            if not isinstance(self.payload, bytes | bytearray):
                raise ValueError("Owned definitions must be native JSON bytes")
            object.__setattr__(self, "payload", bytes(self.payload))
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError("Capture reason must be text")
        if self.payload is None and not self.reason:
            raise ValueError("An unsupported entry requires a capture reason")


@dataclass(frozen=True)
class OwnedSource:
    """Portable material occurrences; a design source includes document identity."""

    source_id: str
    entries: tuple[OwnedEntry, ...]

    def __post_init__(self) -> None:
        _text(self.source_id)
        _entries(self, self.entries, OwnedEntry)
        if sum(len(entry.payload or b"") for entry in self.entries) > MAX_SOURCE_BYTES:
            raise ValueError("Owned source exceeds byte limit")


@dataclass(frozen=True)
class SourceDiagnostic:
    """Plain-text source/record failure, separate from optical coordinates."""

    source_id: str
    record_id: str | None
    message: str


@dataclass(frozen=True)
class SourceResult:
    """A complete staged source; failed replacement must not erase accepted data."""

    snapshot: MaterialSnapshot
    diagnostics: tuple[SourceDiagnostic, ...] = ()
    failed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


def capture_design(
    material_occurrences: Iterable[tuple[str, BaseMaterial]], *, document_id: str
) -> OwnedSource:
    """Copy live parameters without sampling optical functions or reopening files.

    Occurrence IDs must identify surviving document surfaces, not visual row
    numbers. Shared materials at separate occurrences remain separate records.
    Unsupported custom materials remain inspectable through capture diagnostics.
    Callers own document revision checks around this synchronous capture; source
    loading and property calculations run separately outside the GUI thread.
    """
    _text(document_id)
    entries = []
    for record_id, material in material_occurrences:
        _text(record_id)
        if len(entries) >= MAX_SOURCE_RECORDS:
            raise ValueError("Source record limit exceeded")
        try:
            payload = capture_material(material)
            entry = OwnedEntry(record_id, material.display_name, payload)
        except (ValueError, TypeError, NotImplementedError, ArithmeticError) as error:
            entry = OwnedEntry(record_id, type(material).__name__, None, str(error))
        entries.append(entry)
    return OwnedSource(f"design:{document_id}", tuple(entries))


def _hash_parts(parts: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _unavailable(key: MaterialKey, name: str, reason: str) -> MaterialSummary:
    value = PropertyValue(None, "unsupported", reason=reason)
    return MaterialSummary(key, name, value, value, None, None, None, None, None)


def _read(path: str, limit: int) -> bytes:
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Material source exceeds byte limit")
    return data


def load_source(
    request: RegistrySource | GlcSource | OwnedSource,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> SourceResult:
    """Load a staged source, preserving healthy records and explicit failures.

    Invoke outside the GUI thread. Checkpoints may raise cancellation; that
    exception propagates and no partial result is published. Malformed GLC files
    fail as a whole, following the existing decoder. RII read/parse failures
    produce inspectable diagnostic rows; an entirely unreadable nonempty RII
    source is marked failed, allowing a coordinator to retain its prior version.
    """
    if not isinstance(request, RegistrySource | GlcSource | OwnedSource):
        raise TypeError("Unknown material source request")
    check = checkpoint or (lambda: None)
    check()
    if isinstance(request, GlcSource):
        return _load_glc(request, check)
    records, diagnostics, parts = [], [], []
    readable, payload_bytes = 0, 0
    for entry in sorted(request.entries, key=lambda e: e.record_id):
        check()
        if isinstance(request, RegistrySource):
            key = MaterialKey(request.source_id, entry.catalog, entry.record_id)
            name = entry.name
            parts.append(_json([entry.record_id, entry.catalog, name, entry.reference]))
            try:
                raw = _read(entry.path, MAX_RII_BYTES)
                parts.append(hashlib.sha256(raw).digest())
                decoded = yaml.safe_load(raw.decode("utf-8-sig"))
                if not isinstance(decoded, dict):
                    raise ValueError("RII source must contain a YAML object")
                material = MaterialFile.from_data(decoded, name=name, bounds="clamp")
                summary = describe_material(material, key)
                readable += 1
                try:
                    payload = capture_material(material)
                    summary = replace(summary, owned_data=payload)
                except (ValueError, TypeError, NotImplementedError) as error:
                    diagnostics.append(
                        SourceDiagnostic(request.source_id, entry.record_id, str(error))
                    )
                records.append(summary)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                IndexError,
                yaml.YAMLError,
            ) as error:
                message = str(error)
                parts.append(_json(["unreadable", message]))
                diagnostics.append(
                    SourceDiagnostic(request.source_id, entry.record_id, message)
                )
                records.append(_unavailable(key, name, message))
        else:
            key = MaterialKey(
                request.source_id,
                "Current design"
                if request.source_id.startswith("design:")
                else "Owned",
                entry.record_id,
            )
            parts.extend(
                (
                    _json([entry.record_id, entry.display_name, entry.reason]),
                    entry.payload or b"",
                )
            )
            try:
                if entry.payload is None:
                    raise ValueError(entry.reason)
                material = restore_material(entry.payload)
                summary = describe_material(material, key)
                records.append(
                    replace(
                        summary,
                        display_name=entry.display_name,
                        owned_data=entry.payload,
                    )
                )
                readable += 1
            except (ValueError, TypeError, KeyError) as error:
                message = str(error)
                diagnostics.append(
                    SourceDiagnostic(request.source_id, entry.record_id, message)
                )
                records.append(_unavailable(key, entry.display_name, message))
        check()
        payload_bytes += len(records[-1].owned_data or b"")
        if payload_bytes > MAX_SOURCE_BYTES:
            raise ValueError("Material source exceeds owned-data byte limit")
    stamp = SourceStamp(request.source_id, _hash_parts(parts))
    failed = (
        isinstance(request, RegistrySource) and bool(request.entries) and not readable
    )
    return SourceResult(
        MaterialSnapshot((stamp,), tuple(records)), tuple(diagnostics), failed
    )


def _load_glc(request: GlcSource, check: Callable[[], None]) -> SourceResult:
    # Lazy import avoids a materials -> fileio -> materials import cycle.
    from optiland.fileio.oslo.catalog import MAX_CATALOG_BYTES, decode_glc_catalog

    raw = b""
    try:
        if Path(request.path).suffix.casefold() != ".glc":
            raise ValueError("Expected a GLC text catalog")
        raw = _read(request.path, MAX_CATALOG_BYTES)
        entries = decode_glc_catalog(raw, name=request.path)
    except (OSError, ValueError, TypeError) as error:
        diagnostic = SourceDiagnostic(request.source_id, None, str(error))
        stamp = SourceStamp(
            request.source_id, _hash_parts((raw, _json(diagnostic.message)))
        )
        return SourceResult(MaterialSnapshot((stamp,), ()), (diagnostic,), True)
    records, counts = [], {}
    for entry in entries:
        check()
        material = entry.material
        count = counts.get(material.name, 0)
        counts[material.name] = count + 1
        record_id = _hash_parts((_json([material.name, count]),))
        catalog = material.metadata["provenance"][0]["catalog"]
        key = MaterialKey(request.source_id, catalog, record_id)
        records.append(describe_material(material, key))
    check()
    # An unlabeled header derives its catalog from the filename. Include that
    # effective label because it participates in identity and native provenance;
    # the decoder's separate provenance SHA-256 still describes raw bytes only.
    label = records[0].key.catalog if records else None
    return SourceResult(
        MaterialSnapshot(
            (SourceStamp(request.source_id, _hash_parts((raw, _json(label)))),),
            tuple(records),
        )
    )
