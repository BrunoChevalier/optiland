"""OSLO Data Model

Defines OsloDataModel, the shared intermediate representation used by both
the OSLO reader (parser -> model) and writer (optic -> model) paths.

Kramer Harrison, 2026
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OsloDiagnostic:
    """A source-located record of an OSLO command that was not imported."""

    command: str
    line: int
    surface: int
    message: str


@dataclass
class OsloDataModel:
    """Intermediate representation of an OSLO .len optical system.

    Attributes:
        name: System name from the LEN NEW command.
        scaling: EFL or scaling factor from the LEN NEW command.
        num_surfaces: Total number of surfaces from the LEN NEW command.
        aperture: Aperture configuration (e.g. EBR).
        fields: Field configuration (e.g. OBH, ANG).
        wavelengths: Wavelength data including values in microns and weights.
        surfaces: Mapping from surface index to a raw command dict.
        units: Units multiplier (UNI).
        notes: System notes (SNO*).
    """

    name: str | None = None
    scaling: float = 1.0
    num_surfaces: int = 0
    aperture: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    wavelengths: dict[str, Any] = field(
        default_factory=lambda: {"values": [], "weights": [], "primary_index": 0}
    )
    surfaces: dict[int, dict[str, Any]] = field(default_factory=dict)
    units: float = 1.0
    notes: dict[str, str] = field(default_factory=dict)
    diagnostics: list[OsloDiagnostic] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the data model as a plain dictionary.

        Returns:
            A plain dict representation suitable for use with
            ``OsloToOpticConverter``.
        """
        return asdict(self)
