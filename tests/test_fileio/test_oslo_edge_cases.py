"""OSLO boundary validation, fallback behavior and less common round trips."""

from __future__ import annotations

import math
from copy import deepcopy
from types import SimpleNamespace

import pytest

import optiland.backend as be
from optiland.fileio import load_oslo_file, save_oslo_file
from optiland.fileio.oslo.model import OsloDataModel
from optiland.fileio.oslo.reader.converter import OsloToOpticConverter
from optiland.fileio.oslo.reader.coordinates import surface_coordinates
from optiland.fileio.oslo.reader.parser import OsloDataParser
from optiland.fileio.oslo.reader.pickups import resolve_pickups
from optiland.fileio.oslo.writer.formatter import OsloDataFormatter
from optiland.materials import AbbeMaterial, TabulatedMaterial
from optiland.physical_apertures import (
    BaseAperture,
    RectangularAperture,
    RotatedAperture,
    UnclippedAperture,
)
from optiland.rays import RealRays
from tests.utils import assert_allclose


@pytest.fixture
def lens_file(tmp_path):
    """Write an original, small prescription with independently known power."""

    def write(
        *,
        system="",
        surface="",
        second="",
        image="",
        footer="",
        aperture="EBR 2",
        distance="1e20",
    ):
        path = tmp_path / "edge.len"
        path.write_text(
            'LEN NEW "edge cases" 50 3\n'
            f"{aperture}\nANG 0\n{system}\nTH {distance}\nNXT\n"
            f"GLA 1.5\nRD 20\nTH 2\nAP 3\n{surface}\nNXT\n"
            f"AIR\nRD -20\nTH 20\n{second}\nNXT\nAIR\n{image}\n"
            f"END 3\n{footer}",
            encoding="utf-8",
        )
        return path

    return write


@pytest.mark.parametrize(
    "command,message",
    [
        ("WW 0 0 0", "weights cannot all be zero"),
        ("TELE MAYBE", "TELE expects"),
        ("APCK MAYBE", "APCK expects"),
        ("AP -1", "radius must be nonnegative"),
        ("APN -1", "APN count"),
        ("APN 257", "APN count"),
        ("AX1 A", "aperture identifier and value"),
        ("AVX1 A 1 2", "aperture identifier and value"),
        ("ATP A 1.5", "ATP requires an integer"),
        ("AS1", "one coefficient"),
        ("WV1002 .55", "bounded wavelength index"),
        ("WV2 .55 .6", "one value"),
        ("WV5 .55", "undefined wavelength slots"),
        ("GTO -1", "outside the declared lens"),
        ("GTO 4", "outside the declared lens"),
        ("END 2", "count differs from LEN"),
        ("PK AP 0 1", "invalid number of arguments"),
        ("APK A 0", "APK expects"),
    ],
)
def test_parser_rejects_invalid_boundaries_with_source(lens_file, command, message):
    with pytest.raises(ValueError, match=message) as error:
        OsloDataParser(lens_file(surface=command), strict=True).parse()
    assert "edge.len:" in str(error.value)


@pytest.mark.parametrize("count", [0, 10001])
def test_surface_count_limit(tmp_path, count):
    path = tmp_path / "count.len"
    path.write_text(f'LEN NEW "invalid count" 1 {count}\n')
    with pytest.raises(ValueError, match="LEN surface count"):
        OsloDataParser(path).parse()


def test_nxt_cannot_append_beyond_declared_image(lens_file):
    with pytest.raises(ValueError, match="NXT exceeds LEN"):
        OsloDataParser(lens_file(image="NXT")).parse()


@pytest.mark.parametrize(
    "record,message",
    [
        ("F 1 0 0", "ten field-table values"),
        ("F 0 0 0 0 0 0 -1 1 -1 1 1", "positive index"),
        ("F 1 0 0 0 0 0 -1 1 -1 1 -1", "nonnegative weight"),
        ("F 1 0 0 0 0 0 1 -1 -1 1 1", "bounds must be increasing"),
        ("F 1 0 0 0 0 0 -1 1 0 0 1", "bounds must be increasing"),
    ],
)
def test_invalid_field_table(lens_file, record, message):
    with pytest.raises(ValueError, match=message):
        OsloDataParser(lens_file(footer=f"RST NEW\n{record}\nEND\n")).parse()


def test_asymmetric_field_pupil_warns_and_retains_field(lens_file, set_test_backend):
    path = lens_file(
        system="OBH 4",
        distance="100",
        footer=("RST NEW\nF 1 -.5 .25 0 0 0 -.5 1 -1 1 2\nEND\n"),
    )
    with pytest.warns(UserWarning, match="asymmetric field pupil"):
        optic = load_oslo_file(path)
    assert_allclose([optic.fields[0].x, optic.fields[0].y], [1, -2])
    assert_allclose([optic.fields[0].vx, optic.fields[0].vy], [0, 0])
    assert optic.fields[0].weight == 2
    with pytest.raises(ValueError, match="asymmetric field pupil"):
        load_oslo_file(path, strict=True)


@pytest.mark.parametrize(
    "command,message",
    [
        ("LMO NSS", "non-sequential groups"),
        ("ASP ZER 4", "asphere type ZER"),
        ("PK UNSUPPORTED 0", "pickup type UNSUPPORTED"),
        ("AAC A 1", "undeviated hole"),
    ],
)
def test_unsupported_prescriptions_warn_or_reject(lens_file, command, message):
    path = lens_file(surface=command)
    with pytest.warns(UserWarning, match=message):
        model = OsloDataParser(path).parse()
    assert model.diagnostics[0].command == command.split()[0]
    with pytest.raises(ValueError, match=message):
        load_oslo_file(path, strict=True)
    # A warned unsupported pickup must not be executed by the converter.
    with pytest.warns(UserWarning, match=message):
        optic = load_oslo_file(path)
    assert_allclose(optic.surfaces[1].geometry.radius, 20)


@pytest.mark.parametrize(
    "command,message",
    [
        ("GLA 0", "positive refractive indices"),
        ("GLA 1.5 1.6", "index/wavelength counts differ"),
        ("GSP -1", "positive spacing"),
        ("GOR 1", "positive spacing"),
        ("ATP A 5", "aperture type 5"),
        ("ATP A 1\nAX1 A 2\nAX2 A 1", "increasing bounds"),
        ("ASP ASR 1\nAS257 .001", "limit 256"),
        ("ASP ASR 1\nAS0 .001", "offset sag"),
        ("ASP ARA 1\nAS0 .001", "offset sag"),
        ("ASP ASX 1\nCVX .01", "even YZ asphere"),
        ("GC 2", "preceding surface"),
        ("RCO 2", "unavailable surface"),
        ("TLA 10\nTLB 20\nBEN", "single-axis local mirror"),
        ("DCY 1\nTH 1e20", "infinite thickness"),
    ],
)
def test_converter_rejects_invalid_optical_data(lens_file, command, message):
    with pytest.raises(ValueError, match=message):
        load_oslo_file(lens_file(surface=command), strict=True)


def test_coordinate_model_rejects_invalid_transform_order():
    # Models can be supplied independently of the text parser.
    with pytest.raises(ValueError, match="OSLO DT must be"):
        surface_coordinates({0: {"TH": 100}, 1: {"DT": 0}}, 1)


@pytest.mark.parametrize("radius", [0, 40])
def test_rdx_maps_toric_x_radius(lens_file, set_test_backend, radius):
    optic = load_oslo_file(lens_file(surface=f"RDX {radius}"), strict=True)
    sag = optic.surfaces[1].geometry.sag(be.array([2.0]), be.array([0.0]))
    expected = 0 if radius == 0 else 40 - math.sqrt(40**2 - 2**2)
    assert_allclose(sag, [expected])


def test_default_aperture_uses_lens_units(lens_file, set_test_backend):
    optic = load_oslo_file(lens_file(aperture="", system="UNI 10"), strict=True)
    assert optic.aperture.ap_type == "EPD"
    assert_allclose(optic.aperture.value, 20)


def test_image_na_rejects_afocal_system(lens_file):
    with pytest.raises(ValueError, match="afocal system"):
        load_oslo_file(
            lens_file(system="NAP .1", surface="RD 0", second="RD 0"), strict=True
        )


@pytest.mark.parametrize("scale", [1, 10])
def test_finite_gaussian_image_height_has_expected_magnification(
    lens_file, set_test_backend, scale
):
    optic = load_oslo_file(
        lens_file(system=f"UNI {scale}\nGIH 2", distance="100"), strict=True
    )
    # Unit-height marginal ray: u0=.01, u1=-.01, y2=.98, u2=-.0395.
    # The Gaussian magnification is u0/u2=-20/79, hence |OBH|=7.9.
    assert optic.fields.field_definition.__class__.__name__ == "ObjectHeightField"
    assert_allclose(optic.fields[-1].y, 7.9 * scale)


def test_model_glass_approximation_is_explicit(lens_file, set_test_backend):
    path = lens_file(surface="GLA MOD 1.6 50")
    with pytest.warns(UserWarning, match="Buchdahl model"):
        optic = load_oslo_file(path)
    material = optic.surfaces[1].material_post
    assert isinstance(material, AbbeMaterial)
    assert_allclose(material.n(0.5875618), 1.6, atol=1e-6)
    with pytest.raises(ValueError, match="dispersion is approximate"):
        load_oslo_file(path, strict=True)


def test_prefixed_historical_glass_warns(lens_file, monkeypatch):
    import optiland.fileio.oslo.reader.converter as converter

    def missing_glass(name):
        raise ValueError(name)

    monkeypatch.setattr(converter, "Material", missing_glass)
    path = lens_file(surface="GLA H_BAF13")
    with pytest.warns(UserWarning, match="historical Abbe dispersion"):
        optic = load_oslo_file(path)
    assert isinstance(optic.surfaces[1].material_post, AbbeMaterial)
    assert_allclose(optic.surfaces[1].material_post.n(0.5875618), 1.667, atol=1e-6)
    with pytest.raises(ValueError, match="historical Abbe dispersion"):
        load_oslo_file(path, strict=True)


def test_perfect_lens_warns_and_scales_focal_length(lens_file, set_test_backend):
    path = lens_file(system="UNI 10", surface="PFL 15")
    with pytest.warns(UserWarning, match="paraxial thin lens"):
        optic = load_oslo_file(path)
    assert optic.surfaces[1].interaction_model.interaction_type == "thin_lens"
    assert_allclose(optic.surfaces[1].interaction_model.f, 150)
    with pytest.raises(ValueError, match="perfect imagery"):
        load_oslo_file(path, strict=True)


@pytest.mark.parametrize(
    "pickup,message",
    [
        ("PK LN 1 1", "invalid length range"),
        ("APK A 2 A", "preceding source"),
        ("APK A 1 MISSING", "source aperture MISSING is undefined"),
    ],
)
def test_invalid_pickup_references(lens_file, pickup, message):
    with pytest.raises(ValueError, match=message):
        load_oslo_file(lens_file(second=pickup), strict=True)


@pytest.mark.parametrize(
    "source,pickup,message",
    [
        ("RFL", "PK GLA 1", "cannot pick up a reflector"),
        ("RCO", "PK TD 1", "global/return/bend"),
        ("TLA 10\nBEN", "PK TDM 1", "global/return/bend"),
    ],
)
def test_unsupported_pickup_sources(lens_file, source, pickup, message):
    with pytest.raises(ValueError, match=message):
        load_oslo_file(lens_file(surface=source, second=pickup), strict=True)


def test_curvature_pickup_replaces_profile_without_mutating_source():
    surfaces = {
        0: {},
        1: {"RD": 10, "CC": -1, "AD": 0.001},
        2: {
            "ASP": "ASR",
            "AS1": 1,
            "CVX": 2,
            "AE": 3,
            "pickups": [["CVM", "1", ".01"]],
        },
    }
    original = deepcopy(surfaces)
    resolved = resolve_pickups(surfaces)
    assert surfaces == original
    assert resolved[2]["RD"] == pytest.approx(-1 / 0.09)
    assert resolved[2]["CC"] == -1
    assert resolved[2]["AD"] == -0.001
    assert not {"ASP", "AS1", "CVX", "AE"}.intersection(resolved[2])


def test_model_pickup_rejects_unknown_type():
    with pytest.raises(ValueError, match="PK UNSUPPORTED is unsupported"):
        resolve_pickups({0: {}, 1: {"pickups": [["UNSUPPORTED", "0"]]}})


@pytest.mark.parametrize(
    "surface,image,message",
    [
        ("", "PY 1", "interior optical surface"),
        ("EC -1", "", "nonnegative edge height"),
        ("EC 21", "", "outside the surface's sag domain"),
        ("AIR\nPY 1", "", "could not be reached"),
    ],
)
def test_failed_solve_restores_saved_prescription(
    lens_file, set_test_backend, surface, image, message
):
    path = lens_file(surface=surface, image=image)
    model = OsloDataParser(path).parse()
    original = model.to_dict()
    with pytest.warns(UserWarning, match=message + ".*retained saved prescription"):
        optic = OsloToOpticConverter(model).convert()
    assert model.to_dict() == original
    assert_allclose(optic.surfaces[1].thickness, 2)
    assert_allclose(optic.surfaces[2].geometry.cs.z, 2)
    assert_allclose(optic.surfaces[2].geometry.radius, -20)
    with pytest.raises(ValueError, match=message):
        OsloToOpticConverter(model, strict=True).convert()


def test_failed_chief_slope_refinement_restores_curvature(
    lens_file, set_test_backend, monkeypatch
):
    import optiland.fileio.oslo.reader.solves as solves

    calls = []

    def fail_refinement(residual, **kwargs):
        calls.append(residual(0.2))  # Mimic a solver changing the optic before failure.
        return SimpleNamespace(converged=False)

    monkeypatch.setattr(solves, "root_scalar", fail_refinement)
    path = lens_file(system="ANG 5", second="PUC .03")
    with pytest.warns(UserWarning, match="refinement did not converge.*retained"):
        optic = load_oslo_file(path)
    assert len(calls) == 1
    assert_allclose(optic.surfaces[2].geometry.radius, -20)


@pytest.mark.parametrize("command,power", [("AS1 .001", 2), ("AS6 .001", 12)])
def test_general_even_asphere_export_preserves_sag(
    lens_file, tmp_path, set_test_backend, command, power
):
    optic = load_oslo_file(
        lens_file(surface=f"RD 0\nASP ASR 1\n{command}"), strict=True
    )
    path = tmp_path / "general-asphere.len"
    save_oslo_file(optic, path)
    assert "ASP ASR" in path.read_text()
    restored = load_oslo_file(path, strict=True)
    assert_allclose(
        restored.surfaces[1].geometry.sag(be.array([2.0]), be.array([0.0])),
        [0.001 * 2**power],
    )


@pytest.mark.parametrize(
    "field_type,y,message",
    [
        ("paraxial_image_height", 1, "field definition"),
        ("real_image_height", 1, "field definition"),
        ("angle", 90, "wide-angle field"),
        ("angle", -91, "wide-angle field"),
    ],
)
def test_unsupported_export_fields_preserve_destination(
    lens_file, tmp_path, field_type, y, message
):
    optic = load_oslo_file(lens_file(), strict=True)
    optic.fields.set_type(field_type)
    optic.fields.fields.clear()
    optic.fields.add(y=y, x=0)
    target = tmp_path / "existing.len"
    target.write_text("saved design", encoding="utf-8")
    with pytest.raises(NotImplementedError, match=message):
        save_oslo_file(optic, target)
    assert target.read_text() == "saved design"


def test_formatter_preserves_escaped_notes(lens_file):
    model = OsloDataParser(lens_file()).parse()
    note = 'A "quoted" note; // with a \\ path'
    model.notes["SNO1"] = note
    path = lens_file()
    path.write_text(OsloDataFormatter(model).format(), encoding="utf-8")
    assert OsloDataParser(path, strict=True).parse().notes["SNO1"] == note


@pytest.mark.parametrize("value,expected", [(math.inf, 1e10), (-math.inf, -1e10)])
def test_formatter_signed_infinity_sentinels(value, expected):
    assert float(OsloDataFormatter(OsloDataModel())._fmt(value)) == expected


@pytest.mark.parametrize(
    "waves,indices,message",
    [
        ([], [], "at least two paired samples"),
        ([0.5], [1.5], "at least two paired samples"),
        ([0.5, 0.6], [1.5], "paired samples"),
        ([0, 0.6], [1.5, 1.6], "finite and positive"),
        ([0.5, 0.6], [-1, 1.6], "finite and positive"),
        ([0.5, math.inf], [1.5, 1.6], "finite and positive"),
        ([0.5, 0.6], [1.5, math.nan], "finite and positive"),
    ],
)
def test_invalid_tabulated_material_samples(waves, indices, message):
    with pytest.raises(ValueError, match=message):
        TabulatedMaterial(waves, indices)


def test_tabulated_material_is_nonabsorbing(set_test_backend):
    material = TabulatedMaterial([0.4, 0.8], [1.6, 1.5])
    assert_allclose(material.k(0.5), 0)
    assert_allclose(material.k(be.array([0.4, 0.6, 0.8])), [0, 0, 0])


def test_rotated_drawing_aperture_roundtrip_scale_and_clipping(set_test_backend):
    rotated = RotatedAperture(RectangularAperture(1, 3, -1, 1), math.pi / 2)
    drawing = UnclippedAperture(rotated)
    restored = BaseAperture.from_dict(drawing.to_dict())
    restored.scale(2)
    assert_allclose(restored.extent, [-2, 2, 2, 6])
    assert_allclose(drawing.extent, [-1, 1, 1, 3])
    x, y = be.array([0.0, 3.0]), be.array([4.0, 0.0])
    assert_allclose(restored.contains(x, y), [True, False])
    rays = RealRays(x, y, be.zeros(2), be.zeros(2), be.zeros(2), be.ones(2), 1, 0.55)
    restored.clip(rays)
    assert_allclose(rays.i, [1, 1])
    restored.aperture.clip(rays)
    assert_allclose(rays.i, [1, 0])


@pytest.mark.parametrize("name", ['lens "A"', '"', '"quoted"', 'path\\"'])
def test_quoted_name_and_note_preserve_trailing_quote(lens_file, name):
    model = OsloDataParser(lens_file()).parse()
    model.name = name
    model.notes["SNO1"] = name
    path = lens_file()
    path.write_text(OsloDataFormatter(model).format(), encoding="utf-8")
    restored = OsloDataParser(path, strict=True).parse()
    assert restored.name == name
    assert restored.notes["SNO1"] == name


def test_quoted_direct_glass_name(lens_file, set_test_backend):
    optic = load_oslo_file(
        lens_file(surface='GLA "test glass" 1.6 1.62 1.58'), strict=True
    )
    material = optic.surfaces[1].material_post
    assert material.name == "test glass"
    assert_allclose(material.n(0.48613), 1.62)


@pytest.mark.parametrize(
    "command",
    [
        "AP 1 ignored",
        "AP CHK 1 ignored",
        "BEN OFF",
        "RCO 0 1",
        "AIR ignored",
        "AST ignored",
        "ATD ignored",
        "ASP ASR 1 ignored",
        "ASP ASR -1",
        "GLA MOD G1",
    ],
)
def test_optical_commands_do_not_silently_discard_arguments(lens_file, command):
    with pytest.raises(ValueError):
        load_oslo_file(lens_file(surface=command), strict=True)


@pytest.mark.parametrize("next_block", ["CFG NEW", 'LEN NEW "second" 1 1'])
def test_additional_configuration_cannot_replace_first_field_table(
    lens_file, next_block
):
    first = "RST NEW\nF 1 .5 0 0 0 0 -1 1 -1 1 1\nEND\n"
    second = "RST NEW\nF 1 1 0 0 0 0 -1 1 -1 1 1\nEND\n"
    path = lens_file(
        system="OBH 4", distance="100", footer=f"{first}{next_block}\nEND\n{second}"
    )
    with pytest.warns(UserWarning, match="additional configurations"):
        optic = load_oslo_file(path)
    assert_allclose(optic.fields.y_fields, [2])


def test_failed_pickup_rebuild_restores_converter_data(lens_file, set_test_backend):
    path = lens_file(system="NAP .1", surface="PU 0", second="PK CV 1")
    converter = OsloToOpticConverter(OsloDataParser(path).parse())
    with pytest.warns(UserWarning, match="afocal.*retained saved prescription"):
        optic = converter.convert()
    # The saved pickup produces equal radii; solving both to infinity makes
    # NAP undefined. Rollback must restore the model as well as the optic.
    for index in (1, 2):
        assert_allclose(optic.surfaces[index].geometry.radius, 20)
        assert converter.data.surfaces[index]["RD"] == 20


def test_coupled_solve_cannot_silently_change_image_na(lens_file, set_test_backend):
    path = lens_file(system="NAP .1", surface="PU -.05")
    with pytest.warns(UserWarning, match="retained saved prescription"):
        optic = load_oslo_file(path)
    assert_allclose(abs(optic.paraxial.marginal_ray()[1][-2]), 0.1)
    assert_allclose(optic.surfaces[1].geometry.radius, 20)
    with pytest.raises(ValueError, match="target"):
        load_oslo_file(path, strict=True)


def test_tilt_and_bend_requires_a_reflector(lens_file):
    with pytest.raises(ValueError, match="BEN.*reflect"):
        load_oslo_file(lens_file(surface="TLA 20\nBEN"), strict=True)


@pytest.mark.parametrize("coordinate", ["GC 1", "RCO", "BEN"])
def test_tilt_pickup_rejects_target_reference_flags(lens_file, coordinate):
    with pytest.raises(ValueError, match="global/return/bend"):
        load_oslo_file(lens_file(second=f"{coordinate}\nPK TD 1"), strict=True)
