"""Regression tests for equality and inequality tolerancing objectives."""

from __future__ import annotations

import pytest

import optiland.backend as be
from optiland.optic import Optic
from optiland.optimization.operand.operand import operand_registry
from optiland.optimization.scaling.identity import IdentityScaler
from optiland.samples.simple import Edmund_49_847
from optiland.tolerancing import Tolerancing
from tests.utils import assert_allclose


@pytest.fixture
def track_system(set_test_backend):
    """Use a known 10 + 15 mm track, independent of optical power or tracing."""
    optic = Optic()
    optic.surfaces.add(index=0, thickness=be.inf)
    optic.surfaces.add(index=1, thickness=10.0, is_stop=True)
    optic.surfaces.add(index=2, thickness=15.0)
    optic.surfaces.add(index=3)
    optic.set_aperture("EPD", 1.0)
    optic.fields.set_type("angle")
    optic.fields.add(y=0)
    optic.wavelengths.add(0.55, is_primary=True)
    assert_allclose(optic.total_track, 25.0, rtol=0, atol=1e-12)
    return optic


@pytest.mark.parametrize(
    "bounds,track,expected",
    [
        ({"min_val": 20.0}, 18.0, 2.0),
        ({"min_val": 20.0}, 20.0, 0.0),
        ({"min_val": 20.0}, 27.0, 0.0),
        ({"max_val": 30.0}, 32.0, 2.0),
        ({"max_val": 30.0}, 30.0, 0.0),
        ({"max_val": 30.0}, 27.0, 0.0),
        ({"min_val": 20.0, "max_val": 30.0}, 18.0, 2.0),
        ({"min_val": 20.0, "max_val": 30.0}, 20.0, 0.0),
        ({"min_val": 20.0, "max_val": 30.0}, 27.0, 0.0),
        ({"min_val": 20.0, "max_val": 30.0}, 30.0, 0.0),
        ({"min_val": 20.0, "max_val": 30.0}, 32.0, 2.0),
        ({"min_val": 30.0}, 25.0, 5.0),
        ({"max_val": 20.0}, 25.0, 5.0),
        ({"min_val": 30.0, "max_val": 30.0}, 25.0, 5.0),
        ({"min_val": 0.0}, 25.0, 0.0),
        ({"max_val": 0.0}, 25.0, 25.0),
    ],
)
def test_bounds_define_violation_not_nominal_target(
    track_system, bounds, track, expected
):
    tolerancing = Tolerancing(track_system)
    tolerancing.add_operand(
        "total_track", input_data={"optic": track_system}, weight=2.0, **bounds
    )
    operand = tolerancing.operands[0]
    assert operand.target is None
    assert operand.min_val == bounds.get("min_val")
    assert operand.max_val == bounds.get("max_val")
    track_system.updater.set_thickness(track - 10.0, 2)
    assert_allclose(operand.value, track, rtol=0, atol=1e-12)
    assert_allclose(operand.delta(), expected, rtol=0, atol=1e-12)
    assert_allclose(operand.fun(), 2.0 * expected, rtol=0, atol=1e-12)


@pytest.mark.parametrize("target", [None, 0.0, 24.0])
def test_equality_targets_are_preserved(track_system, target):
    tolerancing = Tolerancing(track_system)
    tolerancing.add_operand(
        "total_track", target=target, input_data={"optic": track_system}
    )
    expected_target = 25.0 if target is None else target
    operand = tolerancing.operands[0]
    assert_allclose(operand.target, expected_target, rtol=0, atol=1e-12)
    track_system.updater.set_thickness(17.0, 2)
    assert_allclose(operand.target, expected_target, rtol=0, atol=1e-12)
    assert_allclose(operand.delta(), 27.0 - expected_target, rtol=0, atol=1e-12)


@pytest.mark.parametrize(
    "kwargs,expected_calls",
    [({}, 1), ({"target": 0.0}, 0), ({"min_val": 0.0}, 0), ({"max_val": 20.0}, 0)],
)
def test_operand_constructor_owns_nominal_capture(
    track_system, monkeypatch, kwargs, expected_calls
):
    calls = []

    def counting_metric(optic):
        calls.append(1)
        return optic.total_track

    monkeypatch.setitem(operand_registry._registry, "counting_track", counting_metric)
    tolerancing = Tolerancing(track_system)
    tolerancing.add_operand(
        "counting_track", input_data={"optic": track_system}, **kwargs
    )
    assert len(calls) == expected_calls


@pytest.mark.parametrize(
    "kwargs,target,expected_delta",
    [({}, 25.0, 0.0), ({"target": 0.0}, 0.0, 25.0), ({"min_val": 30.0}, None, 5.0)],
)
def test_operands_without_input_data(
    track_system, monkeypatch, kwargs, target, expected_delta
):
    monkeypatch.setitem(operand_registry._registry, "constant_metric", lambda: 25.0)
    tolerancing = Tolerancing(track_system)
    tolerancing.add_operand("constant_metric", **kwargs)
    operand = tolerancing.operands[0]
    assert operand.input_data == {}
    assert operand.target == target
    assert_allclose(operand.delta(), expected_delta, rtol=0, atol=1e-12)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"target": 25.0, "min_val": 20.0}, "equality and inequality"),
        ({"target": 0.0, "max_val": 30.0}, "equality and inequality"),
        ({"min_val": 30.0, "max_val": 20.0}, "min_val is higher"),
    ],
)
def test_invalid_target_definitions_remain_rejected(track_system, kwargs, message):
    tolerancing = Tolerancing(track_system)
    with pytest.raises(ValueError, match=message):
        tolerancing.add_operand(
            "total_track", input_data={"optic": track_system}, **kwargs
        )
    assert tolerancing.operands == []


@pytest.mark.parametrize("method", ["generic", "least_squares"])
@pytest.mark.parametrize("batching", [False, True])
@pytest.mark.parametrize("track", [18.0, 27.0, 32.0])
def test_compensation_respects_allowed_interval(track_system, method, batching, track):
    tolerancing = Tolerancing(track_system, method=method, tol=1e-8)
    tolerancing.add_operand(
        "total_track",
        input_data={"optic": track_system},
        min_val=20.0,
        max_val=30.0,
    )
    tolerancing.add_compensator("thickness", surface_number=2, scaler=IdentityScaler())
    if not batching:
        tolerancing.compensator.disable_batching()
    track_system.updater.set_thickness(track - 10.0, 2)

    result = tolerancing.apply_compensators()
    final_track = float(be.to_numpy(track_system.total_track))
    assert 20.0 - 1e-5 <= final_track <= 30.0 + 1e-5
    assert_allclose(tolerancing.operands[0].delta(), 0.0, rtol=0, atol=1e-5)
    final_spacing = track_system.surfaces.get_thickness(2)[0]
    assert_allclose(final_track, 10.0 + final_spacing, rtol=0, atol=1e-12)
    assert_allclose(
        result["C0: Thickness, Surface 2"], final_spacing, rtol=0, atol=1e-12
    )
    if 20.0 <= track <= 30.0:
        # An acceptable perturbed design must not be driven back to nominal.
        assert_allclose(final_track, track, rtol=0, atol=1e-12)


@pytest.mark.parametrize("method", ["generic", "least_squares"])
@pytest.mark.parametrize("batching", [False, True])
@pytest.mark.parametrize(
    "bounds",
    [{"min_val": 26.0}, {"max_val": 24.0}, {"min_val": 26.0, "max_val": 30.0}],
)
def test_nominal_bound_violation_is_compensated(track_system, method, batching, bounds):
    tolerancing = Tolerancing(track_system, method=method, tol=1e-8)
    tolerancing.add_operand("total_track", input_data={"optic": track_system}, **bounds)
    tolerancing.add_compensator("thickness", surface_number=2, scaler=IdentityScaler())
    if not batching:
        tolerancing.compensator.disable_batching()

    tolerancing.apply_compensators()
    final_track = float(be.to_numpy(track_system.total_track))
    assert final_track >= bounds.get("min_val", -be.inf) - 1e-5
    assert final_track <= bounds.get("max_val", be.inf) + 1e-5
    assert_allclose(tolerancing.operands[0].delta(), 0.0, rtol=0, atol=1e-5)


@pytest.mark.parametrize("method", ["generic", "least_squares"])
def test_real_lens_compensation_satisfies_efl_limit(set_test_backend, method):
    optic = Edmund_49_847()
    nominal = float(be.to_numpy(optic.paraxial.f2()))
    max_efl = nominal - 2.0
    tolerancing = Tolerancing(optic, method=method, tol=1e-8)
    tolerancing.add_operand(
        "f2", input_data={"optic": optic}, min_val=20.0, max_val=max_efl
    )
    tolerancing.add_compensator("radius", surface_number=1, min_val=15.0, max_val=25.0)

    tolerancing.apply_compensators()
    final_efl = float(be.to_numpy(optic.paraxial.f2()))
    assert 20.0 - 1e-5 <= final_efl <= max_efl + 1e-5
    assert nominal - final_efl > 1.9
    assert_allclose(tolerancing.operands[0].delta(), 0.0, rtol=0, atol=1e-5)
