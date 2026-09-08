"""Independent OSLO command and physical-behavior regression tests."""

from __future__ import annotations

import pytest

from optiland.fileio import load_oslo_file
from optiland.fileio.oslo.reader.parser import OsloDataParser


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
