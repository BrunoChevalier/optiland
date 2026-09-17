"""Owned material replacement and optical preflight on the shared worker."""

from __future__ import annotations

import math
from dataclasses import dataclass

import optiland.backend as be
from optiland.materials import DataMaterial, IdealMaterial
from optiland.materials.portability import capture_material, restore_material
from optiland.optic.optic_updater import OpticUpdater
from optiland.pickup import Pickup
from optiland.solves.curvature import (
    ChiefRayAngleCurvatureSolve,
    MarginalRayAngleCurvatureSolve,
)
from optiland.solves.quick_focus import QuickFocusSolve
from optiland.solves.thickness import (
    ChiefRayHeightThicknessSolve,
    MarginalRayHeightThicknessSolve,
)

from .job_records import OpticSnapshot, check_cancelled
from .prepared_optic import PreparedOptic


@dataclass(frozen=True)
class PreparedMaterialChange:
    """Detached result, identity proof and native persistence snapshot."""

    outcome: str
    candidate: PreparedOptic | None
    native_snapshot: OpticSnapshot | None
    row_mapping: tuple[tuple[int, int], ...]
    affected_rows: tuple[int, ...]
    warnings: tuple[str, ...]


def validate_target(optic, row):
    """Check cheap identity/type metadata without evaluating material functions."""
    if not 0 <= row < len(optic.surfaces):
        raise ValueError("The selected surface is no longer available.")
    if row == len(optic.surfaces) - 1:
        raise ValueError("The image surface has no editable outgoing medium.")
    surface = optic.surfaces[row]
    if getattr(surface.interaction_model, "is_reflective", False):
        raise ValueError("A reflective surface has no transmissive material target.")
    if type(optic.updater) is not OpticUpdater or any(
        callable(value) for value in vars(optic.updater).values()
    ):
        raise ValueError("A custom updater needs an owned preparation adapter.")
    for pickup in optic.pickups.pickups:
        if (
            type(pickup) is not Pickup
            or pickup.optic is not optic
            or pickup.source_optic is not optic
            or any(
                name in vars(pickup) for name in ("apply", "_get_value", "_set_value")
            )
        ):
            raise ValueError(
                "A custom or external-optic pickup is unsupported for Apply."
            )
        if pickup.attr_type not in {"radius", "conic", "thickness"}:
            if pickup.target_surface_idx == row and "material" in pickup.attr_type:
                raise ValueError(
                    "A pickup controls the target material. "
                    "Remove that rule before Apply."
                )
            raise ValueError(
                f"Pickup write path {pickup.attr_type!r} is not supported for Apply."
            )
    supported = {
        ChiefRayAngleCurvatureSolve,
        MarginalRayAngleCurvatureSolve,
        QuickFocusSolve,
        ChiefRayHeightThicknessSolve,
        MarginalRayHeightThicknessSolve,
    }
    for solve in optic.solves.solves:
        if (
            type(solve) not in supported
            or getattr(solve, "optic", None) is not optic
            or any(callable(value) for value in vars(solve).values())
        ):
            raise ValueError(
                "A custom or external-optic solve is unsupported for Apply."
            )


def validate_spectrum(optic, material, cancelled):
    """Reject unsupported known ranges even when the retained policy is clamp."""
    wavelengths = tuple(float(w.value) for w in optic.wavelengths.wavelengths)
    if not wavelengths or any(not math.isfinite(w) or w <= 0 for w in wavelengths):
        raise ValueError(
            "Configure finite positive wavelengths before applying a material."
        )
    warnings = []
    for quantity in ("n", "k"):
        support = material.spectral_range(quantity)
        if support is not None and any(
            w < support[0] or w > support[1] for w in wavelengths
        ):
            raise ValueError(
                f"A configured wavelength lies outside known {quantity} "
                f"support {support} µm."
            )
        if support is None:
            if (
                quantity == "k"
                and isinstance(material, DataMaterial)
                and material.definition.extinction is None
            ):
                warnings.append(
                    "Extinction data are unknown; the native zero-k fallback was used."
                )
            else:
                warnings.append(
                    f"{quantity} support is analytic."
                    if isinstance(material, IdealMaterial)
                    else (
                        f"{quantity} support is unknown; "
                        "configured wavelengths were evaluated."
                    )
                )
        for wavelength in wavelengths:
            check_cancelled(cancelled)
            values = be.to_numpy(getattr(material, quantity)(wavelength)).reshape(-1)
            if (
                len(values) != 1
                or not math.isfinite(float(values[0]))
                or (values[0] <= 0 if quantity == "n" else values[0] < 0)
            ):
                raise ValueError(
                    f"Material {quantity} is invalid at {wavelength:g} µm."
                )
    return tuple(warnings)


def prepare_material_change(snapshot, parameters, progress, cancelled):
    """Prepare the exact medium-after assignment without touching the live optic."""
    check_cancelled(cancelled)
    snapshot.backend.apply()
    progress("Validating selected material and surface")
    optic = snapshot.restore()
    row = parameters["row"]
    validate_target(optic, row)
    surfaces = tuple(optic.surfaces)
    material = restore_material(parameters["payload"])
    expected_payload = capture_material(material)
    warnings = validate_spectrum(optic, material, cancelled)
    previous = surfaces[row].material_post
    # A file lookup and its owned definition are different persisted identities.
    # Never use a converted capture or just n_d/V_d to identify a no-op.
    if type(previous) is type(material) and previous.to_dict() == material.to_dict():
        return PreparedMaterialChange("unchanged", None, None, (), (), warnings)
    finite_before = tuple(
        tuple(
            math.isfinite(float(getattr(surface.geometry.cs, name)))
            for name in ("x", "y", "z", "rx", "ry", "rz")
        )
        for surface in surfaces
    )
    optic.updater.set_material(material, row)
    progress("Updating solves and pickups")
    check_cancelled(cancelled)
    optic.updater.update()
    check_cancelled(cancelled)
    if len(optic.surfaces) != len(surfaces) or any(
        a is not b for a, b in zip(surfaces, optic.surfaces, strict=True)
    ):
        raise ValueError("Material preparation changed surface identity or order.")
    if capture_material(surfaces[row].material_post) != expected_payload:
        raise ValueError("A dependency overwrote the requested material.")
    for surface, finite in zip(surfaces, finite_before, strict=True):
        for value in (surface.thickness, getattr(surface.geometry, "radius", None)):
            if value is not None and math.isnan(float(value)):
                raise ValueError("A dependency produced invalid surface geometry.")
        conic = getattr(surface.geometry, "k", None)
        if conic is not None and not math.isfinite(float(conic)):
            raise ValueError("A dependency produced invalid surface geometry.")
        for name, was_finite in zip(
            ("x", "y", "z", "rx", "ry", "rz"), finite, strict=True
        ):
            if was_finite and not math.isfinite(
                float(getattr(surface.geometry.cs, name))
            ):
                raise ValueError("A dependency produced nonfinite surface placement.")
    validate_spectrum(optic, surfaces[row].material_post, cancelled)
    check_cancelled(cancelled)
    return PreparedMaterialChange(
        "prepared",
        PreparedOptic.capture(optic),
        OpticSnapshot.capture(optic),
        tuple((i, i) for i in range(len(surfaces))),
        tuple(range(len(surfaces)))
        if optic.pickups or optic.solves
        else (row, row + 1),
        warnings,
    )
