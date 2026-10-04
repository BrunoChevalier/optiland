"""Regression tests for X-first radiant-intensity maps and normalization."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pytest

import optiland.backend as be
from optiland.analysis import RadiantIntensity
from optiland.optic import Optic

from .utils import assert_allclose


@pytest.fixture(params=["histogram", "differentiable"])
def accumulation_mode(request, set_test_backend):
    """Exercise both accumulation paths without changing global test fixtures."""
    if be.get_backend() == "numpy":
        if request.param == "differentiable":
            pytest.skip("Bilinear accumulation is Torch-only")
        yield request.param
        return

    old_mode = be.grad_mode.requires_grad
    if request.param == "histogram":
        be.grad_mode.disable()
    else:
        be.grad_mode.enable()
    try:
        yield request.param
    finally:
        if old_mode:
            be.grad_mode.enable()
        else:
            be.grad_mode.disable()


def _make_analysis(shape, absolute, angles_x=None, powers=None):
    """Analyze deterministic recorded unit rays, independent of the tracer."""
    optic = Optic()
    optic.fields.set_type("angle")
    optic.fields.add(y=0)
    optic.wavelengths.add(0.55, is_primary=True)
    optic.surfaces.add(index=0)
    surface = optic.surfaces[0]

    if angles_x is None:
        angles_x = be.array([-23.0, 14.0])
    if powers is None:
        powers = be.array([2.0, 3.0])
    angles_y = be.array([-2.0, 9.0])[: len(angles_x)]
    tx, ty = be.tan(be.radians(angles_x)), be.tan(be.radians(angles_y))
    surface.N = 1.0 / be.sqrt(1.0 + tx**2 + ty**2)
    surface.L = tx * surface.N
    surface.M = ty * surface.N
    surface.intensity = powers

    return RadiantIntensity(
        optic,
        fields=[(0.0, 0.0)],
        wavelengths=[0.55],
        num_angular_bins_X=shape[0],
        num_angular_bins_Y=shape[1],
        angle_X_min=-50,
        angle_X_max=50,
        angle_Y_min=-15,
        angle_Y_max=45,
        use_absolute_units=absolute,
        skip_trace=True,
    )


def _reference_power_map(x_edges, y_edges, mode, angles_x=(-23.0, 14.0)):
    """Compute an independent X-first histogram or four-neighbor reference."""
    angles_y, powers = [-2.0, 9.0], [2.0, 3.0]
    if mode == "histogram":
        return np.histogram2d(angles_x, angles_y, [x_edges, y_edges], weights=powers)[0]

    result = np.zeros((len(x_edges) - 1, len(y_edges) - 1))
    xc, yc = (x_edges[:-1] + x_edges[1:]) / 2, (y_edges[:-1] + y_edges[1:]) / 2
    for x, y, power in zip(angles_x, angles_y, powers, strict=True):
        ix = np.clip(np.searchsorted(xc, x, side="right") - 1, 0, len(xc) - 2)
        iy = np.clip(np.searchsorted(yc, y, side="right") - 1, 0, len(yc) - 2)
        # Preserve the backend's existing denominator regularization.
        wx = (x - xc[ix]) / (xc[ix + 1] - xc[ix] + 1e-9)
        wy = (y - yc[iy]) / (yc[iy + 1] - yc[iy] + 1e-9)
        for dx, dy, weight in (
            (0, 0, (1 - wx) * (1 - wy)),
            (0, 1, (1 - wx) * wy),
            (1, 0, wx * (1 - wy)),
            (1, 1, wx * wy),
        ):
            result[ix + dx, iy + dy] += power * weight
    return result


def _solid_angles(x_edges, y_edges):
    """Existing midpoint solid-angle rule, evaluated by explicit broadcasting."""
    x, y = np.deg2rad(x_edges), np.deg2rad(y_edges)
    tx2 = np.tan((x[:-1] + x[1:]) / 2)[:, None] ** 2
    ty2 = np.tan((y[:-1] + y[1:]) / 2)[None, :] ** 2
    jacobian = (1 + tx2) * (1 + ty2) / (1 + tx2 + ty2) ** 1.5
    return jacobian * (x[1] - x[0]) * (y[1] - y[0])


@pytest.mark.parametrize("shape", [(5, 3), (5, 5)])
@pytest.mark.parametrize("absolute", [False, True])
def test_map_axes_and_bin_values(accumulation_mode, shape, absolute):
    analysis = _make_analysis(shape, absolute)
    result, xe, ye, xc, yc = analysis.data[0][0]
    assert result.shape == shape
    assert len(xc) == shape[0]
    assert len(yc) == shape[1]
    xe, ye = be.to_numpy(xe), be.to_numpy(ye)
    power = _reference_power_map(xe, ye, accumulation_mode)
    expected = power / _solid_angles(xe, ye) if absolute else power
    assert_allclose(result, expected, rtol=1e-12, atol=1e-12)

    recovered_power = (
        be.to_numpy(result) * _solid_angles(xe, ye) if absolute else be.to_numpy(result)
    )
    assert np.sum(recovered_power) == pytest.approx(5.0, rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_empty_and_clipped_maps_keep_axes(accumulation_mode, absolute, empty):
    shape = (5, 3)
    angles_x = be.array([]) if empty else be.array([-23.0, 14.0])
    powers = be.array([]) if empty else be.zeros(2)
    analysis = _make_analysis(shape, absolute, angles_x=angles_x, powers=powers)
    result = analysis.data[0][0][0]
    assert result.shape == shape
    assert_allclose(result, np.zeros(shape), rtol=0, atol=0)


@pytest.mark.parametrize("cross_section", [None, ("cross-x", 0), ("cross-y", 3)])
def test_rectangular_map_views(accumulation_mode, cross_section):
    analysis = _make_analysis((5, 3), False)
    result, _, _, xc, yc = [be.to_numpy(v) for v in analysis.data[0][0]]
    fig = None
    try:
        fig, axes = analysis.view(
            cross_section=cross_section, normalize=False, show=False
        )
        if cross_section is None:
            ax_map, ax_cs = axes[0, 0]
            assert_allclose(ax_map.images[0].get_array(), result.T, rtol=0, atol=0)
            line = ax_cs.lines[0]
            expected_x, expected_y = xc, result[:, len(yc) // 2]
        else:
            line = axes[0, 0].lines[0]
            if cross_section[0] == "cross-x":
                expected_x, expected_y = xc, result[:, cross_section[1]]
            else:
                expected_x, expected_y = yc, result[cross_section[1], :]
        assert_allclose(line.get_xdata(), expected_x, rtol=0, atol=0)
        assert_allclose(line.get_ydata(), expected_y, rtol=0, atol=0)
    finally:
        if fig is not None:
            plt.close(fig)


def test_differentiable_map_gradient(set_test_backend):
    if be.get_backend() != "torch":
        pytest.skip("Autograd is Torch-only")

    angles_x = be.array([-23.0, 14.0])
    angles_x.requires_grad_(True)
    analysis = _make_analysis((5, 3), True, angles_x=angles_x)
    result, xe, ye, _, _ = analysis.data[0][0]
    spatial_weights = np.arange(15.0).reshape(5, 3) ** 2
    loss = be.sum(result * be.array(spatial_weights))
    loss.backward()
    gradient = be.to_numpy(angles_x.grad)
    assert np.all(np.isfinite(gradient))

    xe, ye = be.to_numpy(xe), be.to_numpy(ye)
    solid_angles = _solid_angles(xe, ye)

    def reference_loss(angles):
        power = _reference_power_map(xe, ye, "differentiable", angles)
        return np.sum(power / solid_angles * spatial_weights)

    # Interior points avoid nondifferentiable bin-selection boundaries.
    for step in (1e-2, 1e-3, 1e-4):
        differences = []
        for i in range(2):
            plus, minus = np.array([-23.0, 14.0]), np.array([-23.0, 14.0])
            plus[i] += step
            minus[i] -= step
            differences.append(
                (reference_loss(plus) - reference_loss(minus)) / (2 * step)
            )
        assert_allclose(gradient, differences, rtol=1e-8, atol=1e-8)
