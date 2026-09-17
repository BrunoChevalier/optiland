"""Bounds, catalog thermal data and transmission keep distinct contracts."""

from __future__ import annotations

import json
import math

import pytest

import optiland.backend as be
from optiland.materials import BaseMaterial, DataMaterial
from tests.utils import assert_allclose


def _thermal():
    return {
        "kind": "constant",
        "coefficients": [2e-6],
        "reference_temperature_c": 20,
    }


def _material(factory, bounds, extinction):
    options = {
        "bounds": bounds,
        "thermal": _thermal(),
        "extinction": extinction,
        "name": "owned optical data",
        "metadata": {"source": "synthetic"},
    }
    if factory == "samples":
        return DataMaterial.from_samples([0.4, 0.8], [1.6, 1.4], **options)
    if factory == "coefficients":
        return DataMaterial.from_coefficients(
            "formula 5", [1.8, -0.5, 1], wavelength_range=(0.4, 0.8), **options
        )
    return DataMaterial(
        {
            "dispersion": {
                "kind": "tabulated",
                "wavelengths_um": [0.4, 0.8],
                "indices": [1.6, 1.4],
            },
            "thermal": options.pop("thermal"),
            "extinction": options.pop("extinction"),
        },
        **options,
    )


@pytest.mark.parametrize("factory", ["samples", "coefficients", "definition"])
@pytest.mark.parametrize("bounds", ["raise", "clamp"])
def test_thermal_and_measured_k_preserve_bounds_in_native_transport(
    factory, bounds, set_test_backend
):
    material = _material(
        factory,
        bounds,
        {
            "kind": "tabulated_k",
            "wavelengths_um": [0.5, 0.7],
            "values": [1e-5, 3e-5],
        },
    )
    restored = BaseMaterial.from_dict(json.loads(json.dumps(material.to_dict())))
    for candidate in (material, restored):
        assert candidate.bounds == bounds
        assert candidate.definition == material.definition
        assert candidate.metadata == {"source": "synthetic"}
        assert candidate.spectral_range("n") == (0.4, 0.8)
        assert candidate.spectral_range("k") == (0.5, 0.7)
        for temperature in (20, 35, 20):
            # Repeating the reference query also checks that the thermal query
            # cannot replace cached reference-temperature results.
            assert_allclose(
                candidate.n(0.6, temperature=temperature),
                1.5 + (temperature - 20) * 2e-6,
            )
        assert_allclose(candidate.k(0.6), 2e-5)
        if bounds == "clamp":
            assert_allclose(candidate.k(0.45), 1e-5)
            expected = math.exp(-4 * math.pi * 1e-5 * 2000 / 0.45)
            assert_allclose(candidate.internal_transmittance(0.45, 2), expected)
        else:
            with pytest.raises(ValueError, match="range"):
                candidate.internal_transmittance(0.45, 2)
        if bounds == "raise" or factory == "coefficients":
            with pytest.raises(ValueError, match="range"):
                candidate.n(0.9, temperature=35)
        else:
            assert_allclose(candidate.n(0.9, temperature=35), 1.4 + 15 * 2e-6)


@pytest.mark.parametrize("factory", ["samples", "coefficients", "definition"])
@pytest.mark.parametrize("bounds", ["raise", "clamp"])
def test_internal_transmission_support_is_independent_of_nk_bounds(
    factory, bounds, set_test_backend
):
    material = _material(
        factory,
        bounds,
        {
            "kind": "internal_transmittance",
            "wavelengths_um": [0.5, 0.6, 0.7],
            "values": [0.7, 0.8, 0.9],
            "thickness_mm": 5,
            "interpolation": "local_quadratic",
        },
    )
    restored = BaseMaterial.from_dict(json.loads(json.dumps(material.to_dict())))
    for candidate in (material, restored):
        assert candidate.bounds == bounds
        assert candidate.spectral_range("k") == (0.5, 0.7)
        assert_allclose(candidate.n(0.45, temperature=35), 1.575 + 15 * 2e-6)
        assert_allclose(candidate.internal_transmittance(0.55, 10), 0.75**2)
        assert_allclose(
            candidate.k(0.55), -math.log(0.75) * 0.55 / (4 * math.pi * 5000)
        )
        for wavelength in (0.45, 0.75):
            with pytest.raises(ValueError, match="range"):
                candidate.k(wavelength)
            with pytest.raises(ValueError, match="range"):
                candidate.internal_transmittance(wavelength, 5)


def test_clamped_thermal_table_retains_query_gradients_after_cached_read(
    set_test_backend
):
    if be.get_backend() != "torch":
        pytest.skip("Torch gradient contract")
    import torch

    material = DataMaterial.from_samples(
        [0.4, 0.8], [1.6, 1.4], thermal=_thermal(), bounds="clamp"
    )
    material.n(0.9, temperature=35)
    for _ in range(2):
        wavelength = torch.tensor([0.9], dtype=torch.float64, requires_grad=True)
        temperature = torch.tensor([35.0], dtype=torch.float64, requires_grad=True)
        material.n(wavelength, temperature=temperature).sum().backward()
        torch.testing.assert_close(wavelength.grad, torch.zeros_like(wavelength))
        torch.testing.assert_close(temperature.grad, torch.full_like(temperature, 2e-6))
