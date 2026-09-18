"""Frozen GlassExpert preflight and strategy on the existing calculation worker."""

from __future__ import annotations

import json
import math
import pickle
import time
from dataclasses import replace

import optiland.backend as be
from optiland.fileio.optiland_handler import OptilandEncoder
from optiland.geometries import ToroidalGeometry
from optiland.materials.portability import capture_material
from optiland.optimization import OptimizationProblem
from optiland.optimization.glass_expert_records import GlassExpertCancelled
from optiland.optimization.operand.operand import METRIC_DICT, operand_registry
from optiland.solves.curvature import CurvatureSolve
from optiland.solves.quick_focus import QuickFocusSolve
from optiland.solves.thickness import ThicknessSolve

from .glass_expert_portability import capture_portable_optic
from .glass_expert_proof import make_proof, native_fingerprint
from .glass_expert_records import (
    MAX_DIAGNOSTIC,
    MAX_RUN_BYTES,
    FrozenDefinition,
    GlassExpertJobResult,
    GlassExpertProgress,
    GlassExpertSetup,
    OperandValue,
    PreparedGlassExpertSetup,
    canonical_json,
)
from .job_records import check_cancelled
from .material_apply_tasks import validate_spectrum, validate_target
from .optimization_jobs import operand_arguments, variable_arguments
from .prepared_optic import PreparedOptic

_VARIABLE_TYPES = {
    "radius",
    "reciprocal_radius",
    "thickness",
    "conic",
    "asphere_coeff",
    "index",
    "tilt",
    "decenter",
    "polynomial_coeff",
    "chebyshev_coeff",
    "zernike_coeff",
    "forbes_qbfs_coeff",
    "forbes_qnormalslope_coeff",
    "forbes_q2d_coeff",
    "norm_radius",
    "nurbs_control_point",
    "nurbs_weight",
    "material",
}


def _scalar(value, label):
    values = be.to_numpy(value).reshape(-1)
    if len(values) != 1 or getattr(values.dtype, "kind", "") in {"b", "c"}:
        raise ValueError(f"{label} must be a finite real scalar.")
    result = float(values[0])
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite real scalar.")
    return result


def _geometry(optic, original=None):
    values = []
    for index, surface in enumerate(optic.surfaces):
        row = tuple(
            float(getattr(surface.geometry.cs, name))
            for name in ("x", "y", "z", "rx", "ry", "rz")
        )
        if any(math.isnan(value) for value in row) or (
            original is not None
            and any(
                math.isfinite(before) and not math.isfinite(after)
                for before, after in zip(original[index], row, strict=True)
            )
        ):
            raise ValueError("GlassExpert produced invalid surface placement.")
        for value in (surface.thickness, getattr(surface.geometry, "radius", None)):
            if value is not None and math.isnan(float(value)):
                raise ValueError("GlassExpert produced invalid surface geometry.")
        conic = getattr(surface.geometry, "k", None)
        if conic is not None and not math.isfinite(float(conic)):
            raise ValueError("GlassExpert produced invalid conic geometry.")
        values.append(row)
    return tuple(values)


def _bounds(definition):
    lower, upper = definition.get("min_val"), definition.get("max_val")
    for value in (lower, upper):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("Variable bounds must be finite numbers or unspecified.")
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("Variable lower bound must be less than its upper bound.")


def _check_variables(optic, setup):
    targets = {t.variable_id: t for t in setup.targets}
    material_rows = {t.surface_number for t in setup.targets}
    paths, found = set(), set()
    for record in setup.variables:
        definition = record.as_dict()
        kind, row = definition.get("type"), definition.get("surface_number")
        if (
            kind not in _VARIABLE_TYPES
            or type(row) is not int
            or not 0 <= row < len(optic.surfaces)
        ):
            raise ValueError(f"Unsupported variable or surface in {record.identifier}.")
        extras = {
            "radius": {"axis"},
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
        }.get(kind, set())
        allowed = {"type", "surface_number", "min_val", "max_val", "scaler"} | extras
        supplied = {key for key, value in definition.items() if value is not None}
        if supplied - allowed or definition.get("scaler") is not None:
            raise ValueError(
                "GlassExpert variables require supported native arguments/scalers."
            )
        _bounds(definition)
        path_kind = {
            "reciprocal_radius": "radius",
            "forbes_qbfs_coeff": "forbes_qnormalslope_coeff",
        }.get(kind, kind)
        # JSON turns tuple coefficient indices into lists. Use their canonical
        # content and only arguments that control this native variable behavior.
        path_arguments = {
            key: definition[key]
            for key in extras
            if key != "wavelength" and definition.get(key) is not None
        }
        if kind == "reciprocal_radius" and isinstance(
            optic.surfaces[row].geometry, ToroidalGeometry
        ):
            # The public reciprocal variable uses the ordinary radius updater,
            # which changes the toroid's Y radius. It has no axis argument.
            path_arguments["axis"] = "y"
        path = (row, path_kind, canonical_json(path_arguments))
        if path in paths:
            raise ValueError("Two variables control the same optical parameter.")
        paths.add(path)
        if kind == "material":
            target = targets.get(record.identifier)
            if target is None or target.surface_number != row:
                raise ValueError("Material variable and frozen target do not agree.")
            if (
                definition.get("min_val") is not None
                or definition.get("max_val") is not None
            ):
                raise ValueError("Material choices use a pool, not numerical bounds.")
            found.add(record.identifier)
        elif record.identifier in targets or (kind == "index" and row in material_rows):
            raise ValueError(
                "A material pool cannot overlap a continuous index variable."
            )
        for pickup in optic.pickups.pickups:
            if pickup.target_surface_idx == row and pickup.attr_type == path_kind:
                raise ValueError(
                    "A pickup controls this variable; remove its rule first."
                )
        for solve in optic.solves.solves:
            solve_kind = "radius" if isinstance(solve, CurvatureSolve) else "thickness"
            solve_row = (
                len(optic.surfaces) - 2
                if isinstance(solve, QuickFocusSolve)
                else getattr(solve, "surface_idx", None)
            )
            if isinstance(
                solve, (CurvatureSolve, ThicknessSolve, QuickFocusSolve)
            ) and (solve_row == row and solve_kind == path_kind):
                raise ValueError(
                    "A solve controls this variable; remove its rule first."
                )
    if found != set(targets) or len(setup.variables) <= len(targets):
        raise ValueError(
            "GlassExpert needs every material target and a continuous variable."
        )
    if not any(len(t.pool.candidates) > 1 for t in setup.targets):
        raise ValueError(
            "All material pools are fixed; use ordinary continuous optimization."
        )
    if any(
        1 < len(t.pool.candidates) < setup.settings.global_samples
        for t in setup.targets
    ):
        raise ValueError("Global sample count exceeds a nontrivial material pool.")


def _operands(problem, definitions, cancelled):
    rows = []
    for record, operand in zip(definitions, problem.operands, strict=True):
        check_cancelled(cancelled)
        value = _scalar(operand.value, record.identifier)
        target = (
            None
            if operand.target is None
            else _scalar(operand.target, "Operand target")
        )
        lower = (
            None
            if operand.min_val is None
            else _scalar(operand.min_val, "Operand lower bound")
        )
        upper = (
            None
            if operand.max_val is None
            else _scalar(operand.max_val, "Operand upper bound")
        )
        weight = _scalar(operand.effective_weight(), "Operand effective weight")
        delta = _scalar(operand.delta(), "Operand delta")
        rows.append(
            OperandValue(
                record.identifier,
                value,
                target,
                lower,
                upper,
                weight,
                delta,
                (lower is None or value >= lower) and (upper is None or value <= upper),
            )
        )
    if not any(row.effective_weight != 0 for row in rows):
        raise ValueError(
            "GlassExpert needs an objective with a nonzero effective weight."
        )
    return tuple(rows)


def _budget(value):
    if len(pickle.dumps(value, protocol=5)) > MAX_RUN_BYTES:
        raise ValueError("GlassExpert prepared data exceeds the 64 MiB run budget.")
    return value


def _prepare(setup, cancelled):
    if not isinstance(setup, GlassExpertSetup):
        raise ValueError("Expected an immutable GlassExpert setup.")
    check_cancelled(cancelled)
    setup.baseline.backend.apply()
    optic = setup.baseline.restore()
    original_surfaces = tuple(optic.surfaces)
    geometry = _geometry(optic)
    for target in setup.targets:
        validate_target(optic, target.surface_number)
    _check_variables(optic, setup)
    wavelengths = tuple(float(w.value) for w in optic.wavelengths.wavelengths)
    warnings, verified = [], set()
    for surface in optic.surfaces:
        check_cancelled(cancelled)
        # Reject custom evaluators/propagation before optimization trusts baseline.
        capture_material(surface.material_post)
        warnings.extend(validate_spectrum(optic, surface.material_post, cancelled))
    for target in setup.targets:
        for candidate in target.pool.candidates:
            if candidate.key not in verified:
                warnings.extend(
                    candidate.validate(
                        wavelengths, checkpoint=lambda: check_cancelled(cancelled)
                    )
                )
                verified.add(candidate.key)
    problem = OptimizationProblem()
    targets = {t.variable_id: t for t in setup.targets}
    resolved_variables = []
    # Operand targets are resolved BEFORE constructing even the typed material
    # variables, so an implicit target always describes the pristine reference.
    metadata = json.loads(setup.operand_metadata)
    for record in setup.operands:
        check_cancelled(cancelled)
        definition = record.as_dict()
        kind = definition.get("type")
        if (
            kind not in METRIC_DICT
            or operand_registry.get(kind) is not METRIC_DICT[kind]
        ):
            raise ValueError("Custom operands need a supported frozen replay adapter.")
        arguments = operand_arguments(optic, definition, metadata)
        canonical_json(
            {key: value for key, value in arguments.items() if key != "optic"}
        )
        problem.add_operand(
            kind,
            definition.get("target"),
            definition.get("min_val"),
            definition.get("max_val"),
            definition.get("weight", 1.0),
            arguments,
        )
    baseline_operands = _operands(problem, setup.operands, cancelled)
    resolved_operands = []
    for record, row, operand in zip(
        setup.operands, baseline_operands, problem.operands, strict=True
    ):
        definition = record.as_dict()
        definition.pop("input_data_str", None)
        definition["input_data"] = {
            key: value for key, value in operand.input_data.items() if key != "optic"
        }
        definition["target"] = row.target
        resolved_operands.append(
            FrozenDefinition.from_dict(record.identifier, "operand", definition)
        )
    baseline_merit = _scalar(problem.sum_squared(), "Baseline sum-squared merit")
    reference_data = OptilandEncoder(sort_keys=True).encode(optic.to_dict())
    for record in setup.variables:
        check_cancelled(cancelled)
        definition = record.as_dict()
        arguments = variable_arguments(optic, definition)
        resolved = {"type": definition["type"], **arguments}
        resolved_variables.append(
            FrozenDefinition.from_dict(record.identifier, "variable", resolved)
        )
        if definition["type"] == "material":
            arguments["glass_selection"] = targets[record.identifier].pool
        problem.add_variable(optic, definition["type"], **arguments)
        if definition["type"] != "material":
            _scalar(problem.variables[-1].variable.get_value(), record.identifier)
    if tuple(optic.surfaces) != original_surfaces:
        raise ValueError("Variable preparation changed surface identity or order.")
    if OptilandEncoder(sort_keys=True).encode(optic.to_dict()) != reference_data:
        raise ValueError("Variable construction changed the reference prescription.")
    portable, replay_error = None, None
    try:
        portable = capture_portable_optic(optic, cancelled)
    except (TypeError, ValueError, NotImplementedError) as error:
        replay_error = str(error)[:MAX_DIAGNOSTIC]
        warnings.append("Standalone replay unavailable: " + replay_error)
    prepared = _budget(
        PreparedGlassExpertSetup(
            setup,
            tuple(resolved_variables),
            tuple(resolved_operands),
            baseline_merit,
            baseline_operands,
            portable,
            replay_error,
            tuple(dict.fromkeys(warnings)),
            wavelengths,
        )
    )
    return prepared, optic, problem, original_surfaces, geometry


def _input(snapshot, parameters):
    value = parameters["setup"]
    setup = value.setup if isinstance(value, PreparedGlassExpertSetup) else value
    if not isinstance(setup, GlassExpertSetup) or (
        snapshot is not None and snapshot != setup.baseline
    ):
        raise ValueError("GlassExpert worker snapshot and setup disagree.")
    return setup


def prepare_setup(snapshot, parameters, progress, cancelled):
    """Evaluate the untouched reference and exact pools off the GUI thread."""
    check_cancelled(cancelled)
    progress("Preparing GlassExpert setup")
    prepared, *_ = _prepare(_input(snapshot, parameters), cancelled)
    check_cancelled(cancelled)
    return prepared


def run(snapshot, parameters, progress, cancelled):
    """Run the public core strategy; report a complete candidate for explicit review."""
    from optiland.optimization.optimizer.scipy.glass_expert import GlassExpert

    started = time.monotonic()
    setup = _input(snapshot, parameters)
    prepared, optic, problem, surfaces, geometry = _prepare(setup, cancelled)
    baseline_fingerprint = native_fingerprint(optic)
    last_report = -math.inf

    def report(update):
        nonlocal last_report
        check_cancelled(cancelled)
        now = time.monotonic()
        if now - last_report < 0.2:
            return
        last_report = now
        values, materials = [], []
        for record, variable in zip(setup.variables, original_variables, strict=True):
            if record.as_dict()["type"] == "material":
                value = variable.variable.get_value()
                from optiland.materials.properties import MaterialKey

                materials.append(
                    (
                        record.identifier,
                        value if isinstance(value, MaterialKey) else None,
                    )
                )
            else:
                values.append(
                    (
                        record.identifier,
                        _scalar(variable.variable.get_value(), record.identifier),
                    )
                )
        details = GlassExpertProgress(
            setup.run_id,
            setup.input_digest,
            update.phase,
            update.trials,
            update.evaluations,
            update.baseline_merit,
            update.best_merit,
            now - started,
            tuple(values),
            tuple(materials),
        )
        progress("GlassExpert: " + update.phase, details={"glass_expert": details})

    original_variables = tuple(problem.variables)
    optimizer = GlassExpert(problem)
    try:
        result = optimizer.run(
            **setup.settings.as_dict(),
            variable_ids=tuple(v.identifier for v in setup.variables),
            checkpoint=lambda: check_cancelled(cancelled),
            progress=report,
            plot=False,
            plot_glass_map=False,
            verbose=False,
            disp=False,
        )
        core_report = result.glass_expert
    except GlassExpertCancelled as error:
        check_cancelled(cancelled)
        if error.report is None:
            raise ValueError("GlassExpert cancellation supplied no report.") from error
        core_report = error.report
    check_cancelled(cancelled)
    if core_report.baseline is not None and not math.isclose(
        core_report.baseline.merit, prepared.baseline_merit, rel_tol=1e-9, abs_tol=1e-12
    ):
        raise ValueError("GlassExpert changed the frozen baseline objective.")
    candidate, native, mapping, candidate_operands = None, None, (), ()
    replay_error = prepared.replay_unavailable
    warnings = list(prepared.warnings) + list(core_report.warnings)
    if core_report.candidate_eligible:
        if core_report.baseline is None:
            raise ValueError(
                "An eligible GlassExpert result must include its reference baseline."
            )
        optic.updater.update()
        check_cancelled(cancelled)
        if len(optic.surfaces) != len(surfaces) or any(
            left is not right
            for left, right in zip(surfaces, optic.surfaces, strict=True)
        ):
            raise ValueError("GlassExpert changed surface identity or order.")
        _geometry(optic, geometry)
        state = core_report.candidate
        continuous = {v.variable_id: v.value for v in state.continuous}
        choices = {v.variable_id: v for v in state.materials}
        expected_continuous = {
            v.identifier for v in setup.variables if v.as_dict()["type"] != "material"
        }
        if set(continuous) != expected_continuous or len(continuous) != len(
            state.continuous
        ):
            raise ValueError(
                "GlassExpert returned incomplete continuous variable proof."
            )
        if set(choices) != {t.variable_id for t in setup.targets} or len(
            choices
        ) != len(state.materials):
            raise ValueError(
                "GlassExpert returned incomplete material membership proof."
            )
        for record, variable in zip(setup.variables, original_variables, strict=True):
            if record.identifier in continuous:
                value = _scalar(variable.variable.get_value(), record.identifier)
                definition = record.as_dict()
                if not math.isclose(
                    value, continuous[record.identifier], rel_tol=1e-10, abs_tol=1e-12
                ):
                    raise ValueError(
                        "GlassExpert reported a different continuous state."
                    )
                if (
                    definition.get("min_val") is not None
                    and value < definition["min_val"]
                ) or (
                    definition.get("max_val") is not None
                    and value > definition["max_val"]
                ):
                    raise ValueError(
                        "GlassExpert candidate violates a continuous bound."
                    )
        for target in setup.targets:
            choice = choices[target.variable_id]
            selected = target.pool.resolve(choice.key)
            actual = capture_material(surfaces[target.surface_number].material_post)
            if (
                selected.fingerprint != choice.fingerprint
                or selected.owned_data != actual
            ):
                raise ValueError(
                    "GlassExpert final material differs from its exact pool record."
                )
        for surface in optic.surfaces:
            capture_material(surface.material_post)
            warnings.extend(validate_spectrum(optic, surface.material_post, cancelled))
        candidate_operands = _operands(problem, prepared.resolved_operands, cancelled)
        merit = _scalar(problem.sum_squared(), "Final sum-squared merit")
        if not math.isclose(merit, state.merit, rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError("GlassExpert reported a different final merit.")
        candidate = PreparedOptic.capture(optic)
        mapping = tuple((i, i) for i in range(len(surfaces)))
        try:
            native = capture_portable_optic(optic, cancelled)
        except (TypeError, ValueError, NotImplementedError) as error:
            replay_error = str(error)[:MAX_DIAGNOSTIC]
            warnings.append("Candidate native export unavailable: " + replay_error)
    check_cancelled(cancelled)
    outcome = GlassExpertJobResult(
        setup.run_id,
        setup.input_digest,
        core_report,
        candidate,
        native,
        mapping,
        prepared.baseline_operands,
        candidate_operands,
        tuple(dict.fromkeys(warnings)),
        replay_error,
        time.monotonic() - started,
    )
    if candidate is not None:
        outcome = replace(
            outcome, proof=make_proof(outcome, optic, baseline_fingerprint)
        )
    outcome = _budget(outcome)
    check_cancelled(cancelled)
    return outcome
