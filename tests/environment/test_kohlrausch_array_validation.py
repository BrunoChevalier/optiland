"""Invalid wavelengths must not produce plausible air indices in vector queries."""

from __future__ import annotations

import pytest

import optiland.backend as be
from optiland.environment import EnvironmentalConditions
from optiland.environment.models.kohlrausch import kohlrausch_refractive_index


@pytest.mark.parametrize("invalid", [0.0, -0.55, float("nan"), float("inf")])
@pytest.mark.parametrize("vector", [False, True])
def test_reject_invalid_wavelengths_consistently(invalid, vector, set_test_backend):
    wavelength = be.asarray([0.55, invalid]) if vector else invalid
    with pytest.raises(ValueError, match="Wavelength"):
        kohlrausch_refractive_index(wavelength, EnvironmentalConditions())
