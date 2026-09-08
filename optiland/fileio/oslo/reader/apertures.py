"""OSLO special apertures and aperture-group Boolean rules (reference pp. 49–50)."""

from __future__ import annotations

import math
from typing import Any

from optiland.physical_apertures import (
    BaseAperture,
    DifferenceAperture,
    EllipticalAperture,
    IntersectionAperture,
    PolygonAperture,
    RadialAperture,
    RectangularAperture,
    RotatedAperture,
    UnclippedAperture,
    UnionAperture,
)


def physical_aperture(
    data: dict[str, Any], scale: float, check: bool = True
) -> BaseAperture | None:
    """Build a surface aperture from ordinary and special aperture data."""
    radius = data.get("AP", 0.0)
    # Omit huge unchecked drawing outlines (typically the object plane).
    # A checked radius must retain its clipping boundary at any lens-unit scale.
    outer = (
        RadialAperture(r_max=radius * scale)
        if radius > 0 and (data.get("aperture_checked") or radius < 1e6)
        else None
    )
    groups: dict[int, list[tuple[BaseAperture, int]]] = {}
    for spec in data.get("special_apertures", {}).values():
        action = int(spec.get("AAC", 4))
        if action not in {2, 4}:
            continue  # The parser has already diagnosed hole/unknown actions.
        kind = int(spec.get("ATP", 1))
        if kind in {1, 2}:
            x0, x1, y0, y1 = [
                spec.get(k, 0.0) * scale for k in ("AX1", "AX2", "AY1", "AY2")
            ]
            if x0 >= x1 or y0 >= y1:
                raise ValueError("OSLO special aperture requires increasing bounds")
            if kind == 1:
                ap = EllipticalAperture(
                    (x1 - x0) / 2, (y1 - y0) / 2, (x1 + x0) / 2, (y1 + y0) / 2
                )
            else:
                ap = RectangularAperture(x0, x1, y0, y1)
            if spec.get("AAN", 0.0):
                # AAN rotates around the aperture centroid, not the surface
                # origin: OSLO Optics Reference, printed p. 105.
                # https://lambdares.com/hubfs/Support/support/OSLOOpticsReference_Sep21.pdf#page=105
                ap = RotatedAperture(
                    ap, math.radians(spec["AAN"]), (x1 + x0) / 2, (y1 + y0) / 2
                )
        elif kind in {3, 4}:
            ap = PolygonAperture(
                [spec.get(f"AVX{i}", 0.0) * scale for i in range(1, kind + 1)],
                [spec.get(f"AVY{i}", 0.0) * scale for i in range(1, kind + 1)],
            )
        else:
            raise ValueError(f"OSLO special aperture type {kind} is not supported")
        groups.setdefault(int(spec.get("AGN", 0)), []).append((ap, action))
    combined = None
    for members in groups.values():
        group = None
        for ap, action in members:
            if action == 4:
                group = ap if group is None else IntersectionAperture(group, ap)
        if group is None:
            group = RadialAperture(r_max=math.inf)
        for ap, action in members:
            if action == 2:
                group = DifferenceAperture(group, ap)
        combined = group if combined is None else UnionAperture(combined, group)
    if combined is None:
        return (
            UnclippedAperture(outer)
            if outer is not None and (not check or not data.get("aperture_checked"))
            else outer
        )
    if outer is not None and data.get("aperture_checked"):
        combined = IntersectionAperture(outer, combined)
    return combined if check else UnclippedAperture(combined)
