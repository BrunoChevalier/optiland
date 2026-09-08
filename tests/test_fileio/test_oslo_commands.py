"""Independent OSLO command and physical-behavior regression tests."""

from __future__ import annotations

import pytest

import optiland.backend as be
from optiland.fileio import load_oslo_file
from optiland.fileio.oslo.reader.parser import OsloDataParser
from tests.utils import assert_allclose


def write_lens(tmp_path, text):
    path = tmp_path / "commands.len"
    path.write_text(text, encoding="utf-8")
    return path


def test_statements_quotes_comments_and_empty_image(tmp_path):
    path = write_lens(tmp_path, '\ufefflen new "a; b // c" 1 2\n'
                      'ebr 2; ang 1 // inline\n'
                      'th 1e20; nxt // surface 1\n'
                      'rd 30; th 5; nxt; end 2\n')
    data = OsloDataParser(path).parse()
    assert data.name == "a; b // c"
    assert data.aperture["EPD"] == 4
    assert list(data.surfaces) == [0, 1, 2]
    assert data.surfaces[1]["RD"] == 30


def test_gto_updates_selected_surface_and_parser_reuse(tmp_path):
    path = write_lens(tmp_path, 'LEN NEW "navigation" 1 2\nTH 1e10\n'
                      'NXT\nRD 10\nNXT\nAIR\nGTO 1\nTH 3\nEND 2\n')
    parser = OsloDataParser(path)
    for _ in range(2):
        data = parser.parse()
        assert data.surfaces[1]["TH"] == 3
        assert data.surfaces[1]["RD"] == 10
        assert len(data.surfaces) == 3


def test_unknown_optical_command_is_reported_and_strict_rejects(tmp_path):
    path = write_lens(tmp_path, 'LEN NEW "unsupported" 1 1\nTH 1e10\n'
                      'NXT\nMAGIC 8\nEND 1\n')
    with pytest.warns(UserWarning, match="MAGIC"):
        data = OsloDataParser(path).parse()
    diagnostic = data.diagnostics[0]
    assert diagnostic.command == "MAGIC"
    assert diagnostic.line == 4
    assert diagnostic.surface == 1
    with pytest.raises(ValueError, match="MAGIC"):
        OsloDataParser(path, strict=True).parse()


@pytest.mark.parametrize("command", ["RD", "TH nope", "AP nan", 'DES "unterminated'])
def test_malformed_command_has_source_context(tmp_path, command):
    path = write_lens(tmp_path, f'LEN NEW "bad" 1 0\n{command}\nEND 0\n')
    with pytest.raises(ValueError, match=r"commands.len:2"):
        OsloDataParser(path).parse()


def test_legacy_encoding_and_trailing_analysis_are_not_executed(tmp_path):
    path = tmp_path / "legacy.len"
    path.write_bytes('LEN NEW "Caf\xe9" 1 1\nTH 1e10\nNXT\nEND 1\n'
                     'DLNR 0 11\nRD 999\n'.encode("cp1252"))
    data = OsloDataParser(path).parse()
    assert data.name == "Caf\xe9"
    assert "RD" not in data.surfaces[1]


def test_public_loader_exposes_strict_mode(tmp_path):
    path = write_lens(tmp_path, 'LEN NEW "strict" 1 1\nNXT\nUNKNOWN 1\nEND 1\n')
    with pytest.raises(ValueError, match="UNKNOWN"):
        load_oslo_file(path, strict=True)


def simple_lens(tmp_path, system="", surface="", footer=""):
    return write_lens(tmp_path, 'LEN NEW "commands" 50 3\nEBR 2\nANG 0\n'
                      f'{system}\nTH 1e20\nNXT\nGLA 1.5 1.5 1.5\n'
                      f'RD 20\nTH 2\nAP 3\n{surface}\nNXT\n'
                      f'AIR\nRD -20\nTH 20\nNXT\nAIR\n{footer}\nEND 3\n')


def test_wavelength_slots_replace_preserve_order_weights_and_defaults(tmp_path):
    path = simple_lens(tmp_path, footer='WV .6 .5 .7\nWW 1 2 3\n'
                       'WV2 .45\nWV4 .8\nWW2 4\nWW4 5')
    data = OsloDataParser(path).parse()
    assert data.wavelengths["values"] == [.6, .45, .7, .8]
    assert data.wavelengths["weights"] == [1, 4, 3, 5]
    path = simple_lens(tmp_path, system='WV .6 .5 .7', footer='WV .55\nWW 2')
    assert OsloDataParser(path).parse().wavelengths["values"] == [.55]
    assert OsloDataParser(simple_lens(tmp_path)).parse().wavelengths["values"] == [
        .58756, .48613, .65627]


@pytest.mark.parametrize("command", ["WV 0", "WV -1", "WW -1", "UNI 0", "UNI -2"])
def test_invalid_system_values_rejected(tmp_path, command):
    with pytest.raises(ValueError):
        OsloDataParser(simple_lens(tmp_path, footer=command)).parse()


def test_units_scale_prescription_aperture_and_object_height(tmp_path, set_test_backend):
    path = simple_lens(tmp_path, system='UNI 25.4\nOBH 1')
    # Finite object: the field is a length in the same units as the lens.
    path.write_text(path.read_text().replace('TH 1e20', 'TH 10'))
    optic = load_oslo_file(path)
    assert_allclose(optic.surfaces[1].geometry.radius, 20 * 25.4)
    assert_allclose(optic.surfaces[1].thickness, 2 * 25.4)
    assert_allclose(optic.surfaces[1].aperture.r_max, 3 * 25.4)
    assert_allclose(optic.aperture.value, 4 * 25.4)
    assert_allclose(optic.fields.y_fields[-1], 25.4)
    assert_allclose(optic.wavelengths.primary_wavelength.value, .58756)


def test_infinite_object_field_uses_actual_oslo_distance(tmp_path, set_test_backend):
    path = simple_lens(tmp_path, system='OBH -1e20')
    optic = load_oslo_file(path)
    assert_allclose(optic.fields.y_fields[-1], 45)
    assert be.isinf(optic.surfaces[0].thickness)


def test_system_aperture_last_command_wins(tmp_path, set_test_backend):
    optic = load_oslo_file(simple_lens(tmp_path, system='FNO 8\nEBR 3'))
    assert optic.aperture.ap_type == "EPD"
    assert_allclose(optic.aperture.value, 6)


def test_image_na_and_telecentric(tmp_path, set_test_backend):
    optic = load_oslo_file(simple_lens(tmp_path, system='NAP .1\nTELE ON'))
    # Thick symmetric lens power = (n-1)(1/R1-1/R2+(n-1)t/(n R1 R2)).
    power = .5 * (.1 - .5 * 2 / (1.5 * 400))
    assert optic.aperture.ap_type == "EPD"
    assert_allclose(optic.aperture.value, .2 / power)
    assert optic.obj_space_telecentric is True
