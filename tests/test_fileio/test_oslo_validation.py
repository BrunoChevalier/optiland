"""OSLO boundary cases preserve destinations and reject undefined ray launches."""

from __future__ import annotations

import math

import pytest

import optiland.backend as be
from optiland.aperture import EPDAperture
from optiland.fileio import load_oslo_file, save_oslo_file
from optiland.fileio.oslo.reader.converter import OsloToOpticConverter
from optiland.fileio.oslo.reader.parser import OsloDataParser
from optiland.interactions import RefractiveReflectiveModel
from optiland.rays import RealRays
from tests.test_fileio.test_oslo_edge_cases import lens_file
from tests.utils import assert_allclose


def test_export_without_system_aperture_omits_aperture_command(lens_file, tmp_path):
    optic = load_oslo_file(lens_file(), strict=True)
    optic.aperture = None
    output = tmp_path / "no-aperture.len"
    save_oslo_file(optic, output)
    assert OsloDataParser(output).parse().aperture == {}


def test_export_rejects_custom_system_aperture_before_overwriting(lens_file, tmp_path):
    class CustomAperture(EPDAperture):
        @property
        def ap_type(self):
            return "custom"

    optic = load_oslo_file(lens_file(), strict=True)
    optic.aperture = CustomAperture(4)
    output = tmp_path / "custom-aperture.len"
    output.write_text("saved design", encoding="utf-8")
    with pytest.raises(NotImplementedError, match="system aperture"):
        save_oslo_file(optic, output)
    assert output.read_text() == "saved design"


@pytest.mark.parametrize("diameter", [0, -1, math.inf, math.nan])
def test_export_rejects_invalid_entrance_beam_without_overwriting(
    lens_file, tmp_path, set_test_backend, diameter
):
    optic = load_oslo_file(lens_file(), strict=True)
    optic.set_aperture("EPD", diameter)
    output = tmp_path / "invalid-beam.len"
    output.write_text("saved design", encoding="utf-8")
    with pytest.raises(ValueError, match="finite positive entrance beam"):
        save_oslo_file(optic, output)
    assert output.read_text() == "saved design"


@pytest.mark.parametrize(
    "aperture_type,distance",
    [("EPD", "1e20"), ("float_by_stop_size", "1e20"), ("imageFNO", "100")],
)
def test_export_preserves_aperture_calculation_error_and_destination(
    lens_file, tmp_path, set_test_backend, aperture_type, distance
):
    optic = load_oslo_file(lens_file(distance=distance), strict=True)
    optic.set_aperture(aperture_type, 4)
    while optic.wavelengths:
        optic.wavelengths.remove(0)
    output = tmp_path / "missing-spectrum.len"
    output.write_text("saved design", encoding="utf-8")
    with pytest.raises(ValueError, match="No primary wavelength"):
        save_oslo_file(optic, output)
    assert output.read_text() == "saved design"


def test_model_conversion_rejects_empty_modeled_glass(lens_file):
    # OsloToOpticConverter also accepts models constructed outside the parser.
    model = OsloDataParser(lens_file()).parse()
    model.surfaces[1]["material"] = "GLA MOD"
    with pytest.raises(ValueError, match="GLA MOD requires refractive-index data"):
        OsloToOpticConverter(model, strict=True).convert()


def test_finite_object_on_first_surface_cannot_define_nonzero_beam(
    lens_file, set_test_backend
):
    # An axial point at surface 1 has zero height there, even with a later stop.
    # No finite cone from this point can have the requested EBR=2 at surface 1.
    with pytest.raises(ValueError, match="finite nonzero beam at surface 1"):
        load_oslo_file(
            lens_file(distance="0", surface="AIR\nRD 0", second="AST"), strict=True
        )


def test_export_cannot_discard_custom_ray_interactions(
    lens_file, tmp_path, set_test_backend
):
    class AttenuatingInteraction(RefractiveReflectiveModel):
        def interact_real_rays(self, rays):
            rays = super().interact_real_rays(rays)
            rays.i *= 0.25
            return rays

    optic = load_oslo_file(lens_file(), strict=True)
    surface = optic.surfaces[1]
    surface.interaction_model = AttenuatingInteraction(surface, is_reflective=False)
    zeros = be.zeros(1)
    rays = RealRays(zeros, zeros, zeros, zeros, zeros, be.ones(1), 1, 0.55)
    surface.interaction_model.interact_real_rays(rays)
    # This model inherits the standard interaction_type string, but changes
    # transmitted power. Exporting only its GLA record would lose that physics.
    assert_allclose(rays.i, [0.25])
    output = tmp_path / "custom-interaction.len"
    output.write_text("saved design", encoding="utf-8")
    with pytest.raises(NotImplementedError, match="interaction model"):
        save_oslo_file(optic, output)
    assert output.read_text() == "saved design"
