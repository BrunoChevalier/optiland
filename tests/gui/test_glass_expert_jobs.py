"""Exact frozen GlassExpert preflight, native replay and shared-worker execution."""

from __future__ import annotations

import json
import threading
from dataclasses import FrozenInstanceError, replace

import pytest

from optiland.materials import DataMaterial
from optiland.materials.portability import capture_material
from optiland.materials.properties import MaterialKey, SourceStamp, describe_material
from optiland.optimization.material_candidates import (
    MaterialPool,
    prepare_material_candidate,
)
from optiland_gui.services import glass_expert_jobs as jobs
from optiland_gui.services.glass_expert_portability import capture_portable_optic
from optiland_gui.services.glass_expert_records import (
    FrozenDefinition,
    GlassExpertSettings,
    GlassExpertSetup,
    MaterialTarget,
)
from optiland_gui.services.job_records import CalculationCancelled
from optiland_gui.services.prepared_optic import PreparedOptic


def make_pool(source="owned", count=2):
    records = []
    for index in range(count):
        material = DataMaterial.from_coefficients(
            "formula 5",
            [1.48 + index * 0.05, 0.008, -2],
            name="Same display glass",
            wavelength_range=(0.35, 1.1),
        )
        summary = describe_material(
            material, MaterialKey(source, "catalog", str(index))
        )
        records.append(
            prepare_material_candidate(
                summary, SourceStamp(source, "1"), wavelengths_um=(0.55,)
            )
        )
    return MaterialPool(tuple(records))


def make_setup(optic, *, target=1, pool=None, variables=None, operands=None):
    variables = variables or [
        {"type": "radius", "surface_number": 1, "min_val": 20.0, "max_val": 150.0},
        {"type": "material", "surface_number": target},
    ]
    operands = operands or [{"type": "f2", "target": 85.0, "weight": 1.0}]
    return GlassExpertSetup(
        "test-run",
        PreparedOptic.capture(optic),
        tuple(
            FrozenDefinition.from_dict(f"variable:{i}", "variable", v)
            for i, v in enumerate(variables)
        ),
        tuple(
            FrozenDefinition.from_dict(f"operand:{i}", "operand", v)
            for i, v in enumerate(operands)
        ),
        (MaterialTarget("variable:1", target, pool or make_pool()),),
        GlassExpertSettings(maxiter=3),
    )


def prepare(setup, event=None):
    return jobs.prepare_setup(
        setup.baseline,
        {"setup": setup},
        lambda *a, **k: None,
        event or threading.Event(),
    )


def test_preflight_keeps_baseline_and_freezes_implicit_target(
    minimal_optic, set_test_backend
):
    before = capture_material(minimal_optic.surfaces[1].material_post)
    setup = make_setup(minimal_optic, operands=[{"type": "f2", "weight": 1.0}])
    prepared = prepare(setup)
    assert prepared.baseline_merit == pytest.approx(0.0)
    operand = prepared.baseline_operands[0]
    assert operand.target == operand.value
    assert prepared.resolved_operands[0].as_dict()["target"] == operand.value
    assert prepared.portable_baseline is not None
    assert prepared.replay_unavailable is None
    assert capture_material(minimal_optic.surfaces[1].material_post) == before
    restored = setup.baseline.restore()
    assert capture_material(restored.surfaces[1].material_post) == before


def test_primary_selector_resolved_against_frozen_baseline(minimal_optic):
    setup = make_setup(
        minimal_optic,
        operands=[
            {
                "type": "real_y_intercept",
                "target": 0.0,
                "input_data": {
                    "surface_number": 3,
                    "Hx": 0.0,
                    "Hy": 0.0,
                    "Px": 0.0,
                    "Py": 1.0,
                    "wavelength": "primary",
                },
            }
        ],
    )
    minimal_optic.wavelengths.remove(0)
    minimal_optic.wavelengths.add(0.7, is_primary=True)
    prepared = prepare(setup)
    assert prepared.resolved_operands[0].as_dict()["input_data"]["wavelength"] == 0.55


def test_native_replay_owns_untouched_fresnel_and_thinfilm_media(
    minimal_optic, monkeypatch
):
    from optiland.coatings import ThinFilmCoating
    from optiland.materials.material_file import MaterialFile
    from optiland.optic import Optic

    front = minimal_optic.surfaces[1]
    coating = ThinFilmCoating(
        front.material_pre,
        front.material_post,
        [(front.material_post, 120.0, "native layer")],
    )
    front.interaction_model.coating = coating
    minimal_optic.surfaces[2].set_fresnel_coating()
    # A native surface update rebinds the evaluated stack, not constructor fields.
    changed = make_pool().candidates[0].restore()
    front.material_post = changed
    assert coating.stack.substrate_material is changed
    monkeypatch.setattr(
        MaterialFile, "from_dict", classmethod(lambda *a: pytest.fail("file lookup"))
    )
    encoded = capture_portable_optic(minimal_optic, threading.Event())
    restored = Optic.from_dict(json.loads(encoded))
    stack = restored.surfaces[1].interaction_model.coating.stack
    assert capture_material(stack.substrate_material) == capture_material(changed)
    assert capture_material(stack.layers[0].material) == capture_material(
        coating.stack.layers[0].material
    )
    assert stack.layers[0].thickness_um == pytest.approx(0.12)
    assert restored.surfaces[2].interaction_model.coating.material_pre is not None


@pytest.mark.parametrize(
    "alter, message",
    [
        (lambda v: v[0].update(type="index", wavelength="primary"), "overlap"),
        (lambda v: v[0].update(min_val=200.0), "lower bound"),
        (lambda v: v[0].update(min_val="20"), "bounds must be finite"),
        (lambda v: v[0].update(surface_number=True), "Unsupported"),
        (lambda v: v[0].update(scaler={"type": "custom"}), "arguments/scalers"),
        (lambda v: v[1].update(min_val=1.0), "pool, not numerical"),
    ],
)
def test_conflicting_variable_definitions_rejected_before_execution(
    minimal_optic, alter, message
):
    variables = [
        {"type": "radius", "surface_number": 1, "min_val": 20.0, "max_val": 150.0},
        {"type": "material", "surface_number": 1},
    ]
    alter(variables)
    with pytest.raises(ValueError, match=message):
        prepare(make_setup(minimal_optic, variables=variables))


def test_pickup_and_solve_conflicts_are_not_removed(minimal_optic):
    minimal_optic.pickups.add(2, "radius", 1)
    setup = make_setup(minimal_optic)
    with pytest.raises(ValueError, match="pickup controls"):
        prepare(setup)
    assert len(minimal_optic.pickups.pickups) == 1
    minimal_optic.pickups.pickups.clear()
    from optiland.solves.curvature import ChiefRayAngleCurvatureSolve

    minimal_optic.solves.solves.append(
        ChiefRayAngleCurvatureSolve(minimal_optic, 1, 0.0)
    )
    with pytest.raises(ValueError, match="solve controls"):
        prepare(make_setup(minimal_optic))


def test_exact_pool_metadata_and_known_support_are_verified(minimal_optic):
    pool = make_pool()
    corrupted = MaterialPool((replace(pool.candidates[0], nd=1.9), pool.candidates[1]))
    with pytest.raises(ValueError, match="incorrect model nd"):
        prepare(make_setup(minimal_optic, pool=corrupted))
    minimal_optic.wavelengths.remove(0)
    minimal_optic.wavelengths.add(1.2, is_primary=True)
    with pytest.raises(ValueError, match="support"):
        prepare(make_setup(minimal_optic))


def test_fixed_pool_no_effective_objective_and_image_target_rejected(minimal_optic):
    with pytest.raises(ValueError, match="fixed"):
        prepare(make_setup(minimal_optic, pool=make_pool(count=1)))
    with pytest.raises(ValueError, match="nonzero effective"):
        prepare(
            make_setup(
                minimal_optic, operands=[{"type": "f2", "target": 85, "weight": 0.0}]
            )
        )
    with pytest.raises(ValueError, match="image"):
        prepare(make_setup(minimal_optic, target=3))


def test_setup_deduplicates_exact_identity_and_freezes_definition_lists(minimal_optic):
    definition = {"type": "f2", "input_data": {}}
    frozen = FrozenDefinition.from_dict("operand:0", "operand", definition)
    definition["input_data"]["wavelength"] = 0.6
    assert frozen.as_dict() == {"type": "f2", "input_data": {}}
    with pytest.raises(FrozenInstanceError):
        frozen.identifier = "changed"
    setup = make_setup(minimal_optic)
    assert setup.source_stamps == (SourceStamp("owned", "1"),)
    assert replace(setup, run_id="other-run").input_digest == setup.input_digest
    assert (
        replace(setup, settings=replace(setup.settings, seed=99)).input_digest
        != setup.input_digest
    )


@pytest.mark.parametrize(
    "settings",
    [{"seed": True}, {"tol": float("nan")}, {"report_limit": 1001}, {"nd_scale": 0.0}],
)
def test_invalid_settings_are_rejected_before_queue(settings):
    with pytest.raises(ValueError):
        GlassExpertSettings(**settings)


def test_cancel_before_preparation_and_snapshot_mismatch(minimal_optic):
    setup = make_setup(minimal_optic)
    event = threading.Event()
    event.set()
    with pytest.raises(CalculationCancelled):
        prepare(setup, event)
    minimal_optic.name = "different"
    with pytest.raises(ValueError, match="disagree"):
        jobs.prepare_setup(
            PreparedOptic.capture(minimal_optic),
            {"setup": setup},
            lambda *a: None,
            threading.Event(),
        )


def test_prepared_native_data_and_transport_budgets(minimal_optic, monkeypatch):
    from optiland_gui.services import glass_expert_portability as portable
    from optiland_gui.services import glass_expert_records as records

    setup = make_setup(minimal_optic)
    monkeypatch.setattr(records, "MAX_RUN_BYTES", 100)
    with pytest.raises(ValueError, match="budget"):
        replace(setup, run_id="too-large")
    monkeypatch.setattr(portable, "MAX_RUN_BYTES", 100)
    prepared = prepare(setup)
    assert prepared.portable_baseline is None
    assert "budget" in prepared.replay_unavailable
    monkeypatch.setattr(jobs, "MAX_RUN_BYTES", 100)
    with pytest.raises(ValueError, match="prepared data exceeds"):
        prepare(setup)


@pytest.mark.parametrize("override", ["optic", "tracer", "geometry"])
def test_native_replay_rejects_patched_behavior(minimal_optic, override):
    if override == "optic":
        minimal_optic.trace_generic = minimal_optic.trace
    elif override == "tracer":
        minimal_optic.ray_tracer.trace = minimal_optic.ray_tracer.trace_generic
    else:
        geometry = minimal_optic.surfaces[1].geometry
        geometry.sag = geometry.distance
    with pytest.raises(ValueError, match="snapshot adapter|Patched"):
        capture_portable_optic(minimal_optic, threading.Event())


@pytest.mark.parametrize("coating_kind", ["fresnel", "thin_film"])
def test_native_replay_rejects_detached_evaluated_jones_state(
    minimal_optic, coating_kind
):
    from optiland.coatings import ThinFilmCoating
    from optiland.materials import IdealMaterial
    from optiland.thin_film import ThinFilmStack

    surface = minimal_optic.surfaces[1]
    if coating_kind == "fresnel":
        surface.set_fresnel_coating()
        surface.interaction_model.coating.jones.material_post = IdealMaterial(2.0)
    else:
        coating = ThinFilmCoating(surface.material_pre, surface.material_post)
        surface.interaction_model.coating = coating
        coating.jones.stack = ThinFilmStack(surface.material_pre, IdealMaterial(2.0))
    with pytest.raises(ValueError, match="[Dd]etached.*Jones"):
        capture_portable_optic(minimal_optic, threading.Event())
    prepared = prepare(make_setup(minimal_optic))
    assert prepared.portable_baseline is None
    assert "Jones" in prepared.replay_unavailable


def test_native_replay_rejects_custom_nested_components(minimal_optic):
    from optiland.jones import JonesFresnel

    class CustomJones(JonesFresnel):
        pass

    surface = minimal_optic.surfaces[1]
    surface.set_fresnel_coating()
    surface.interaction_model.coating._jones = CustomJones(
        surface.material_pre, surface.material_post
    )
    with pytest.raises(ValueError, match="custom optical component"):
        capture_portable_optic(minimal_optic, threading.Event())


def test_native_replay_checks_restored_material_state(minimal_optic, monkeypatch):
    from optiland.materials import IdealMaterial
    from optiland.optic import Optic

    original = Optic.from_dict

    def wrong_native_reconstruction(cls, data):
        optic = original(data)
        optic.updater.set_material(IdealMaterial(2.0), 1)
        return optic

    monkeypatch.setattr(Optic, "from_dict", classmethod(wrong_native_reconstruction))
    with pytest.raises(ValueError, match="Native replay changed"):
        capture_portable_optic(minimal_optic, threading.Event())


def test_toroidal_axis_definitions_and_reciprocal_alias_are_native(minimal_optic):
    from optiland.geometries import ToroidalGeometry

    geometry = minimal_optic.surfaces[1].geometry
    minimal_optic.surfaces[1].geometry = ToroidalGeometry(geometry.cs, 50.0, 60.0)
    variables = [
        {"type": "radius", "surface_number": 1, "axis": "x"},
        {"type": "material", "surface_number": 1},
        {"type": "radius", "surface_number": 1, "axis": "y"},
    ]
    prepared = prepare(make_setup(minimal_optic, variables=variables))
    assert prepared.resolved_variables[0].as_dict()["axis"] == "x"
    assert prepared.resolved_variables[2].as_dict()["axis"] == "y"
    # Public reciprocal-radius updates the toroid's Y radius, not its X radius.
    variables.append({"type": "reciprocal_radius", "surface_number": 1})
    with pytest.raises(ValueError, match="same optical parameter"):
        prepare(make_setup(minimal_optic, variables=variables))
    variables.pop(2)
    prepare(make_setup(minimal_optic, variables=variables))
    variables[-1]["axis"] = "x"
    with pytest.raises(ValueError, match="arguments/scalers"):
        prepare(make_setup(minimal_optic, variables=variables))


def test_ordinary_radius_axis_and_reciprocal_alias_validation(minimal_optic):
    variables = [
        {"type": "radius", "surface_number": 1, "axis": None},
        {"type": "material", "surface_number": 1},
        {"type": "reciprocal_radius", "surface_number": 1},
    ]
    with pytest.raises(ValueError, match="same optical parameter"):
        prepare(make_setup(minimal_optic, variables=variables))
    variables.pop()
    variables[0]["axis"] = "x"
    with pytest.raises(ValueError, match="only supported for toroidal"):
        prepare(make_setup(minimal_optic, variables=variables))


def test_variable_path_uses_index_contents_and_normalizes_aliases(minimal_optic):
    variables = [
        {"type": "polynomial_coeff", "surface_number": 1, "coeff_index": [0, 0]},
        {"type": "material", "surface_number": 1},
    ]
    setup = make_setup(minimal_optic, variables=variables)
    # Validate supported structured indices without executing on a wrong geometry.
    jobs._check_variables(minimal_optic, setup)
    first = FrozenDefinition.from_dict(
        "forbes:a",
        "variable",
        {"type": "forbes_qbfs_coeff", "surface_number": 1, "coeff_number": 0},
    )
    second = FrozenDefinition.from_dict(
        "forbes:b",
        "variable",
        {"type": "forbes_qnormalslope_coeff", "surface_number": 1, "coeff_number": 0},
    )
    with pytest.raises(ValueError, match="same optical parameter"):
        jobs._check_variables(
            minimal_optic, replace(setup, variables=(first, second, setup.variables[1]))
        )
    unrelated = replace(
        setup.variables[0],
        data=b'{"type":"radius","surface_number":1,"coeff_number":1}',
    )
    with pytest.raises(ValueError, match="arguments/scalers"):
        jobs._check_variables(
            minimal_optic, replace(setup, variables=(unrelated, setup.variables[1]))
        )


def test_actual_core_run_returns_exact_whole_candidate(minimal_optic, set_test_backend):
    setup = make_setup(minimal_optic)
    prepared = prepare(setup)
    before = capture_material(minimal_optic.surfaces[1].material_post)
    updates = []
    result = jobs.run(
        setup.baseline,
        {"setup": prepared},
        lambda *a, **k: updates.append(k.get("details")),
        threading.Event(),
    )
    assert result.report.candidate_eligible
    assert result.report.candidate.merit >= 0
    assert result.report.baseline.merit == pytest.approx(prepared.baseline_merit)
    assert result.row_mapping == ((0, 0), (1, 1), (2, 2), (3, 3))
    assert result.native_candidate is not None
    candidate = result.candidate.restore()
    choice = result.report.candidate.materials[0]
    assert (
        capture_material(candidate.surfaces[1].material_post)
        == setup.targets[0].pool.resolve(choice.key).owned_data
    )
    assert capture_material(minimal_optic.surfaces[1].material_post) == before
    assert any(
        update["glass_expert"].input_digest == setup.input_digest
        for update in updates
        if update
    )


def test_zero_baseline_never_fabricates_material_eligibility(minimal_optic):
    setup = make_setup(minimal_optic, operands=[{"type": "f2", "weight": 1.0}])
    result = jobs.run(
        setup.baseline,
        {"setup": prepare(setup)},
        lambda *a, **k: None,
        threading.Event(),
    )
    assert result.outcome == "already_reached"
    assert result.candidate is None and result.native_candidate is None
    assert result.row_mapping == () and result.candidate_operands == ()


def test_run_rebuilds_tampered_prepared_fields_from_frozen_setup(minimal_optic):
    setup = make_setup(minimal_optic)
    prepared = prepare(setup)
    changed = replace(
        prepared,
        baseline_merit=123456.0,
        portable_baseline=b"wrong",
        resolved_operands=(
            FrozenDefinition.from_dict(
                "untrusted", "operand", {"type": "f2", "target": -1.0}
            ),
        ),
    )
    result = jobs.run(
        setup.baseline, {"setup": changed}, lambda *a, **k: None, threading.Event()
    )
    assert result.report.baseline.merit == pytest.approx(prepared.baseline_merit)
    assert result.baseline_operands == prepared.baseline_operands
    assert result.candidate_operands[0].identifier == setup.operands[0].identifier


@pytest.mark.parametrize("phase", ["seed", "global", "local", "final"])
def test_stop_at_core_phase_preserves_executor_cancellation(
    minimal_optic, monkeypatch, phase
):
    from optiland.optimization.optimizer.scipy.glass_expert import GlassExpert

    original = GlassExpert.run
    event = threading.Event()
    phases = []

    def interrupt(self, *args, **kwargs):
        report = kwargs["progress"]

        def callback(update):
            phases.append(update.phase)
            if update.phase == phase:
                event.set()
            report(update)

        kwargs["progress"] = callback
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GlassExpert, "run", interrupt)
    setup = make_setup(minimal_optic)
    with pytest.raises(CalculationCancelled):
        jobs.run(setup.baseline, {"setup": prepare(setup)}, lambda *a, **k: None, event)
    assert phase in phases


@pytest.mark.parametrize(
    "corruption,message",
    [
        ("baseline", "baseline objective"),
        ("no_baseline", "reference baseline"),
        ("continuous_missing", "incomplete continuous"),
        ("continuous_value", "different continuous"),
        ("material_missing", "incomplete material"),
        ("fingerprint", "exact pool"),
        ("merit", "different final merit"),
        ("material_state", "exact pool"),
        ("placement", "invalid surface placement"),
        ("radius_nan", "invalid surface geometry"),
        ("conic_nan", "invalid conic geometry"),
        ("replacement", "surface identity or order"),
        ("bound", "continuous bound"),
    ],
)
def test_final_candidate_proof_is_independently_verified(
    minimal_optic, monkeypatch, corruption, message
):
    from optiland.optimization.optimizer.scipy.glass_expert import GlassExpert

    original = GlassExpert.run
    setup = make_setup(minimal_optic)

    def corrupt(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        report = result.glass_expert
        state = report.candidate
        if corruption == "baseline":
            report = replace(report, baseline=replace(report.baseline, merit=-100.0))
        elif corruption == "no_baseline":
            report = replace(report, baseline=None)
        elif corruption == "continuous_missing":
            state = replace(state, continuous=())
        elif corruption == "continuous_value":
            state = replace(
                state, continuous=(replace(state.continuous[0], value=-100.0),)
            )
        elif corruption == "material_missing":
            state = replace(state, materials=())
        elif corruption == "fingerprint":
            state = replace(
                state, materials=(replace(state.materials[0], fingerprint="wrong"),)
            )
        elif corruption == "merit":
            state = replace(state, merit=123456.0)
        elif corruption == "material_state":
            optic = self.problem.variables[0].optic
            candidate = next(
                c
                for c in setup.targets[0].pool.candidates
                if c.key != state.materials[0].key
            )
            optic.updater.set_material(candidate.restore(), 1)
        else:
            from copy import deepcopy

            optic = self.problem.variables[0].optic
            if corruption == "placement":
                optic.surfaces[1].geometry.cs.x = float("nan")
            elif corruption == "radius_nan":
                optic.surfaces[1].geometry.radius = float("nan")
            elif corruption == "conic_nan":
                optic.surfaces[1].geometry.k = float("nan")
            elif corruption == "replacement":
                optic.surfaces._surfaces[1] = deepcopy(optic.surfaces[1])
            elif corruption == "bound":
                self.problem.variables[0].variable.update_value(10.0)
                state = replace(
                    state, continuous=(replace(state.continuous[0], value=10.0),)
                )
        result.glass_expert = replace(report, candidate=state)
        return result

    monkeypatch.setattr(GlassExpert, "run", corrupt)
    with pytest.raises(ValueError, match=message):
        jobs.run(
            setup.baseline,
            {"setup": prepare(setup)},
            lambda *a, **k: None,
            threading.Event(),
        )


def test_actual_mixed_descriptor_order_is_preserved(minimal_optic):
    setup = make_setup(
        minimal_optic,
        variables=[
            {"type": "radius", "surface_number": 1, "min_val": 20.0, "max_val": 150.0},
            {"type": "material", "surface_number": 1},
            {"type": "thickness", "surface_number": 1, "min_val": 1.0, "max_val": 8.0},
        ],
    )
    result = jobs.run(
        setup.baseline,
        {"setup": prepare(setup)},
        lambda *a, **k: None,
        threading.Event(),
    )
    assert tuple(v.variable_id for v in result.report.candidate.continuous) == (
        "variable:0",
        "variable:2",
    )
    assert tuple(v.variable_id for v in result.report.candidate.materials) == (
        "variable:1",
    )


def test_constructor_padding_cannot_change_the_reference(minimal_optic):
    from optiland.geometries import EvenAsphere

    surface = minimal_optic.surfaces[1]
    surface.geometry = EvenAsphere(surface.geometry.cs, 50.0, coefficients=[0.0])
    variables = [
        {"type": "asphere_coeff", "surface_number": 1, "coeff_number": 3},
        {"type": "material", "surface_number": 1},
    ]
    with pytest.raises(ValueError, match="construction changed the reference"):
        prepare(make_setup(minimal_optic, variables=variables))
    assert surface.geometry.coefficients == [0.0]


@pytest.mark.parametrize("value", [[1.0, 2.0], True, complex(1.0, 1.0), float("inf")])
def test_operand_scalar_validation_rejects_nonphysical_results(value):
    import numpy as np

    with pytest.raises(ValueError, match="finite real scalar"):
        jobs._scalar(np.asarray(value), "actual operand")


def test_pool_sampling_and_custom_operand_are_explicit_setup_errors(minimal_optic):
    setup = make_setup(minimal_optic)
    with pytest.raises(ValueError, match="sample count"):
        prepare(replace(setup, settings=replace(setup.settings, global_samples=3)))
    with pytest.raises(ValueError, match="Custom operands"):
        prepare(make_setup(minimal_optic, operands=[{"type": "not-a-builtin"}]))
    with pytest.raises(ValueError, match="continuous variable"):
        prepare(replace(setup, variables=(setup.variables[1],)))
    with pytest.raises(ValueError, match="target do not agree"):
        prepare(replace(setup, targets=(replace(setup.targets[0], surface_number=2),)))


def test_standalone_candidate_export_failure_retains_internal_candidate(
    minimal_optic, monkeypatch
):
    original = jobs.capture_portable_optic
    calls = 0

    def fail_candidate(optic, cancelled):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("Native candidate unsupported")
        return original(optic, cancelled)

    monkeypatch.setattr(jobs, "capture_portable_optic", fail_candidate)
    setup = make_setup(minimal_optic)
    result = jobs.run(
        setup.baseline, {"setup": setup}, lambda *a, **k: None, threading.Event()
    )
    assert result.candidate is not None and result.report.candidate_eligible
    assert result.native_candidate is None
    assert result.replay_unavailable == "Native candidate unsupported"
    assert any(
        "Candidate native export unavailable" in warning for warning in result.warnings
    )


@pytest.mark.parametrize("with_report", [False, True])
def test_core_requested_cancel_never_returns_a_candidate(
    minimal_optic, monkeypatch, with_report
):
    from optiland.optimization.glass_expert_records import GlassExpertCancelled
    from optiland.optimization.optimizer.scipy.glass_expert import GlassExpert

    original = GlassExpert.run

    def cancel(self, *args, **kwargs):
        if not with_report:
            raise GlassExpertCancelled()
        result = original(self, *args, **kwargs)
        raise GlassExpertCancelled(
            report=replace(result.glass_expert, outcome="cancelled", candidate=None)
        )

    monkeypatch.setattr(GlassExpert, "run", cancel)
    setup = make_setup(minimal_optic)
    if with_report:
        result = jobs.run(
            setup.baseline, {"setup": setup}, lambda *a, **k: None, threading.Event()
        )
        assert result.outcome == "cancelled" and result.candidate is None
    else:
        with pytest.raises(ValueError, match="cancellation supplied no report"):
            jobs.run(
                setup.baseline,
                {"setup": setup},
                lambda *a, **k: None,
                threading.Event(),
            )
