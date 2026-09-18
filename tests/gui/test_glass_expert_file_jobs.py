"""Portable file workers retain exact frozen optics and never regain authority."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import replace

import pytest

from optiland.optimization.glass_expert_replay import (
    decode_glass_expert_manifest,
    replay_glass_expert,
)
from optiland_gui.services import glass_expert_file_jobs as files
from optiland_gui.services import glass_expert_jobs as jobs
from optiland_gui.services.job_records import CalculationCancelled
from tests.gui.test_glass_expert_jobs import make_setup, prepare


def export(prepared, kind="manifest", result=None):
    return files.prepare_export(
        prepared.setup.baseline,
        {
            "operation_id": "export:1",
            "kind": kind,
            "prepared": prepared,
            "result": result,
        },
        lambda *a: None,
        threading.Event(),
    )


def load(path, **parameters):
    return files.load_manifest(
        None,
        {
            "path": path,
            "run_id": "loaded:fresh",
            "operation_id": "load:1",
            **parameters,
        },
        lambda *a: None,
        threading.Event(),
    )


def test_worker_manifest_roundtrip_rebuilds_fresh_owned_setup(minimal_optic, tmp_path):
    original = prepare(make_setup(minimal_optic))
    encoded = export(original)
    path = tmp_path / "run.json"
    path.write_bytes(encoded.data)
    loaded = load(path)
    assert loaded.kind == "load" and loaded.operation_id == "load:1"
    assert loaded.prepared.setup.run_id == "loaded:fresh"
    assert loaded.prepared.setup.targets == original.setup.targets
    assert loaded.prepared.resolved_variables == original.resolved_variables
    assert loaded.prepared.resolved_operands == original.resolved_operands
    assert loaded.prepared.baseline_merit == pytest.approx(original.baseline_merit)
    assert any("missing" in message for message in loaded.warnings)
    assert not any(
        "Captured source" in w
        for w in load(path, source_stamps=original.setup.source_stamps).warnings
    )
    stamp = replace(original.setup.source_stamps[0], generation="changed")
    assert any(
        "different generation" in w for w in load(path, source_stamps=(stamp,)).warnings
    )


def test_public_replay_and_script_need_no_gui_or_catalog(minimal_optic, tmp_path):
    prepared = prepare(make_setup(minimal_optic))
    result = jobs.run(
        prepared.setup.baseline,
        {"setup": prepared},
        lambda *a, **k: None,
        threading.Event(),
    )
    encoded = export(prepared, result=result)
    replay = replay_glass_expert(decode_glass_expert_manifest(encoded.data))
    assert replay.report.baseline.merit == pytest.approx(result.report.baseline.merit)
    assert replay.report.candidate.merit == pytest.approx(result.report.candidate.merit)
    assert replay.report.candidate.materials == result.report.candidate.materials
    script = export(prepared, "script", result)
    path = tmp_path / "replay.py"
    path.write_bytes(script.data)
    harness = (
        "import sys, importlib.abc, runpy\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self, fullname, path=None, target=None):\n"
        "  if fullname.startswith(('optiland_gui', 'PySide', 'torch')):\n"
        "   raise ImportError(fullname)\n"
        "sys.meta_path.insert(0, Block())\n"
        "from optiland.materials.material_file import MaterialFile\n"
        "def no_files(*a,**k): raise RuntimeError('Catalog access forbidden')\n"
        "MaterialFile.from_dict = no_files\n"
        "runpy.run_path(sys.argv[1],run_name='__main__')\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", harness, str(path)],
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert run.returncode == 0, run.stderr
    output = json.loads(run.stdout.strip().splitlines()[-1])
    assert output["candidate"]["merit"] == pytest.approx(result.report.candidate.merit)
    assert (
        output["candidate"]["materials"][0]["key"]
        == json.loads(encoded.data)["payload"]["result"]["report"]["candidate"][
            "materials"
        ][0]["key"]
    )


def test_unavailable_outputs_and_exact_result_ownership(minimal_optic):
    prepared = prepare(make_setup(minimal_optic))
    unavailable = replace(
        prepared, portable_baseline=None, replay_unavailable="Custom behavior"
    )
    assert export(unavailable, "script").unavailable_reason == "Custom behavior"
    assert export(unavailable).data
    assert export(prepared, "candidate").data is None
    result = jobs.run(
        prepared.setup.baseline,
        {"setup": prepared},
        lambda *a, **k: None,
        threading.Event(),
    )
    assert export(prepared, "candidate", result).data == result.native_candidate
    with pytest.raises(ValueError, match="does not belong"):
        export(prepared, result=replace(result, run_id="other"))


def test_file_cancel_and_byte_limits(minimal_optic, tmp_path, monkeypatch):
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * 500)
    monkeypatch.setattr(files, "MAX_REPLAY_BYTES", 100)
    with pytest.raises(ValueError, match="input limit"):
        load(path)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(CalculationCancelled):
        files.load_manifest(None, {}, lambda *a: None, cancelled)
    with pytest.raises(CalculationCancelled):
        files.prepare_export(None, {}, lambda *a: None, cancelled)


@pytest.mark.parametrize("nested", [False, True])
def test_file_aperture_disables_portable_export_without_reopening(
    minimal_optic, tmp_path, monkeypatch, nested
):
    from optiland.physical_apertures.polygon import FileAperture

    vertices = tmp_path / "aperture.txt"
    vertices.write_text("-1 -1\n1 -1\n1 1\n-1 1\n", encoding="utf-8")
    aperture = FileAperture(str(vertices))
    if nested:
        from optiland.physical_apertures.rotated import RotatedAperture

        aperture = RotatedAperture(aperture, 0.2)
    minimal_optic.surfaces[1].aperture = aperture
    monkeypatch.setattr(
        FileAperture,
        "_load_vertices",
        lambda *a: pytest.fail("external aperture reopen"),
    )
    prepared = prepare(make_setup(minimal_optic))
    assert prepared.portable_baseline is None
    assert "File-backed apertures" in prepared.replay_unavailable
    assert export(prepared, "script").data is None


def test_open_separate_candidate_uses_complete_native_reconstruction(
    minimal_optic, monkeypatch
):
    from optiland.materials.material_file import MaterialFile
    from optiland.materials.portability import capture_material

    prepared = prepare(make_setup(minimal_optic))
    result = jobs.run(
        prepared.setup.baseline,
        {"setup": prepared},
        lambda *a, **k: None,
        threading.Event(),
    )
    monkeypatch.setattr(
        MaterialFile, "from_dict", classmethod(lambda *a: pytest.fail("catalog lookup"))
    )
    parameters = {"prepared": prepared, "result": result, "operation_id": "open:1"}
    opened = files.open_candidate(
        prepared.setup.baseline, parameters, lambda *a: None, threading.Event()
    )
    assert opened.kind == "open_candidate" and opened.run_id == result.run_id
    optic = opened.prepared.restore()
    assert (
        capture_material(optic.surfaces[1].material_post)
        == prepared.setup.targets[0]
        .pool.resolve(result.report.candidate.materials[0].key)
        .owned_data
    )
    with pytest.raises(ValueError, match="complete portable"):
        files.open_candidate(
            None,
            {**parameters, "result": replace(result, native_candidate=None)},
            lambda *a: None,
            threading.Event(),
        )
    tampered = json.loads(result.native_candidate)
    tampered["name"] = "not the retained candidate"
    with pytest.raises(ValueError, match="differs from"):
        files.open_candidate(
            None,
            {
                **parameters,
                "result": replace(
                    result, native_candidate=json.dumps(tampered).encode()
                ),
            },
            lambda *a: None,
            threading.Event(),
        )


def test_loaded_objective_and_export_transport_limits(
    minimal_optic, tmp_path, monkeypatch
):
    from optiland.optimization.glass_expert_replay import encode_glass_expert_manifest

    prepared = prepare(make_setup(minimal_optic))
    payload = json.loads(export(prepared).data)["payload"]
    payload["resolved_operands"][0]["definition"]["target"] = -100.0
    path = tmp_path / "tampered.json"
    path.write_bytes(encode_glass_expert_manifest(payload))
    with pytest.raises(ValueError, match="recorded resolved"):
        load(path)
    monkeypatch.setattr(files, "MAX_REPLAY_BYTES", 100)
    with pytest.raises(ValueError, match="output limit"):
        export(prepared, "script")
    with pytest.raises(ValueError, match="byte limit"):
        export(prepared)
    with pytest.raises(ValueError, match="transport limit"):
        files._bounded(files.ManifestExport("id", "manifest", b"x" * 90, None, ()))
