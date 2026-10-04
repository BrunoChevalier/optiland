"""Tolerancing runners must not leak trial state into the nominal optic."""

from __future__ import annotations

import pandas as pd
import pytest

import optiland.backend as be
from optiland.optic import Optic
from optiland.optimization.scaling.identity import IdentityScaler
from optiland.tolerancing import Tolerancing
from optiland.tolerancing.monte_carlo import MonteCarlo
from optiland.tolerancing.perturbation import RangeSampler, ScalarSampler
from optiland.tolerancing.sensitivity_analysis import SensitivityAnalysis
from tests.utils import assert_allclose


@pytest.fixture
def track_system(set_test_backend):
    """The two finite spacings have the exact nominal sum 10 + 15 mm."""
    optic = Optic()
    optic.surfaces.add(index=0, thickness=be.inf)
    optic.surfaces.add(index=1, thickness=10.0, is_stop=True)
    optic.surfaces.add(index=2, thickness=15.0)
    optic.surfaces.add(index=3)
    optic.set_aperture("EPD", 1.0)
    optic.fields.set_type("angle")
    optic.fields.add(y=0)
    optic.wavelengths.add(0.55, is_primary=True)
    return optic


@pytest.fixture(
    params=[MonteCarlo, SensitivityAnalysis], ids=["monte_carlo", "sensitivity"]
)
def runner_type(request):
    return request.param


def make_analysis(optic, runner_type, compensated=False, method="generic", steps=2):
    tolerancing = Tolerancing(optic, method=method, tol=1e-8)
    tolerancing.add_operand("total_track", input_data={"optic": optic}, target=25.0)
    tolerancing.add_perturbation(
        "thickness", RangeSampler(17.0, 19.0, steps), surface_number=2
    )
    if compensated:
        tolerancing.add_compensator(
            "thickness", surface_number=1, scaler=IdentityScaler()
        )
    return runner_type(tolerancing)


def run(analysis, iterations=2):
    if isinstance(analysis, MonteCarlo):
        analysis.run(iterations)
    else:
        analysis.run()


def assert_nominal(analysis):
    optic = analysis.tolerancing.optic
    assert_allclose(optic.surfaces.get_thickness(1), [10.0], rtol=0, atol=1e-12)
    assert_allclose(optic.surfaces.get_thickness(2), [15.0], rtol=0, atol=1e-12)
    assert_allclose(optic.total_track, 25.0, rtol=0, atol=1e-12)
    for perturbation in analysis.tolerancing.perturbations:
        assert_allclose(
            perturbation.value,
            perturbation.variable.variable.get_value(),
            rtol=0,
            atol=1e-12,
        )


@pytest.mark.parametrize("compensated", [False, True])
def test_success_restores_nominal_and_keeps_trial_results(
    track_system, runner_type, compensated, monkeypatch
):
    analysis = make_analysis(track_system, runner_type, compensated)
    starts = []
    perturbation = analysis.tolerancing.perturbations[0]
    original_apply = perturbation.apply

    def apply():
        starts.append(float(be.to_numpy(track_system.total_track)))
        original_apply()

    monkeypatch.setattr(perturbation, "apply", apply)
    if compensated:
        # Deterministic compensation separates runner lifecycle from solver behavior.
        def compensate():
            spacing = float(be.to_numpy(track_system.surfaces.get_thickness(2)[0]))
            track_system.updater.set_thickness(25.0 - spacing, 1)
            return {"C0: Thickness, Surface 1": 25.0 - spacing}

        monkeypatch.setattr(analysis.tolerancing, "apply_compensators", compensate)

    for _ in range(2):
        run(analysis)
        results = analysis.get_results()
        perturbation_column = (
            "Thickness, Surface 2"
            if runner_type is MonteCarlo
            else "perturbation_value"
        )
        assert_allclose(
            results[perturbation_column].to_numpy(dtype=float),
            [17, 19],
            rtol=0,
            atol=1e-12,
        )
        expected_track = [25, 25] if compensated else [27, 29]
        assert_allclose(
            results["0: total track"].to_numpy(), expected_track, rtol=0, atol=1e-12
        )
        if compensated:
            assert_allclose(
                results["C0: Thickness, Surface 1"].to_numpy(),
                [8, 6],
                rtol=0,
                atol=1e-12,
            )
        assert_nominal(analysis)
    assert starts == [25.0] * 4


@pytest.mark.parametrize("stage", ["sampling", "compensation", "evaluation"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_failure_restores_nominal_and_preserves_error_and_completed_results(
    track_system, runner_type, stage, error_type, monkeypatch
):
    analysis = make_analysis(track_system, runner_type, compensated=True)
    completed = pd.DataFrame({"previous_completed_result": [123.0]})
    analysis._results = completed
    error = error_type("injected trial failure")
    observed_tracks = []

    def fail():
        observed_tracks.append(float(be.to_numpy(track_system.total_track)))
        raise error

    def compensate():
        track_system.updater.set_thickness(8.0, 1)
        if stage == "compensation":
            fail()
        return {"C0: Thickness, Surface 1": 8.0}

    monkeypatch.setattr(analysis.tolerancing, "apply_compensators", compensate)
    if stage == "sampling":
        sampler = RangeSampler(11, 12, 2)
        monkeypatch.setattr(sampler, "sample", fail)
        analysis.tolerancing.add_perturbation("thickness", sampler, surface_number=1)
    elif stage == "evaluation":
        monkeypatch.setattr(analysis.tolerancing, "evaluate", fail)

    with pytest.raises(error_type) as caught:
        run(analysis)
    assert caught.value is error
    sampling_in_monte_carlo = stage == "sampling" and runner_type is MonteCarlo
    assert observed_tracks == ([27.0] if sampling_in_monte_carlo else [25.0])
    assert analysis.get_results() is completed
    expected_index = (
        2 if stage == "sampling" and runner_type is SensitivityAnalysis else 1
    )
    assert analysis.tolerancing.perturbations[0].sampler.index == expected_index
    assert_nominal(analysis)


def test_late_failure_does_not_publish_partial_results(
    track_system, runner_type, monkeypatch
):
    analysis = make_analysis(track_system, runner_type)
    run(analysis)
    completed = analysis.get_results()
    completed_copy = completed.copy(deep=True)
    original_evaluate = analysis.tolerancing.evaluate
    calls = []

    def evaluate():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("second trial failed")
        return original_evaluate()

    monkeypatch.setattr(analysis.tolerancing, "evaluate", evaluate)
    with pytest.raises(RuntimeError, match="second trial failed"):
        run(analysis)
    assert len(calls) == 2
    assert analysis.get_results() is completed
    pd.testing.assert_frame_equal(completed, completed_copy)
    assert_nominal(analysis)


def test_invalid_late_sensitivity_sampler_restores_nominal(track_system):
    analysis = make_analysis(track_system, SensitivityAnalysis)
    analysis.tolerancing.add_perturbation(
        "thickness", ScalarSampler(11), surface_number=1
    )
    with pytest.raises(ValueError, match="Only range samplers"):
        analysis.run()
    assert analysis.get_results().empty
    assert_nominal(analysis)


def test_result_construction_failure_restores_nominal(
    track_system, runner_type, monkeypatch
):
    analysis = make_analysis(track_system, runner_type)
    run(analysis)
    completed = analysis.get_results()
    error = RuntimeError("DataFrame construction failed")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(pd, "DataFrame", fail)
    with pytest.raises(RuntimeError) as caught:
        run(analysis)
    assert caught.value is error
    assert analysis.get_results() is completed
    assert_nominal(analysis)


def test_multiple_perturbations_keep_analysis_semantics(track_system, runner_type):
    analysis = make_analysis(track_system, runner_type)
    analysis.tolerancing.add_perturbation(
        "thickness", RangeSampler(11.0, 12.0, 2), surface_number=1
    )
    run(analysis)
    results = analysis.get_results()
    if runner_type is MonteCarlo:
        expected = [28.0, 31.0]  # Both perturbations are applied simultaneously.
        assert_allclose(
            results["Thickness, Surface 1"].to_numpy(), [11, 12], rtol=0, atol=1e-12
        )
        assert_allclose(
            results["Thickness, Surface 2"].to_numpy(), [17, 19], rtol=0, atol=1e-12
        )
    else:
        expected = [27.0, 29.0, 26.0, 27.0]  # Each sweep perturbs only one spacing.
        assert results["perturbation_type"].tolist() == [
            "Thickness, Surface 2",
            "Thickness, Surface 2",
            "Thickness, Surface 1",
            "Thickness, Surface 1",
        ]
    assert_allclose(results["0: total track"].to_numpy(), expected, rtol=0, atol=1e-12)
    assert_nominal(analysis)


def test_zero_trials_restore_registered_nominal_not_run_entry_state(
    track_system, runner_type
):
    analysis = make_analysis(track_system, runner_type, compensated=True, steps=0)
    track_system.updater.set_thickness(11.0, 1)
    track_system.updater.set_thickness(16.0, 2)
    # Reset is deliberately scoped to registered variables, not a full-optic snapshot.
    track_system.surfaces[1].geometry.radius = 50.0
    run(analysis, iterations=0)
    assert analysis.get_results().empty
    assert_nominal(analysis)
    assert_allclose(track_system.surfaces[1].geometry.radius, 50.0, rtol=0, atol=1e-12)


@pytest.mark.parametrize("method", ["generic", "least_squares"])
def test_real_compensator_results_survive_restoration(
    track_system, runner_type, method
):
    analysis = make_analysis(track_system, runner_type, compensated=True, method=method)
    run(analysis)
    results = analysis.get_results()
    assert_allclose(results["0: total track"].to_numpy(), [25, 25], rtol=0, atol=1e-5)
    assert_allclose(
        results["C0: Thickness, Surface 1"].to_numpy(), [8, 6], rtol=0, atol=1e-5
    )
    assert_nominal(analysis)
