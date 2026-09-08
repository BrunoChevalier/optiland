"""A drawing boundary whose shape does not clip traced rays."""

from __future__ import annotations

from optiland.physical_apertures.base import BaseAperture


class UnclippedAperture(BaseAperture):
    """Retain an aperture's display shape while disabling ray clipping."""

    def __init__(self, aperture: BaseAperture):
        self.aperture = aperture

    @property
    def extent(self):
        return self.aperture.extent

    def contains(self, x, y):
        return self.aperture.contains(x, y)

    def clip(self, rays):
        """Leave ray intensity unchanged; this is a drawing boundary."""

    def scale(self, scale_factor):
        self.aperture.scale(scale_factor)

    def to_dict(self):
        return {"type": self.__class__.__name__, "aperture": self.aperture.to_dict()}

    @classmethod
    def from_dict(cls, data):
        return cls(BaseAperture.from_dict(data["aperture"]))
