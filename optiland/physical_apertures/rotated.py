"""Rotation of an existing physical aperture around the surface origin."""

from __future__ import annotations

import math
from typing import Any

from optiland.physical_apertures.base import BaseAperture


class RotatedAperture(BaseAperture):
    """Rotate an aperture counterclockwise about +z.

    Args:
        aperture: The aperture to rotate.
        angle: Rotation angle in radians.
    """

    def __init__(self, aperture: BaseAperture, angle: float):
        self.aperture = aperture
        self.angle = angle

    @property
    def extent(self) -> tuple[float, float, float, float]:
        x0, x1, y0, y1 = self.aperture.extent
        c, s = math.cos(self.angle), math.sin(self.angle)
        corners = [(c * x - s * y, s * x + c * y) for x in (x0, x1) for y in (y0, y1)]
        return (
            min(x for x, _ in corners),
            max(x for x, _ in corners),
            min(y for _, y in corners),
            max(y for _, y in corners),
        )

    def contains(self, x: Any, y: Any) -> Any:
        c, s = math.cos(self.angle), math.sin(self.angle)
        return self.aperture.contains(c * x + s * y, -s * x + c * y)

    def scale(self, scale_factor: float) -> None:
        self.aperture.scale(scale_factor)

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "aperture": self.aperture.to_dict(),
            "angle": self.angle,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RotatedAperture:
        return cls(BaseAperture.from_dict(data["aperture"]), data["angle"])
