"""Independent published air indices, physical scaling and backend queries."""

from __future__ import annotations

import pytest

import optiland.backend as be
from optiland.environment import EnvironmentalConditions
from optiland.environment.models.kohlrausch import kohlrausch_refractive_index
from tests.utils import assert_allclose


@pytest.mark.parametrize('temperature,pressure,expected', [
    (20., 101325., 1.00027308), (30., 202650., 1.00052810),
])
def test_ansys_published_air_index_example(temperature, pressure, expected):
    # Ansys: How OpticStudio calculates refractive index at arbitrary
    # temperatures and pressures, example at 0.55 µm. Published to 8 decimals.
    actual = kohlrausch_refractive_index(.55, EnvironmentalConditions(temperature=temperature, pressure=pressure))
    assert actual == pytest.approx(expected, abs=5e-9, rel=0)


def test_independent_catalog_formula_at_multiple_wavelengths(set_test_backend):
    waves = be.asarray([.4, .55, .8, 1.5])
    conditions = EnvironmentalConditions(temperature=20., pressure=101325.)
    expected = 1 + 1e-8*(6432.8 + 2949810/(146-waves**-2) + 25540/(41-waves**-2))/(1+5*.0034785)
    assert_allclose(kohlrausch_refractive_index(waves, conditions), expected, rtol=1e-13)


def test_temperature_vectors_and_gradients_for_material_callers(set_test_backend):
    temperatures = be.asarray([10., 20., 30.])
    conditions = EnvironmentalConditions(temperature=temperatures, pressure=101325.)
    values = kohlrausch_refractive_index(.55, conditions)
    expected = 1 + .0002778260416499504 / (1 + (temperatures-15)*.0034785)
    assert_allclose(values, expected, rtol=1e-13)
    if be.get_backend() == 'torch':
        import torch
        for _ in range(2):
            temperature = torch.tensor([20.], dtype=torch.float64, requires_grad=True)
            conditions = EnvironmentalConditions(temperature=temperature, pressure=101325.)
            kohlrausch_refractive_index(.55, conditions).sum().backward()
            derivative = -.0002778260416499504*.0034785/(1+5*.0034785)**2
            assert temperature.grad.item() == pytest.approx(derivative, rel=1e-12)
