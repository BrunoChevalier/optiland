"""Portable, bounded GlassExpert JSON reconstruction and core-only replay.

These files carry optical data, not document authority. No external pickle,
catalog lookup, GUI import or automatic file output is involved.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import optiland.backend as be
from optiland.fileio.optiland_handler import OptilandEncoder
from optiland.materials.portability import capture_material, restore_material
from optiland.materials.properties import MaterialKey
from optiland.optic import Optic
from optiland.optimization import OptimizationProblem
from optiland.optimization.material_candidates import (
    MaterialCandidate,
    MaterialPool,
    validate_material_spectrum,
)
from optiland.optimization.operand.operand import METRIC_DICT, operand_registry

if TYPE_CHECKING:
    from optiland.optimization.glass_expert_records import GlassExpertReport

MAX_REPLAY_BYTES = 64 * 1024 * 1024
FORMAT = "optiland-glass-expert"
VERSION = 1


def _check(checkpoint):
    if checkpoint is not None:
        checkpoint()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError(f"Nonfinite manifest number: {value}")


def _json(data, *, native=False):
    if type(data) is not bytes or not data or len(data) > MAX_REPLAY_BYTES:
        raise ValueError("Expected nonempty JSON bytes within the 64 MiB limit.")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            **({} if native else {"parse_constant": _nonfinite}),
        )
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid replay JSON.") from error
    if not isinstance(value, dict):
        raise ValueError("Replay JSON must contain an object.")
    return value


def _encode(value):
    try:
        result = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Replay metadata must be finite plain JSON.") from error
    if len(result) > MAX_REPLAY_BYTES:
        raise ValueError("Replay JSON exceeds the 64 MiB limit.")
    return result


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"Invalid {label} fields.")


def _number(value, label, *, positive=False):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or (positive and value <= 0)
    ):
        raise ValueError(
            f"{label} must be a finite {'positive ' if positive else ''}number."
        )


def _identifier(value):
    if type(value) is not str or not value.strip() or len(value) > 200:
        raise ValueError("Replay row IDs must contain 1–200 characters.")


def _rows(value, cap, label):
    if type(value) is not list or not 1 <= len(value) <= cap:
        raise ValueError(f"Replay {label} needs 1–{cap} rows.")
    result = []
    for row in value:
        _fields(row, ("id", "definition"), label)
        _identifier(row["id"])
        if not isinstance(row["definition"], dict):
            raise ValueError("Replay definitions must be objects.")
        result.append((row["id"], _encode(row["definition"])))
    return tuple(result)


@dataclass(frozen=True)
class ReplayInputs:
    """Validated portable bytes; each payload access returns a fresh object."""

    data: bytes
    digest: str

    @property
    def payload(self):
        return _json(self.data)["payload"]


@dataclass(frozen=True)
class ReplayResult:
    """Core report and detached optic, with no GUI publication capability."""

    report: GlassExpertReport
    optic: Optic


def encode_glass_expert_manifest(payload: dict) -> bytes:
    """Encode one current-version payload; decoding validates its structure."""
    encoded = _encode(payload)
    data = _encode(
        {
            "format": FORMAT,
            "version": VERSION,
            "digest": hashlib.sha256(encoded).hexdigest(),
            "payload": payload,
        }
    )
    decode_glass_expert_manifest(data)
    return data


def decode_glass_expert_manifest(data: bytes) -> ReplayInputs:
    """Validate finite metadata and exact embedded pools, without optical work."""
    value = _json(data)
    _fields(value, ("format", "version", "digest", "payload"), "manifest")
    if (
        value["format"] != FORMAT
        or type(value["version"]) is not int
        or value["version"] != VERSION
    ):
        raise ValueError("Unsupported GlassExpert manifest format/version.")
    payload = value["payload"]
    _fields(
        payload,
        (
            "baseline",
            "backend",
            "variables",
            "operands",
            "resolved_variables",
            "resolved_operands",
            "targets",
            "candidates",
            "settings",
            "operand_metadata",
            "baseline_merit",
            "baseline_operands",
            "result",
            "warnings",
            "replay_unavailable",
        ),
        "payload",
    )
    if value["digest"] != hashlib.sha256(_encode(payload)).hexdigest():
        raise ValueError("GlassExpert manifest digest mismatch.")
    backend = payload["backend"]
    _fields(backend, ("name", "device", "precision"), "backend")
    if (
        backend["name"] not in ("numpy", "torch")
        or type(backend["device"]) is not str
        or len(backend["device"]) > 100
        or type(backend["precision"]) is not int
        or backend["precision"] not in (32, 64)
    ):
        raise ValueError("Unsupported replay backend settings.")
    settings = payload["settings"]
    integer_ranges = {
        "global_samples": (2, 10000),
        "num_neighbours": (1, 10000),
        "maxiter": (1, 1000000),
        "seed": (0, 2**32 - 1),
        "report_limit": (1, 1000),
    }
    _fields(settings, (*integer_ranges, "tol", "nd_scale", "vd_scale"), "settings")
    for name, (low, high) in integer_ranges.items():
        if type(settings[name]) is not int or not low <= settings[name] <= high:
            raise ValueError(f"Replay {name} is outside its limits.")
    for name in ("tol", "nd_scale", "vd_scale"):
        _number(settings[name], name, positive=True)
    sections = {
        name: _rows(payload[name], cap, name)
        for name, cap in (
            ("variables", 256),
            ("operands", 10000),
            ("resolved_variables", 256),
            ("resolved_operands", 10000),
        )
    }
    ids = [row[0] for row in (*sections["variables"], *sections["operands"])]
    if len(set(ids)) != len(ids):
        raise ValueError("Replay descriptor IDs must be distinct.")
    for name in ("variables", "operands"):
        if tuple(row[0] for row in sections[name]) != tuple(
            row[0] for row in sections["resolved_" + name]
        ):
            raise ValueError("Resolved replay descriptor IDs/order changed.")
    if (
        type(payload["candidates"]) is not list
        or not 1 <= len(payload["candidates"]) <= 10000
    ):
        raise ValueError("Replay needs 1–10,000 unique candidates.")
    candidates = {}
    stamps = {}
    for item in payload["candidates"]:
        _fields(item, ("record", "fingerprint"), "candidate")
        candidate = MaterialCandidate.from_dict(item["record"])
        if candidate.fingerprint != item["fingerprint"] or candidate.key in candidates:
            raise ValueError("Duplicate candidate or material fingerprint mismatch.")
        if (
            stamps.setdefault(candidate.stamp.source_id, candidate.stamp)
            != candidate.stamp
        ):
            raise ValueError("Conflicting captured source generations.")
        candidates[candidate.key] = candidate
    if type(payload["targets"]) is not list or not 1 <= len(payload["targets"]) <= 32:
        raise ValueError("Replay needs 1–32 material targets.")
    target_ids, target_rows, used = set(), set(), set()
    for target in payload["targets"]:
        _fields(target, ("variable_id", "surface_number", "keys"), "target")
        if (
            target["variable_id"] not in dict(sections["variables"])
            or target["variable_id"] in target_ids
            or type(target["surface_number"]) is not int
            or target["surface_number"] < 1
            or target["surface_number"] in target_rows
        ):
            raise ValueError("Invalid or duplicate replay material target.")
        if type(target["keys"]) is not list or not 1 <= len(target["keys"]) <= 10000:
            raise ValueError("Replay target pool must be nonempty and bounded.")
        try:
            keys = tuple(MaterialKey(**key) for key in target["keys"])
            MaterialPool(tuple(candidates[key] for key in keys))
        except (KeyError, TypeError) as error:
            raise ValueError("Replay pool references an absent candidate.") from error
        target_ids.add(target["variable_id"])
        target_rows.add(target["surface_number"])
        used.update(keys)
    if used != set(candidates):
        raise ValueError("Replay contains unused candidate records.")
    if not isinstance(payload["operand_metadata"], dict):
        raise ValueError("Replay operand metadata must be an object.")
    _number(payload["baseline_merit"], "Baseline merit")
    if payload["baseline_merit"] < 0:
        raise ValueError("Sum-squared merit cannot be negative.")
    if (
        type(payload["baseline_operands"]) is not list
        or len(payload["baseline_operands"]) > 10000
    ):
        raise ValueError("Invalid replay operand inspection rows.")
    if (
        type(payload["warnings"]) is not list
        or len(payload["warnings"]) > 1001
        or any(type(w) is not str or len(w) > 2048 for w in payload["warnings"])
    ):
        raise ValueError("Replay warnings exceed their limits.")
    if payload["replay_unavailable"] is not None and (
        type(payload["replay_unavailable"]) is not str
        or len(payload["replay_unavailable"]) > 2048
    ):
        raise ValueError("Invalid native replay diagnostic.")
    baseline = payload["baseline"]
    if baseline is not None:
        _fields(baseline, ("native", "digest"), "native baseline")
        if (
            type(baseline["native"]) is not str
            or hashlib.sha256(baseline["native"].encode("utf-8")).hexdigest()
            != baseline["digest"]
        ):
            raise ValueError("Native baseline digest mismatch.")
        _json(baseline["native"].encode("utf-8"), native=True)
    result = payload["result"]
    if result is not None:
        _fields(result, ("report", "candidate_operands"), "saved result")
        if (
            not isinstance(result["report"], dict)
            or type(result["candidate_operands"]) is not list
            or len(result["candidate_operands"]) > 10000
            or type(result["report"].get("trials")) is not list
            or len(result["report"].get("trials", ())) > 1000
        ):
            raise ValueError("Saved replay result exceeds its limits.")
    return ReplayInputs(_encode(value), value["digest"])


def _native_materials(data):
    """Validate every material-bearing slot before a native constructor runs."""
    surfaces = data["surface_group"]["surfaces"]
    if type(surfaces) is not list or not 2 <= len(surfaces) <= 10000:
        raise ValueError("Native replay surface count is invalid.")
    for surface in surfaces:
        if surface.get("type") != "ObjectSurface" and not isinstance(
            surface.get("interaction_model"), dict
        ):
            raise ValueError("Native replay requires explicit surface interactions.")
        restore_material(_encode(surface["material_post"]))
        coating = (surface.get("interaction_model") or {}).get("coating")
        if coating and coating.get("type") in ("FresnelCoating", "ThinFilmCoating"):
            for name in ("material_pre", "material_post"):
                restore_material(_encode(coating[name]))
            for layer in coating.get("layers", ()):
                restore_material(_encode(layer["material"]))


def _builtin_registry_records(data):
    """Reject plugin constructors before native deserialization is invoked."""
    from optiland.aperture import BaseSystemAperture
    from optiland.apodization import BaseApodization
    from optiland.coatings import BaseCoating
    from optiland.fields.field_types.base import BaseFieldDefinition
    from optiland.geometries.base import BaseGeometry
    from optiland.interactions.base import BaseInteractionModel
    from optiland.physical_apertures import BaseAperture
    from optiland.scatter import BaseBSDF
    from optiland.solves.base import BaseSolve
    from optiland.surfaces.standard_surface import Surface

    registries = (
        BaseSystemAperture._registry,
        BaseApodization._registry,
        BaseBSDF._registry,
        BaseCoating._registry,
        BaseFieldDefinition._registry,
        BaseGeometry._registry,
        BaseInteractionModel._registry,
        BaseAperture._registry,
        Surface._registry,
        BaseSolve._registry,
    )
    for surface in data["surface_group"]["surfaces"]:
        if (
            surface.get("type") not in {"Surface", *Surface._registry}
            or surface.get("geometry", {}).get("type") not in BaseGeometry._registry
        ):
            raise ValueError("Unknown native surface or geometry constructor.")
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            field_type = node.get("field_type")
            if isinstance(field_type, str) and any(
                (name == field_type or cls.__name__ == field_type)
                and not cls.__module__.startswith("optiland.")
                for name, cls in BaseFieldDefinition._registry.items()
            ):
                raise ValueError(
                    "Custom field definitions cannot be replayed natively."
                )
            name = node.get("type")
            if isinstance(name, str):
                if name == "FileAperture":
                    raise ValueError(
                        "File-backed apertures cannot be replayed from embedded data."
                    )
                for registry in registries:
                    cls = registry.get(name)
                    if cls is not None and not cls.__module__.startswith("optiland."):
                        raise ValueError(
                            "Custom native replay constructors are unsupported."
                        )
            # Metadata is descriptive only and is never a constructor request.
            stack.extend(value for key, value in node.items() if key != "metadata")
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, float) and math.isnan(node):
            raise ValueError("Native replay cannot contain NaN optical data.")


def restore_replay_baseline(inputs: ReplayInputs, *, checkpoint=None) -> Optic:
    """Restore only embedded native data on the explicitly recorded backend."""
    inputs = decode_glass_expert_manifest(inputs.data)
    payload = inputs.payload
    if payload["baseline"] is None:
        raise ValueError(
            payload["replay_unavailable"] or "Portable baseline unavailable."
        )
    _check(checkpoint)
    backend = payload["backend"]
    be.set_backend(backend["name"])
    if backend["name"] == "torch":
        be.set_device(backend["device"])
        be.set_precision(f"float{backend['precision']}")
    data = _json(payload["baseline"]["native"].encode("utf-8"), native=True)
    _native_materials(data)
    _builtin_registry_records(data)
    _check(checkpoint)
    optic = Optic.from_dict(data)
    _check(checkpoint)
    return optic


def _wavelength(optic, value):
    if isinstance(value, str):
        value = value.strip().strip("'\"")
        value = (
            optic.wavelengths.primary_wavelength.value
            if value == "primary"
            else json.loads(value)
        )
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError("Replay needs one explicit wavelength.")
        value = value[0]
    _number(value, "Wavelength", positive=True)
    return float(value)


def _variable_paths(optic, rows, targets):
    from optiland.geometries import ToroidalGeometry
    from optiland.solves.curvature import CurvatureSolve
    from optiland.solves.quick_focus import QuickFocusSolve
    from optiland.solves.thickness import ThicknessSolve

    extras = {
        "radius": {"axis"},
        "reciprocal_radius": set(),
        "thickness": set(),
        "conic": set(),
        "norm_radius": set(),
        "material": set(),
        "index": {"wavelength"},
        "tilt": {"axis"},
        "decenter": {"axis"},
        "asphere_coeff": {"coeff_number"},
        "forbes_qbfs_coeff": {"coeff_number"},
        "forbes_qnormalslope_coeff": {"coeff_number"},
        "forbes_q2d_coeff": {"coeff_number"},
        "polynomial_coeff": {"coeff_index"},
        "chebyshev_coeff": {"coeff_index"},
        "zernike_coeff": {"coeff_index"},
        "nurbs_control_point": {"coeff_index"},
        "nurbs_weight": {"coeff_index"},
    }
    paths, found = set(), set()
    for record in rows:
        definition = record["definition"]
        kind, row = definition.get("type"), definition.get("surface_number")
        if (
            kind not in extras
            or type(row) is not int
            or not 0 <= row < len(optic.surfaces)
        ):
            raise ValueError("Unsupported replay variable/surface.")
        supplied = {key for key, value in definition.items() if value is not None}
        if supplied - ({"type", "surface_number", "min_val", "max_val"} | extras[kind]):
            raise ValueError("Unsupported replay variable arguments.")
        path_kind = {
            "reciprocal_radius": "radius",
            "forbes_qbfs_coeff": "forbes_qnormalslope_coeff",
        }.get(kind, kind)
        args = {
            key: definition[key]
            for key in extras[kind]
            if key != "wavelength" and definition.get(key) is not None
        }
        if kind == "reciprocal_radius" and isinstance(
            optic.surfaces[row].geometry, ToroidalGeometry
        ):
            args["axis"] = "y"
        path = row, path_kind, _encode(args)
        if path in paths:
            raise ValueError("Replay variables control the same optical parameter.")
        paths.add(path)
        low, high = definition.get("min_val"), definition.get("max_val")
        for bound in (low, high):
            if bound is not None:
                _number(bound, "Variable bound")
        if low is not None and high is not None and low >= high:
            raise ValueError("Invalid replay variable bounds.")
        if kind == "material":
            if (
                record["id"] not in targets
                or targets[record["id"]]["surface_number"] != row
                or not 0 < row < len(optic.surfaces) - 1
                or low is not None
                or high is not None
            ):
                raise ValueError("Replay material variable and target disagree.")
            found.add(record["id"])
        elif record["id"] in targets or (
            kind == "index" and row in {t["surface_number"] for t in targets.values()}
        ):
            raise ValueError("Replay continuous variable overlaps a material target.")
        if any(
            p.target_surface_idx == row and p.attr_type in {path_kind, "material"}
            for p in optic.pickups.pickups
        ):
            raise ValueError("A pickup controls a replay target.")
        for solve in optic.solves.solves:
            solve_row = (
                len(optic.surfaces) - 2
                if isinstance(solve, QuickFocusSolve)
                else getattr(solve, "surface_idx", None)
            )
            solve_kind = "radius" if isinstance(solve, CurvatureSolve) else "thickness"
            if (
                isinstance(solve, (CurvatureSolve, ThicknessSolve, QuickFocusSolve))
                and solve_row == row
                and solve_kind == path_kind
            ):
                raise ValueError("A solve controls a replay variable.")
    if found != set(targets) or len(rows) <= len(targets):
        raise ValueError("Replay needs each material target and a continuous variable.")


def replay_glass_expert(
    inputs: ReplayInputs, *, checkpoint=None, progress=None
) -> ReplayResult:
    """Run resolved public core definitions; never open catalogs or write files."""
    from optiland.optimization.optimizer.scipy.glass_expert import GlassExpert

    inputs = decode_glass_expert_manifest(inputs.data)
    payload = inputs.payload
    optic = restore_replay_baseline(inputs, checkpoint=checkpoint)
    before = OptilandEncoder(sort_keys=True).encode(optic.to_dict())
    problem = OptimizationProblem()
    candidates = {
        c.key: c
        for c in (
            MaterialCandidate.from_dict(row["record"]) for row in payload["candidates"]
        )
    }
    wavelengths = tuple(float(w.value) for w in optic.wavelengths.wavelengths)
    warnings = list(payload["warnings"])
    for surface in optic.surfaces:
        warnings.extend(
            validate_material_spectrum(
                surface.material_post, wavelengths, checkpoint=checkpoint
            )
        )
    for candidate in candidates.values():
        candidate.validate(wavelengths, checkpoint=checkpoint)
    pools = {
        target["variable_id"]: MaterialPool(
            tuple(candidates[MaterialKey(**key)] for key in target["keys"])
        )
        for target in payload["targets"]
    }
    targets = {row["variable_id"]: row for row in payload["targets"]}
    _variable_paths(optic, payload["resolved_variables"], targets)
    for original, row in zip(
        payload["operands"], payload["resolved_operands"], strict=True
    ):
        _check(checkpoint)
        definition = dict(row["definition"])
        kind = definition.pop("type", None)
        if (
            kind not in METRIC_DICT
            or operand_registry.get(kind) is not METRIC_DICT[kind]
        ):
            raise ValueError("Replay requires builtin operands.")
        raw = original["definition"]
        allowed = {"target", "min_val", "max_val", "weight", "input_data", "category"}
        arguments = definition.get("input_data")
        if (
            not isinstance(arguments, dict)
            or "optic" in arguments
            or "input_data_str" in definition
            or set(definition) - allowed
            or set(raw) - allowed - {"type", "input_data_str"}
        ):
            raise ValueError("Replay requires explicit resolved operand inputs.")
        # Native operand dialogs retain a presentation category. It is not an
        # optical argument, but its exact bounded value must survive resolution.
        if ("category" in raw) != ("category" in definition) or raw.get(
            "category"
        ) != definition.get("category"):
            raise ValueError("Resolved operand category differs from its original.")
        if "category" in definition and (
            not isinstance(definition["category"], str)
            or not definition["category"].strip()
            or len(definition["category"]) > 200
        ):
            raise ValueError(
                "Operand category must be a nonempty string up to 200 characters."
            )
        if raw.get("type") != kind:
            raise ValueError("Resolved operand changed its original metric.")
        original_arguments = raw.get("input_data")
        if not isinstance(original_arguments, dict):
            original_arguments = json.loads(raw.get("input_data_str") or "{}")
        if not isinstance(original_arguments, dict) or "optic" in original_arguments:
            raise ValueError("Replay operand inputs must be plain explicit data.")
        original_arguments = dict(original_arguments)
        if "wavelength" in original_arguments or "wavelength" in payload[
            "operand_metadata"
        ].get(kind, {}):
            original_arguments["wavelength"] = _wavelength(
                optic, original_arguments.get("wavelength", "primary")
            )
        if arguments != original_arguments or any(
            definition.get(key, default) != raw.get(key, default)
            for key, default in (("weight", 1.0), ("min_val", None), ("max_val", None))
        ):
            raise ValueError("Resolved operand changed its original inputs or weights.")
        problem.add_operand(
            kind,
            raw.get("target"),
            definition.get("min_val"),
            definition.get("max_val"),
            definition.get("weight", 1.0),
            {**arguments, "optic": optic},
        )
        target = problem.operands[-1].target
        if (None if target is None else float(target)) != definition.get("target"):
            raise ValueError(
                "Resolved operand target differs from the original reference."
            )
    merit = float(problem.sum_squared())
    if not math.isfinite(merit) or not math.isclose(
        merit, payload["baseline_merit"], rel_tol=1e-9, abs_tol=1e-12
    ):
        raise ValueError("Replay baseline differs from the frozen objective.")
    ids = []
    for original, row in zip(
        payload["variables"], payload["resolved_variables"], strict=True
    ):
        _check(checkpoint)
        definition = dict(row["definition"])
        kind = definition.pop("type", None)
        raw = {
            key: value
            for key, value in original["definition"].items()
            if value is not None
        }
        if "wavelength" in raw:
            raw["wavelength"] = _wavelength(optic, raw["wavelength"])
        if {"type": kind, **definition} != raw:
            raise ValueError("Resolved variable differs from its original definition.")
        if kind == "material":
            definition["glass_selection"] = pools[row["id"]]
        problem.add_variable(optic, kind, **definition)
        ids.append(row["id"])
    if OptilandEncoder(sort_keys=True).encode(optic.to_dict()) != before:
        raise ValueError("Replay variable construction changed the reference.")
    result = GlassExpert(problem).run(
        **payload["settings"],
        variable_ids=tuple(ids),
        checkpoint=checkpoint,
        progress=progress,
        plot=False,
        plot_glass_map=False,
        verbose=False,
        disp=False,
    )
    _check(checkpoint)
    # Every material remains a supported embedded native codec for optional
    # caller-owned native output; no registry name/file reference is introduced.
    for surface in optic.surfaces:
        capture_material(surface.material_post)
    warnings = list(dict.fromkeys((*warnings, *result.glass_expert.warnings)))
    messages = tuple(w[:2048] for w in warnings[:1000])
    if len(warnings) > 1000:
        messages += (f"{len(warnings) - 1000} additional replay diagnostics omitted.",)
    return ReplayResult(replace(result.glass_expert, warnings=messages), optic)
