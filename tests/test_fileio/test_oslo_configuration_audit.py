"""All-configuration audit outcomes using only original synthetic lenses/GLCs."""

from __future__ import annotations

import json
import runpy
import sys

import numpy as np
import pytest

import optiland.backend as be
from optiland.optic import Optic
from optiland.fileio import load_oslo_file
from optiland.fileio.oslo import catalog
from optiland.materials.material_file import MaterialFile
from scripts import audit_oslo_configurations
from scripts.audit_oslo_configurations import audit
from tests.test_fileio.test_oslo_glc_fixtures import FIXTURES


@pytest.fixture
def audit_lens(tmp_path):
    path = tmp_path / "private-name.len"
    path.write_text(
        'LEN NEW "private title" 1 3; EBR 1; WV .5; TH 1e20; '
        "NXT; GLA FAKE_LA; RD 20; TH 2; NXT; AIR; RD -20; TH 10; "
        "NXT; AIR; END 3; CFG NEW; TH 1 3 4; WV1 3 .7; END; CFAC 3 NO",
        encoding="utf-8",
    )
    return path


def test_all_configurations_include_inactive_and_fresh_process(
    audit_lens, set_test_backend
):
    report = audit(
        audit_lens,
        [FIXTURES / "valid" / "version-7-02.glc"],
        backends=[be.get_backend()],
    )
    assert [row["configuration"] for row in report["configurations"]] == [1, 2, 3]
    assert report["configurations"][2]["active"] is False
    assert report["passed"] is True
    assert report["summary"] == {
        "total": 3,
        "imported": 3,
        "traced": 3,
        "native_matches": 3,
        "fresh_process_matches": 3,
    }
    for row in report["configurations"]:
        assert row["import_warnings"] == 0
        assert row["trace"]["transmitted"] == 5
        assert row["trace"]["nonfinite_transmitted"] == 0
        assert row["trace"]["samples"] == 1
    encoded = json.dumps(report)
    assert str(audit_lens.parent) not in encoded
    assert "private-name" not in encoded and "private title" not in encoded
    assert "FAKE_LA" not in encoded


def test_missing_catalog_is_import_failure_not_trace_failure(audit_lens):
    report = audit(audit_lens, [], backends=["numpy"], fresh_process=False)
    assert report["summary"]["imported"] == 0
    assert all(row["phase"] == "import" for row in report["configurations"])
    assert all(row["error_type"] == "ValueError" for row in report["configurations"])
    assert "FAKE_LA" not in json.dumps(report)


def test_polarization_is_an_explicit_assumption(audit_lens):
    text = audit_lens.read_text(encoding="utf-8")
    audit_lens.write_text(
        text.replace("RD 20;", "RD 20; JAA .5; JAD .5;"), encoding="utf-8"
    )
    catalogs = [FIXTURES / "valid" / "version-7-02.glc"]
    ignored = audit(audit_lens, catalogs, backends=["numpy"], fresh_process=False)
    explicit = audit(
        audit_lens,
        catalogs,
        backends=["numpy"],
        polarization="unpolarized",
        fresh_process=False,
    )
    assert ignored["summary"]["imported"] == 3
    assert ignored["summary"]["traced"] == 0
    assert explicit["summary"]["traced"] == 3
    assert explicit["summary"]["native_matches"] == 3
    assert explicit["polarization_assumption"] == "unpolarized"
    assert explicit["summary"]["fresh_process_matches"] == 0
    assert all(
        row["fresh_process_matches"] is None for row in explicit["configurations"]
    )


def test_trace_failure_is_distinct_and_error_text_is_private(audit_lens, monkeypatch):
    def fail_trace(self, *args, **kwargs):
        raise RuntimeError("private prescription text")

    monkeypatch.setattr(Optic, "trace", fail_trace)
    report = audit(
        audit_lens,
        [FIXTURES / "valid" / "version-7-02.glc"],
        backends=["numpy"],
        fresh_process=False,
    )
    assert report["summary"]["imported"] == 3
    assert report["summary"]["traced"] == 0
    assert all(row["phase"] == "trace" for row in report["configurations"])
    assert "private prescription text" not in json.dumps(report)


def test_nonfinite_rays_fail_even_when_native_reload_matches(
    audit_lens, tmp_path, monkeypatch
):
    original_trace = Optic.trace

    def invalid_trace(self, *args, **kwargs):
        rays = original_trace(self, *args, **kwargs)
        rays.x[0] = np.nan  # Nonfinite geometry with positive intensity.
        rays.i[1] = np.nan  # Intensity alone is also an audit failure.
        rays.i[2] = 0
        rays.x[2] = np.nan  # Extinguished ray coordinates are not surviving rays.
        return rays

    monkeypatch.setattr(Optic, "trace", invalid_trace)
    output = tmp_path / "nonfinite.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit",
            str(audit_lens),
            str(output),
            "--backend",
            "numpy",
            "--catalog",
            str(FIXTURES / "valid" / "version-7-02.glc"),
            "--no-fresh-process",
        ],
    )
    assert audit_oslo_configurations.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is False
    for row in report["configurations"]:
        assert row["traced"] and row["native_matches"]
        assert row["trace"] == {
            "samples": 1,
            "rays": 5,
            "transmitted": 2,
            "zero_intensity": 1,
            "nonfinite_transmitted": 1,
            "nonfinite_intensity": 1,
        }


def test_invalid_source_and_unavailable_backend_are_visible(audit_lens):
    report = audit(audit_lens, [], backends=["unavailable"])
    assert report["backend_errors"] == {"unavailable": "ValueError"}
    audit_lens.write_text('LEN NEW "bad" 1 1; RD bad', encoding="utf-8")
    report = audit(audit_lens, [], backends=["numpy"])
    assert report["parse_error"] == "ValueError"
    assert report["configurations"] == []


def test_cli_writes_report_and_fails_for_unresolved_material(
    audit_lens, tmp_path, monkeypatch, capsys
):
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_oslo_configurations",
            str(audit_lens),
            str(output),
            "--backend",
            "numpy",
            "--no-fresh-process",
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(audit_oslo_configurations.__file__, run_name="__main__")
    assert exit_info.value.code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == report["summary"]


def test_reopen_command_enforces_material_independence(
    audit_lens, tmp_path, monkeypatch, capsys, set_test_backend
):
    optic = load_oslo_file(
        audit_lens,
        strict=True,
        glass_catalogs=[FIXTURES / "valid" / "version-7-02.glc"],
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(optic.to_dict()), encoding="utf-8")
    # Register the original functions for restoration after the child-mode guard
    # deliberately replaces them in this instrumented process.
    monkeypatch.setattr(catalog, "read_glc_catalog", catalog.read_glc_catalog)
    monkeypatch.setattr(MaterialFile, "_read_file", MaterialFile._read_file)
    monkeypatch.setattr(
        sys, "argv", ["audit", "--reopen", str(snapshot), "--backend", be.get_backend()]
    )
    assert audit_oslo_configurations.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["transmitted"] == 5
    with pytest.raises(RuntimeError, match="external material access"):
        catalog.read_glc_catalog(FIXTURES / "valid" / "version-7-02.glc")
    with pytest.raises(RuntimeError, match="external material access"):
        MaterialFile("must-not-open.yml")


def test_audit_rejects_unknown_polarization(audit_lens):
    with pytest.raises(ValueError, match="Unknown source polarization"):
        audit(audit_lens, [], polarization="unknown")


def test_audit_cli_requires_input_and_output(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["audit"])
    with pytest.raises(SystemExit) as error:
        audit_oslo_configurations.main()
    assert error.value.code == 2
    assert "lens and output are required" in capsys.readouterr().err


def test_audit_without_git_retains_optical_outcomes(audit_lens, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git unavailable")

    monkeypatch.setattr(audit_oslo_configurations.subprocess, "run", unavailable)
    result = audit(
        audit_lens,
        [FIXTURES / "valid" / "version-7-02.glc"],
        backends=["numpy"],
        fresh_process=False,
    )
    assert result["source_revision"] is None
    assert result["source_dirty"] is None
    assert result["passed"] is True
