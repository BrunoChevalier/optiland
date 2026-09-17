"""Immutable, bounded table/image data for non-document file exports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_CSV_ROWS = 100_000
MAX_CSV_CELLS = 1_000_000
MAX_CSV_COLUMNS = 64
MAX_TEXT_BYTES = 64 * 1024
MAX_METADATA_BYTES = 256 * 1024
MAX_PNG_DIMENSION = 8192
MAX_PNG_PIXELS = 16_000_000

CsvCell: TypeAlias = str | int | float | bool | None
Metadata: TypeAlias = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class BytesArtifact:
    """Already encoded immutable output, such as a JSON view preset."""

    data: bytes

    def __post_init__(self) -> None:
        validate_artifact(self)


@dataclass(frozen=True)
class CsvArtifact:
    """Exact table cells with per-export provenance appended as CSV columns.

    Missing numeric cells use ``None``; their reason belongs in an explicit table
    column. Per-material identity/conditions belong in ``columns`` and ``rows``.
    Export-wide provenance columns use the prefix ``provenance:`` and repeat on
    every row, keeping the output an ordinary rectangular CSV without a preamble.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[CsvCell, ...], ...]
    provenance: Metadata = ()

    def __post_init__(self) -> None:
        validate_artifact(self)


@dataclass(frozen=True)
class RgbaArtifact:
    """Owned top-down RGBA8 pixels, row stride and PNG text provenance.

    The GUI captures pixels on its owning thread. The file worker receives no
    Qt image, canvas, model or artist, and performs only PNG encoding.
    """

    width: int
    height: int
    stride: int
    pixels: bytes
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        validate_artifact(self)


FileArtifact: TypeAlias = BytesArtifact | CsvArtifact | RgbaArtifact


def _text_size(value: str, *, limit: int = MAX_TEXT_BYTES) -> int:
    if type(value) is not str or "\x00" in value:
        raise ValueError("Artifact text must be a string without NUL characters.")
    if len(value) > limit:
        raise ValueError("Artifact text exceeds its byte limit.")
    size = len(value.encode("utf-8"))
    if size > limit:
        raise ValueError("Artifact text exceeds its byte limit.")
    return size


def _metadata_size(metadata: Metadata, *, png: bool = False) -> int:
    if type(metadata) is not tuple or len(metadata) > MAX_CSV_COLUMNS:
        raise ValueError("Artifact metadata must be a bounded immutable tuple.")
    total, keys = 0, set()
    for pair in metadata:
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError("Artifact metadata must contain key/value pairs.")
        key, value = pair
        total += _text_size(key) + _text_size(value, limit=MAX_METADATA_BYTES)
        if not key or key in keys:
            raise ValueError("Artifact metadata keys must be nonempty and unique.")
        if png:
            try:
                keyword = key.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError("PNG metadata keys must be Latin-1 keywords.") from exc
            if not 1 <= len(keyword) <= 79:
                raise ValueError("PNG metadata keys must contain 1–79 bytes.")
            if (
                key != key.strip(" ")
                or "  " in key
                or any(
                    not (32 <= value <= 126 or 161 <= value <= 255) for value in keyword
                )
            ):
                raise ValueError("PNG metadata keys must be printable PNG keywords.")
        keys.add(key)
    if total > MAX_METADATA_BYTES:
        raise ValueError("Artifact metadata exceeds its byte limit.")
    return total


def validate_artifact(
    artifact: FileArtifact, *, cancel_check: Callable[[], None] | None = None
) -> str:
    """Validate deep immutability and bounded input before admission or encoding.

    Returns the output format. Workers repeat this validation because trusted
    process transport reconstructs dataclasses without calling their initializer.
    The writer separately caps actual encoded bytes, including quoting/PNG costs.
    """
    if type(artifact) is BytesArtifact:
        if type(artifact.data) is not bytes or len(artifact.data) > MAX_ARTIFACT_BYTES:
            raise ValueError("Artifact data must be bounded immutable bytes.")
        return "bytes"
    if type(artifact) is RgbaArtifact:
        if any(
            type(value) is not int
            for value in (artifact.width, artifact.height, artifact.stride)
        ):
            raise ValueError("RGBA dimensions and stride must be integers.")
        if not (
            1 <= artifact.width <= MAX_PNG_DIMENSION
            and 1 <= artifact.height <= MAX_PNG_DIMENSION
            and artifact.width * artifact.height <= MAX_PNG_PIXELS
            and artifact.stride >= 4 * artifact.width
        ):
            raise ValueError("RGBA dimensions or stride exceed the image limits.")
        if (
            type(artifact.pixels) is not bytes
            or len(artifact.pixels) != artifact.stride * artifact.height
            or len(artifact.pixels) + _metadata_size(artifact.metadata, png=True)
            > MAX_ARTIFACT_BYTES
        ):
            raise ValueError("RGBA pixels must match the bounded declared row layout.")
        return "png"
    if type(artifact) is not CsvArtifact:
        raise ValueError("Unsupported immutable file artifact.")
    if type(artifact.columns) is not tuple or not artifact.columns:
        raise ValueError("CSV columns must be a nonempty immutable tuple.")
    if type(artifact.rows) is not tuple or not 1 <= len(artifact.rows) <= MAX_CSV_ROWS:
        raise ValueError("CSV export needs 1–100000 immutable rows.")
    metadata_size = _metadata_size(artifact.provenance)
    column_count = len(artifact.columns) + len(artifact.provenance)
    if (
        column_count > MAX_CSV_COLUMNS
        or column_count * len(artifact.rows) > MAX_CSV_CELLS
    ):
        raise ValueError("CSV export exceeds its column or cell limit.")
    headers = (
        *artifact.columns,
        *(f"provenance:{key}" for key, _ in artifact.provenance),
    )
    total = sum(_text_size(column) for column in headers)
    if any(not column for column in headers) or len(set(headers)) != len(headers):
        raise ValueError("CSV columns must be nonempty and unique.")
    total += metadata_size * len(artifact.rows)
    for index, row in enumerate(artifact.rows):
        if cancel_check is not None and index % 128 == 0:
            cancel_check()
        if type(row) is not tuple or len(row) != len(artifact.columns):
            raise ValueError("CSV rows must be immutable and match the columns.")
        for value in row:
            if type(value) is str:
                total += _text_size(value)
            elif value is None or type(value) is bool:
                total += 5
            elif type(value) is int and -(2**63) <= value < 2**63:
                total += 20
            elif type(value) is float and math.isfinite(value):
                total += 24
            else:
                raise ValueError(
                    "CSV cells need strings, finite numbers, booleans or None."
                )
        if total > MAX_ARTIFACT_BYTES:
            raise ValueError("CSV content exceeds the artifact byte limit.")
    return "csv"
