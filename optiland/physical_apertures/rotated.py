"""Rotation of an existing physical aperture around a specified pivot."""

from __future__ import annotations

import math
from typing import Any

from optiland.physical_apertures.base import BaseAperture


class RotatedAperture(BaseAperture):
    """Rotate an aperture counterclockwise about +z.

    Args:
        aperture: The aperture to rotate.
        angle: Rotation angle in radians.
        center_x: Pivot x-coordinate, defaulting to the surface origin.
        center_y: Pivot y-coordinate, defaulting to the surface origin.
    """

    def __init__(
        self,
        aperture: BaseAperture,
        angle: float,
        center_x: float = 0.0,
        center_y: float = 0.0,
    ):
        self.aperture = aperture
        self.angle = angle
        self.center_x = center_x
        self.center_y = center_y

    @property
    def extent(self) -> tuple[float, float, float, float]:
        x0, x1, y0, y1 = self.aperture.extent
        c, s = math.cos(self.angle), math.sin(self.angle)
        corners = [
            (
                self.center_x + c * (x - self.center_x) - s * (y - self.center_y),
                self.center_y + s * (x - self.center_x) + c * (y - self.center_y),
            )
            for x in (x0, x1)
            for y in (y0, y1)
        ]
        return (
            min(x for x, _ in corners),
            max(x for x, _ in corners),
            min(y for _, y in corners),
            max(y for _, y in corners),
        )

    def contains(self, x: Any, y: Any) -> Any:
        c, s = math.cos(self.angle), math.sin(self.angle)
        x, y = x - self.center_x, y - self.center_y
        return self.aperture.contains(
            self.center_x + c * x + s * y, self.center_y - s * x + c * y
        )

    def scale(self, scale_factor: float) -> None:
        self.aperture.scale(scale_factor)
        self.center_x *= scale_factor
        self.center_y *= scale_factor

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "aperture": self.aperture.to_dict(),
            "angle": self.angle,
            "center_x": self.center_x,
            "center_y": self.center_y,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RotatedAperture:
        return cls(
            BaseAperture.from_dict(data["aperture"]),
            data["angle"],
            data.get("center_x", 0.0),
            data.get("center_y", 0.0),
        )
