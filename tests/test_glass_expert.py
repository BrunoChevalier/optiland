"""Exact-pool search, complete state ownership and numerical failure contracts."""

from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from scipy.optimize import OptimizeResult

import optiland.backend as be
from optiland.materials import DataMaterial, IdealMaterial
from optiland.materials.portability import capture_material
from optiland.materials.properties import (
    D_LINE_UM,
    MaterialKey,
    SourceStamp,
    describe_material,
)
from optiland.optic import Optic
from optiland.optimization import (
    GlassExpert,
    GlassExpertCancelled,
    MaterialPool,
    OptimizationProblem,
    prepare_material_candidate,
)
from optiland.optimization.optimizer.scipy.glass_expert import _distance, _sample

WAVES = (0.4861327, D_LINE_UM, 0.6562725)


def material(offset=1.5, coefficient=0.005, name="Same label"):
    return DataMaterial.from_coefficients(
        "formula 5",
        [offset, coefficient, -2],
        name=name,
        wavelength_range=(0.4, 0.8),
        bounds="raise",
    )


def pool(count=2, *, source="owned", start=1.52, coincident=False):
    records = tuple(
        describe_material(
            material(start if coincident else start + index * 0.04),
            MaterialKey(source, f"Catalog{index % 2}", f"record{index}"),
        )
        for index in range(count)
    )
    stamp = SourceStamp(source, "frozen")
    return MaterialPool(
        tuple(
            prepare_material_candidate(record, stamp, wavelengths_um=WAVES)
            for record in records
        )
    )


def make_problem(*, selection=None, baseline=None, radius=50.0, fixed=None):
    selection = pool() if selection is None else selection
    baseline = material() if baseline is None else baseline
    lens = Optic()
    lens.surfaces.add(index=0, radius=float("inf"), thickness=float("inf"))
    lens.surfaces.add(
        index=1, radius=radius, thickness=5.0, material=baseline, is_stop=True
    )
    lens.surfaces.add(index=2, radius=-50.0, thickness=45.0)
    lens.surfaces.add(index=3)
    lens.set_aperture("EPD", 10.0)
    lens.fields.set_type("angle")
    lens.fields.add(y=0.0)
    for wavelength in WAVES:
        lens.wavelengths.add(wavelength, is_primary=wavelength == D_LINE_UM)
    lens.updater.update()
    problem = OptimizationProblem()
    # Deliberately interleave categorical and continuous descriptor order.
    problem.add_variable(lens, "material", surface_number=1, glass_selection=selection)
    problem.add_variable(lens, "radius", surface_number=1, min_val=20.0, max_val=150.0)
    if fixed is not None:
        problem.add_variable(lens, "material", surface_number=2, glass_selection=fixed)
    problem.add_operand("f2", target=60.0, weight=1.0, input_data={"optic": lens})
    return lens, problem


def run(problem, **kwargs):
    options = dict(
        num_neighbours=1,
        global_samples=2,
        seed=17,
        maxiter=40,
        tol=1e-10,
        disp=False,
        verbose=False,
    )
    options.update(kwargs)
    return GlassExpert(problem).run(**options)


def scalar(value):
    return float(be.to_numpy(value).item())


def test_actual_optical_search_improves_and_preserves_descriptors(set_test_backend):
    lens, problem = make_problem(radius=1000.0)
    original_manager = problem.variables
    original_variables = tuple(problem.variables)
    original_kwargs = tuple(variable.kwargs.copy() for variable in original_variables)
    initial_material = lens.surfaces[1].material_post
    baseline = float(be.to_numpy(problem.sum_squared()))
    result = run(problem, variable_ids=("exact-glass", "front-radius"))
    report = result.glass_expert
    assert report.outcome == "completed"
    assert report.candidate_eligible and report.improved
    assert report.baseline.merit == pytest.approx(baseline)
    assert result.fun < baseline * 1e-6
    assert result.fun == float(be.to_numpy(problem.sum_squared()))
    assert report.candidate.merit == result.fun
    assert problem.variables is original_manager
    assert tuple(problem.variables) == original_variables
    assert tuple(variable.kwargs for variable in problem.variables) == original_kwargs
    assert report.baseline.materials[0].key is None
    assert report.candidate.materials[0].variable_id == "exact-glass"
    chosen = original_variables[0].glass_selection.resolve(
        report.candidate.materials[0].key
    )
    assert capture_material(lens.surfaces[1].material_post) == chosen.owned_data
    assert lens.surfaces[1].material_post is not initial_material
    assert report.candidate.materials[0].fingerprint == chosen.fingerprint
    physical = report.candidate.continuous[0]
    assert physical.variable_id == "front-radius"
    assert physical.value == pytest.approx(scalar(lens.surfaces[1].geometry.radius))
    assert physical.value != pytest.approx(float(result.x[0]))  # solver uses a scaler
    assert 20 <= physical.value <= 150
    assert {trial.phase for trial in report.trials} == {
        "seed",
        "global",
        "local",
        "final",
    }
    assert report.improvement_percent == pytest.approx(
        (baseline - result.fun) / baseline * 100
    )
    json.dumps(asdict(report), allow_nan=False)


def test_complete_membership_includes_fixed_outside_pool_target(
    set_test_backend, monkeypatch
):
    fixed = pool(1, source="fixed", start=1.65)
    lens, problem = make_problem(fixed=fixed)
    baseline_second = lens.surfaces[2].material_post
    desired = problem.variables[0].glass_selection.candidates[1]

    def objective():
        radius = lens.surfaces[1].geometry.radius
        index = lens.surfaces[1].material_post.n(D_LINE_UM)
        # The second replacement is deliberately costly; retaining its old air
        # baseline would make a partial assignment look spuriously better.
        cost = 0 if lens.surfaces[2].material_post is baseline_second else 1000
        return (radius - 70) ** 2 + (index - desired.nd) ** 2 * 10000 + cost

    monkeypatch.setattr(problem, "sum_squared", objective)
    result = run(problem, variable_ids=("glass-a", "radius", "fixed-b"))
    report = result.glass_expert
    assert report.candidate_eligible
    assert report.outcome == "no_improvement" and not report.improved
    assert report.candidate.merit > report.baseline.merit
    assert report.candidate.materials[1].key == fixed.candidates[0].key
    assert (
        capture_material(lens.surfaces[2].material_post)
        == fixed.candidates[0].owned_data
    )
    assert all(trial.variable_id != "fixed-b" for trial in report.trials)
    assert report.candidate.materials[0].key == desired.key
    assert report.candidate.continuous[0].value == pytest.approx(70.0, abs=1e-4)


@pytest.mark.parametrize("phase", ["seed", "global", "local", "final"])
def test_cancellation_in_every_phase_restores_exact_originals(set_test_backend, phase):
    lens, problem = make_problem(fixed=pool(1, source="fixed"))
    manager, variables = problem.variables, tuple(problem.variables)
    original_materials = tuple(surface.material_post for surface in lens.surfaces)
    original_radius = scalar(lens.surfaces[1].geometry.radius)
    events = []

    def checkpoint():
        if events and events[-1].phase == phase:
            raise RuntimeError("Stop requested")

    optimizer = GlassExpert(problem)
    with pytest.raises(GlassExpertCancelled) as stopped:
        optimizer.run(
            num_neighbours=1,
            global_samples=2,
            maxiter=15,
            disp=False,
            verbose=False,
            progress=events.append,
            checkpoint=checkpoint,
        )
    report = stopped.value.report
    assert report is optimizer.last_report
    assert report.outcome == "cancelled" and not report.candidate_eligible
    assert report.candidate is None
    assert problem.variables is manager and tuple(problem.variables) == variables
    assert all(
        surface.material_post is before
        for surface, before in zip(lens.surfaces, original_materials, strict=True)
    )
    assert scalar(lens.surfaces[1].geometry.radius) == original_radius
    assert not optimizer._running
    assert any(
        trial.status == "cancelled" and trial.phase == phase for trial in report.trials
    )


def test_truthy_callback_cancels_and_does_not_lose_return_value(set_test_backend):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    calls = []
    with pytest.raises(GlassExpertCancelled, match="callback"):
        run(problem, callback=lambda *args: calls.append(args) or True)
    assert calls
    assert lens.surfaces[1].material_post is original
    assert scalar(lens.surfaces[1].geometry.radius) == 50.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_nonfinite_or_negative_baseline_has_no_candidate(value, monkeypatch):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    manager = problem.variables
    monkeypatch.setattr(problem, "sum_squared", lambda: be.array(value))
    optimizer = GlassExpert(problem)
    with pytest.raises(ValueError, match="merit"):
        optimizer.run(global_samples=2)
    assert optimizer.last_report.outcome == "failed"
    assert optimizer.last_report.baseline is None
    assert not optimizer.last_report.candidate_eligible
    assert lens.surfaces[1].material_post is original and problem.variables is manager


def test_zero_baseline_is_reference_only_and_never_divides(monkeypatch):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    monkeypatch.setattr(problem, "sum_squared", lambda: be.array(0.0))
    result = run(problem)
    assert result.glass_expert.outcome == "already_reached"
    assert result.glass_expert.candidate is None
    assert not result.glass_expert.candidate_eligible
    assert result.glass_expert.improvement_percent is None
    assert result.glass_expert.trials == ()
    assert lens.surfaces[1].material_post is original


def test_invalid_trials_cannot_turn_finite_penalty_into_candidate(monkeypatch):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    monkeypatch.setattr(
        problem,
        "sum_squared",
        lambda: be.array(
            1.0 if lens.surfaces[1].material_post is original else float("nan")
        ),
    )
    result = run(problem, maxiter=3)
    assert result.glass_expert.outcome == "no_candidate"
    assert result.glass_expert.candidate is None
    assert not result.success and not result.glass_expert.candidate_eligible
    assert all(trial.status == "nonfinite" for trial in result.glass_expert.trials)
    assert lens.surfaces[1].material_post is original


@pytest.mark.parametrize("failure", ["raises", "worse"])
def test_failed_or_worse_final_polish_retains_best(monkeypatch, failure):
    lens, problem = make_problem()
    optimizer = GlassExpert(problem)
    original = optimizer._optimize_continuous

    def polish():
        if optimizer._phase != "final":
            return original()
        if failure == "raises":
            raise ValueError("Cannot refine this step")
        lens.updater.set_radius(145.0, 1)
        lens.updater.update()
        return OptimizeResult(success=True, message="worse polish", nit=1)

    monkeypatch.setattr(optimizer, "_optimize_continuous", polish)
    result = optimizer.run(
        global_samples=2,
        num_neighbours=1,
        maxiter=30,
        tol=1e-10,
        disp=False,
        verbose=False,
    )
    assert result.glass_expert.candidate_eligible
    final = result.glass_expert.trials[-1]
    assert not final.improved_best
    assert final.status == ("failed" if failure == "raises" else "evaluated")
    assert result.fun < 1e-6
    assert result.fun == float(be.to_numpy(problem.sum_squared()))


def test_unknown_baseline_coordinates_and_bounded_reports():
    lens, problem = make_problem(selection=pool(5), baseline=IdealMaterial(1.5))
    result = run(problem, report_limit=2, global_samples=3)
    assert result.glass_expert.candidate_eligible
    assert any(
        "Baseline coordinates unavailable" in warning
        for warning in result.glass_expert.warnings
    )
    assert len(result.glass_expert.trials) == 2
    assert result.glass_expert.omitted_trials == result.glass_expert.total_trials - 2
    assert result.nfev == result.glass_expert.evaluations
    assert result.glass_expert.total_trials == 6


def test_seeded_sampler_coincident_points_keys_and_global_rng():
    candidates = pool(6, coincident=True).candidates
    before = random.getstate()
    first = _sample(candidates, 4, random.Random(3), (1, 1), lambda: None)
    again = _sample(
        tuple(reversed(candidates)), 4, random.Random(3), (1, 1), lambda: None
    )
    other = _sample(candidates, 4, random.Random(5), (1, 1), lambda: None)
    assert first == again and first != other
    assert len(first) == len({candidate.key for candidate in first}) == 4
    assert len(_sample(candidates, 10, random.Random(3), (1, 1), lambda: None)) == 6
    assert random.getstate() == before
    assert _distance((1.6, 30), (1.5, 50), (0.05, 10)) == pytest.approx(math.sqrt(8))
    with pytest.raises(ValueError, match="overflow"):
        _distance((1e308, 0), (-1e308, 0), (1, 1))


@pytest.mark.parametrize(
    "options",
    [
        {"num_neighbours": 0},
        {"num_neighbours": True},
        {"global_samples": 1},
        {"global_samples": 3},
        {"maxiter": 0},
        {"seed": -1},
        {"seed": True},
        {"report_limit": 0},
        {"report_limit": 10001},
        {"tol": float("nan")},
        {"nd_scale": 0},
        {"vd_scale": float("inf")},
        {"nd_scale": True},
        {"variable_ids": ("same", "same")},
        {"variable_ids": ("one",)},
    ],
)
def test_invalid_configuration_rejected_before_mutation(options):
    lens, problem = make_problem()
    original, manager = lens.surfaces[1].material_post, problem.variables
    with pytest.raises(ValueError):
        run(problem, **options)
    assert problem.variables is manager and lens.surfaces[1].material_post is original
    assert scalar(lens.surfaces[1].geometry.radius) == 50.0


def test_cardinality_no_continuous_and_cross_pool_stamps():
    lens, problem = make_problem(selection=pool(1))
    with pytest.raises(ValueError, match="ordinary optimization"):
        run(problem)
    lens, problem = make_problem()
    problem.variables.variables.pop(1)
    with pytest.raises(ValueError, match="continuous"):
        run(problem)
    main = pool()
    conflicting = MaterialPool(
        (replace(main.candidates[0], stamp=SourceStamp("owned", "new")),)
    )
    lens, problem = make_problem(selection=main, fixed=conflicting)
    with pytest.raises(ValueError, match="conflicting source"):
        run(problem)


def test_typed_legacy_spelling_cannot_bypass_source_generations():
    main = pool(source="legacy:string")
    changed = MaterialPool(
        (replace(main.candidates[0], stamp=SourceStamp("legacy:string", "changed")),)
    )
    lens, problem = make_problem(selection=main, fixed=changed)
    original = lens.surfaces[1].material_post
    with pytest.raises(ValueError, match="conflicting source"):
        run(problem)
    assert lens.surfaces[1].material_post is original


def test_sampler_distance_work_is_linear_per_selected_center(monkeypatch):
    from optiland.optimization.optimizer.scipy import glass_expert

    candidates = pool(30).candidates
    calls = []
    distance = glass_expert._distance

    def counted(*args):
        calls.append(args)
        return distance(*args)

    monkeypatch.setattr(glass_expert, "_distance", counted)
    selected = _sample(candidates, 15, random.Random(8), (1, 1), lambda: None)
    assert len({candidate.key for candidate in selected}) == 15
    assert len(calls) < 30 * 15


@pytest.mark.parametrize(
    "solver", ["OptimizerGeneric", "OrthogonalDescent", "TorchAdamOptimizer"]
)
def test_continuous_solvers_reject_typed_behavior_before_numeric_conversion(solver):
    import optiland.optimization as optimization

    solver_type = getattr(optimization, solver, None)
    if solver_type is None:
        pytest.skip("Optional Torch optimizer unavailable")
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    with pytest.raises(AssertionError, match="GlassExpert"):
        solver_type(problem)
    assert lens.surfaces[1].material_post is original


def test_unguarded_target_mutation_prevents_complete_result(monkeypatch):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    optimizer = GlassExpert(problem)
    original_solver = optimizer._optimize_continuous

    def overwrite():
        result = original_solver()
        lens.updater.set_material(material(1.7), 1)
        return result

    monkeypatch.setattr(optimizer, "_optimize_continuous", overwrite)
    result = optimizer.run(
        global_samples=2, num_neighbours=1, maxiter=10, disp=False, verbose=False
    )
    assert result.glass_expert.outcome == "no_candidate"
    assert not result.glass_expert.candidate_eligible
    assert all(
        "no longer matches" in trial.message for trial in result.glass_expert.trials
    )
    assert lens.surfaces[1].material_post is original


@pytest.mark.parametrize("phase", ["seed", "global", "local"])
def test_cancel_during_coordinate_selection(phase, monkeypatch):
    from optiland.optimization.optimizer.scipy import glass_expert

    lens, problem = make_problem(selection=pool(8))
    original = lens.surfaces[1].material_post
    optimizer = GlassExpert(problem)
    distance = glass_expert._distance
    selected = []

    def measured(*args):
        value = distance(*args)
        if optimizer._phase == phase:
            selected.append(value)
        return value

    def checkpoint():
        if selected:
            raise GlassExpertCancelled("Stop during distance selection")

    monkeypatch.setattr(glass_expert, "_distance", measured)
    with pytest.raises(GlassExpertCancelled, match="distance selection"):
        optimizer.run(
            global_samples=3,
            num_neighbours=2,
            maxiter=15,
            checkpoint=checkpoint,
            disp=False,
            verbose=False,
        )
    assert selected and optimizer.last_report.outcome == "cancelled"
    assert lens.surfaces[1].material_post is original
    assert scalar(lens.surfaces[1].geometry.radius) == 50.0


def test_last_checkpoint_cannot_invalidate_final_prescription():
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    optimizer = GlassExpert(problem)
    changed = []

    def checkpoint():
        if optimizer.last_report is not None and not changed:
            changed.append(True)
            lens.updater.set_radius(145.0, 1)
            lens.updater.update()

    with pytest.raises(ValueError, match="final notification"):
        optimizer.run(
            global_samples=2,
            num_neighbours=1,
            maxiter=20,
            checkpoint=checkpoint,
            disp=False,
            verbose=False,
        )
    assert changed and optimizer.last_report.outcome == "failed"
    assert not optimizer.last_report.candidate_eligible
    assert lens.surfaces[1].material_post is original
    assert scalar(lens.surfaces[1].geometry.radius) == 50.0


def test_reentrant_run_rejected_and_outer_state_restored():
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    optimizer = GlassExpert(problem)

    with pytest.raises(RuntimeError, match="already running"):
        optimizer.run(
            global_samples=2,
            progress=lambda _event: optimizer.run(),
            disp=False,
            verbose=False,
        )
    assert lens.surfaces[1].material_post is original
    assert optimizer.last_report.outcome == "failed"
    assert not optimizer._running


@pytest.mark.parametrize(
    "case", ["bounds", "image", "duplicate", "empty-legacy", "missing-waves", "payload"]
)
def test_preflight_error_paths_preserve_target(case):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    expected = None
    if case == "bounds":
        problem.variables[1].min_val = float("nan")
        expected = "bounds"
    elif case == "image":
        problem.add_variable(lens, "material", surface_number=3, glass_selection=pool())
        expected = "outgoing medium"
    elif case == "duplicate":
        problem.add_variable(lens, "material", surface_number=1, glass_selection=pool())
        expected = "distinct"
    elif case == "empty-legacy":
        problem.variables[0].variable.glass_selection = []
        expected = "nonempty"
    elif case == "missing-waves":
        lens.wavelengths.wavelengths.clear()
        expected = "wavelengths"
    else:
        changed = replace(
            pool().candidates[0], owned_data=capture_material(material(1.8))
        )
        problem.add_variable(
            lens, "material", surface_number=2, glass_selection=MaterialPool((changed,))
        )
        expected = "conflicting payloads"
    manager = problem.variables
    with pytest.raises(ValueError, match=expected):
        run(problem)
    assert problem.variables is manager and lens.surfaces[1].material_post is original


def test_multiple_optics_and_shared_candidate_validation(monkeypatch):
    from optiland.optic.optic_updater import OpticUpdater

    shared = pool()
    lens, problem = make_problem(selection=shared)
    other, _unused = make_problem(selection=shared)
    problem.add_variable(other, "material", surface_number=1, glass_selection=shared)
    problem.add_operand("f2", target=60.0, input_data={"optic": other})
    updates = []
    update = OpticUpdater.update

    def tracked(updater):
        updates.append(updater.optic)
        return update(updater)

    monkeypatch.setattr(OpticUpdater, "update", tracked)
    result = run(problem)
    assert result.glass_expert.candidate_eligible
    assert len(result.glass_expert.candidate.materials) == 2
    assert any(optic is other for optic in updates)
    assert all(
        choice.key in {item.key for item in shared.candidates}
        for choice in result.glass_expert.candidate.materials
    )


@pytest.mark.parametrize("custom_type", [False, True])
def test_custom_updater_rejected_before_it_can_change_topology(custom_type):
    from optiland.optic.optic_updater import OpticUpdater

    lens, problem = make_problem()
    surfaces = tuple(lens.surfaces)
    original = lens.surfaces[1].material_post
    calls = []
    if custom_type:

        class CustomUpdater(OpticUpdater):
            def update(self):
                calls.append(True)

        lens.updater = CustomUpdater(lens)
    else:
        lens.updater.update = lambda: calls.append(True)
    with pytest.raises(ValueError, match="built-in optic updater"):
        run(problem)
    assert not calls
    assert tuple(lens.surfaces) == surfaces
    assert lens.surfaces[1].material_post is original


def test_cancel_after_prior_assignment_restores_assigned_key():
    lens, problem = make_problem()
    first = run(problem)
    original_material = lens.surfaces[1].material_post
    original_key = problem.variables[0].value
    assert original_key == first.glass_expert.candidate.materials[0].key
    with pytest.raises(GlassExpertCancelled):
        run(
            problem,
            progress=lambda event: (_ for _ in ()).throw(GlassExpertCancelled()),
        )
    assert lens.surfaces[1].material_post is original_material
    assert problem.variables[0].value == original_key


def test_unrepresentable_percentage_keeps_finite_merits(monkeypatch):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    monkeypatch.setattr(
        problem,
        "sum_squared",
        lambda: be.array(
            1e-310 if lens.surfaces[1].material_post is original else 1e300
        ),
    )
    result = run(problem, maxiter=2)
    assert result.glass_expert.candidate_eligible
    assert result.glass_expert.outcome == "no_improvement"
    assert result.glass_expert.improvement_percent is None
    assert any("Percentage" in warning for warning in result.glass_expert.warnings)
    assert result.glass_expert.baseline.merit == 1e-310
    assert result.glass_expert.candidate.merit == 1e300
    json.dumps(asdict(result.glass_expert), allow_nan=False)


def test_supported_plot_flags_keep_exact_distinct_points(monkeypatch, capsys):
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    from optiland.optimization.optimizer.scipy import glass_expert

    stages, figures = [], []

    class Plotter:
        def __init__(self, optimizer):
            self.optimizer = optimizer

        def initialize(self):
            stages.append("initialize")

        def update(self):
            stages.append("update")

        def finalize(self):
            stages.append("finalize")

    monkeypatch.setattr(glass_expert, "LiveOptimizationPlotter", Plotter)
    monkeypatch.setattr(Figure, "show", lambda figure: figures.append(figure))
    _lens, problem = make_problem(selection=pool(coincident=True))
    try:
        result = run(problem, plot=True, plot_glass_map=True, verbose=True, disp=True)
        assert result.glass_expert.candidate_eligible
        assert stages[0] == "initialize" and stages[-1] == "finalize"
        assert stages.count("update") > 1
        assert len(figures) == 1
        axes = figures[0].axes[0]
        assert axes.xaxis_inverted()
        assert [len(collection.get_offsets()) for collection in axes.collections] == [
            2,
            2,
        ]
        assert "GlassExpert completed" in capsys.readouterr().out
    finally:
        for figure in figures:
            plt.close(figure)


def test_optional_torch_and_qt_unavailable_public_example():
    example = Path(__file__).parents[1] / "docs/samples/glassmap/typed_glass_expert.py"
    script = """
import importlib.abc, runpy, sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'torch', 'PySide6'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockOptional())
namespace = runpy.run_path(sys.argv[1])
lens, report = namespace['run_example']()
assert report.candidate_eligible and report.improved
assert 'torch' not in sys.modules and 'PySide6' not in sys.modules
print('owned-core-replay-ok')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(example)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "owned-core-replay-ok" in completed.stdout


def test_out_of_bounds_solver_return_never_becomes_a_candidate(monkeypatch):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    optimizer = GlassExpert(problem)

    def invalid_solver():
        lens.updater.set_radius(200.0, 1)
        return OptimizeResult(success=True, message="invalid bound", nit=1)

    monkeypatch.setattr(optimizer, "_optimize_continuous", invalid_solver)
    result = optimizer.run(global_samples=2, disp=False, verbose=False)
    assert result.glass_expert.outcome == "no_candidate"
    assert all("bounds" in trial.message for trial in result.glass_expert.trials)
    assert lens.surfaces[1].material_post is original
    assert scalar(lens.surfaces[1].geometry.radius) == 50.0


def test_public_catalog_name_cancellation_restores_original_material():
    lens, problem = make_problem(selection=["N-BK7", "N-SF11"])
    original = lens.surfaces[1].material_post
    manager = problem.variables

    def stop(_event):
        raise GlassExpertCancelled("Cancel a public name-based run")

    with pytest.raises(GlassExpertCancelled, match="name-based"):
        run(problem, progress=stop)
    assert problem.variables is manager
    assert lens.surfaces[1].material_post is original
    assert scalar(lens.surfaces[1].geometry.radius) == 50.0


def test_unreproducible_restored_merit_rejected_and_rolled_back(monkeypatch):
    lens, problem = make_problem()
    original = lens.surfaces[1].material_post
    optimizer = GlassExpert(problem)
    merit = optimizer._merit

    def drifting_merit(*, checkpoints=True):
        value = merit(checkpoints=checkpoints)
        final_trial_recorded = bool(
            optimizer._trials and optimizer._trials[-1].phase == "final"
        )
        return value + 1 if final_trial_recorded else value

    monkeypatch.setattr(optimizer, "_merit", drifting_merit)
    with pytest.raises(ValueError, match="reproduce"):
        optimizer.run(global_samples=2, maxiter=20, disp=False, verbose=False)
    assert optimizer.last_report.outcome == "failed"
    assert not optimizer.last_report.candidate_eligible
    assert lens.surfaces[1].material_post is original
    assert scalar(lens.surfaces[1].geometry.radius) == 50.0
