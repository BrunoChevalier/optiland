"""Worker-only native prescription capture, including untouched coating media."""

from __future__ import annotations

import json
from types import MethodType

from optiland.coatings import FresnelCoating, ThinFilmCoating
from optiland.fileio.optiland_handler import OptilandEncoder
from optiland.materials.portability import capture_material
from optiland.optic import Optic
from optiland.physical_apertures import BaseAperture

from .glass_expert_records import MAX_RUN_BYTES
from .job_records import OpticSnapshot, check_cancelled


def _validate_behavior(optic):
    """Reject behavior that the native serializer cannot reconstruct."""
    # Existing owned tracing guard permits only the unchanged same-owner method.
    OpticSnapshot.capture(optic)
    components = [
        optic,
        optic.aperture,
        optic.apodization,
        optic.fields.field_definition,
        optic.ray_tracer,
        getattr(optic.ray_tracer, "ray_generator", None),
    ]
    for surface in optic.surfaces:
        interaction = surface.interaction_model
        coating = getattr(interaction, "coating", None)
        if isinstance(coating, FresnelCoating) and (
            coating.jones.material_pre is not coating.material_pre
            or coating.jones.material_post is not coating.material_post
        ):
            raise ValueError(
                "Detached Fresnel Jones media cannot be exported natively."
            )
        if (
            isinstance(coating, ThinFilmCoating)
            and coating.jones.stack is not coating.stack
        ):
            raise ValueError(
                "A detached thin-film Jones stack cannot be exported natively."
            )
        components.extend(
            (
                surface,
                surface.geometry,
                surface.geometry.cs,
                surface.aperture,
                interaction,
                getattr(interaction, "bsdf", None),
                coating,
                getattr(coating, "_jones", None),
                getattr(coating, "stack", None),
            )
        )
    components.extend(optic.solves.solves)
    seen = set()
    for component in components:
        if component is None:
            continue
        if id(component) in seen:
            continue
        seen.add(id(component))
        if isinstance(component, BaseAperture):
            components.extend(
                value
                for value in vars(component).values()
                if isinstance(value, BaseAperture)
            )
        if type(component).__name__ == "FileAperture":
            raise ValueError(
                "File-backed apertures require an embedded native replay adapter."
            )
        if not type(component).__module__.startswith("optiland."):
            raise ValueError(
                "A custom optical component needs a native replay adapter."
            )
        for name, value in vars(component).items():
            original = getattr(type(component), name, None)
            if (
                callable(original)
                and callable(value)
                and not (
                    isinstance(value, MethodType)
                    and value.__self__ is component
                    and value.__func__ is original
                )
            ):
                raise ValueError(
                    f"Patched {type(component).__name__}.{name} "
                    "cannot be exported natively."
                )


def _material_slots(optic, data):
    """Enumerate each supported material-bearing native serialization slot."""
    surfaces = data["surface_group"]["surfaces"]
    if len(surfaces) != len(optic.surfaces):
        raise ValueError("Native serialization changed the surface count.")
    for surface, record in zip(optic.surfaces, surfaces, strict=True):
        yield surface.material_post, record, "material_post"
        coating = getattr(surface.interaction_model, "coating", None)
        if isinstance(coating, (FresnelCoating, ThinFilmCoating)):
            stored = record["interaction_model"]["coating"]
            if isinstance(coating, ThinFilmCoating):
                # Surface updates rebind the live stack while the coating's
                # constructor attributes may retain their original materials.
                # Capture the state actually evaluated by JonesThinFilm.
                yield coating.stack.incident_material, stored, "material_pre"
                yield coating.stack.substrate_material, stored, "material_post"
                for layer, item in zip(
                    coating.stack.layers, stored["layers"], strict=True
                ):
                    yield layer.material, item, "material"
            else:
                yield coating.material_pre, stored, "material_pre"
                yield coating.material_post, stored, "material_post"


def _encode(data, cancelled):
    # Native lens format explicitly permits infinite plane radii/object distance.
    # Run settings and material payloads use separate strict finite JSON codecs.
    chunks, total = [], 0
    for chunk in OptilandEncoder(sort_keys=True, separators=(",", ":")).iterencode(
        data
    ):
        check_cancelled(cancelled)
        payload = chunk.encode("utf-8")
        total += len(payload)
        if total > MAX_RUN_BYTES:
            raise ValueError("Portable native prescription exceeds the 64 MiB budget.")
        chunks.append(payload)
    return b"".join(chunks)


def capture_portable_optic(optic, cancelled) -> bytes:
    """Copy live native materials without reopening files; prove native replay."""
    check_cancelled(cancelled)
    _validate_behavior(optic)
    data = optic.to_dict()
    payloads = []
    for material, parent, key in _material_slots(optic, data):
        check_cancelled(cancelled)
        payload = capture_material(material)
        payloads.append(payload)
        parent[key] = json.loads(payload)
    encoded = _encode(data, cancelled)
    restored = Optic.from_dict(json.loads(encoded))
    check_cancelled(cancelled)
    restored_data = restored.to_dict()
    actual = [
        capture_material(m) for m, _, _ in _material_slots(restored, restored_data)
    ]
    if actual != payloads or _encode(restored_data, cancelled) != encoded:
        raise ValueError(
            "Native replay changed the captured prescription or material state."
        )
    return encoded
