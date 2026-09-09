"""In-memory refractive-index samples with bounded linear interpolation."""

from __future__ import annotations

import math
from typing import Any

import optiland.backend as be
from optiland.materials.base import BaseMaterial


class TabulatedMaterial(BaseMaterial):
    """Preserve measured or imported indices at their specified wavelengths.

    Args:
        wavelengths: Distinct positive wavelengths in micrometers.
        indices: Corresponding positive refractive indices.
        name: Optional source material name.

    Values between samples are linearly interpolated. Extrapolation is rejected;
    samples alone do not determine a dispersion formula outside their range.
    Sample wavelengths must remain distinct in the active backend precision.
    """

    def __init__(self, wavelengths: list[float], indices: list[float], name: str = ""):
        super().__init__()
        if len(wavelengths) != len(indices) or len(wavelengths) < 2:
            raise ValueError("Tabulated material requires at least two paired samples")
        pairs = sorted(zip(wavelengths, indices, strict=True))
        if any(not math.isfinite(v) or v <= 0 for pair in pairs for v in pair):
            raise ValueError("Wavelengths and indices must be finite and positive")
        if len(set(wavelengths)) != len(wavelengths):
            raise ValueError("Tabulated material wavelengths must be distinct")
        self.wavelengths = [float(w) for w, _ in pairs]
        self.indices = [float(n) for _, n in pairs]
        self.name = name

    def _calculate_n(self, wavelength: Any, **kwargs: Any) -> Any:
        wave = be.asarray(wavelength)
        if not be.all(be.isfinite(wave)):
            raise ValueError("Tabulated wavelengths must be finite")
        if be.any(wave < self.wavelengths[0]) or be.any(wave > self.wavelengths[-1]):
            raise ValueError(f"Wavelength outside tabulated range for {self.name!r}")
        samples = be.array(self.wavelengths)
        # Distinct Python floats can coincide after conversion to float32.
        # A zero-width interpolation interval would produce NaN indices.
        if be.any(samples[1:] <= samples[:-1]):
            raise ValueError(
                "Tabulated wavelengths must remain distinct at the active "
                "backend precision"
            )
        return be.atleast_1d(be.interp(wave, samples, be.array(self.indices)))

    def _calculate_k(self, wavelength: Any, **kwargs: Any) -> Any:
        return be.zeros_like(be.asarray(wavelength))

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "wavelengths": self.wavelengths,
            "indices": self.indices,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TabulatedMaterial:
        return cls(data["wavelengths"], data["indices"], data["name"])
