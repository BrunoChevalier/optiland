"""Real-worker artifacts use existing atomic file lifecycle, never lens Save state."""

from __future__ import annotations

import csv
import pickle

import pytest
from PIL import Image

from optiland_gui.services.file_artifacts import (
    BytesArtifact,
    CsvArtifact,
    RgbaArtifact,
)
from optiland_gui.services.job_records import JobResult, OpticSnapshot
from tests.gui.test_calculation_jobs import wait_for
from tests.gui.test_file_operations import edit_comment, stall
from tests.gui.test_file_operations import file_operations as file_operations


def document_state(connector):
    return (
        pickle.dumps(connector.get_optic().to_dict()),
        connector.document_state.edit_token,
        connector._undo_redo_manager.revision,
        connector._file_service.get_current_filepath(),
        connector.is_modified(),
    )


@pytest.mark.parametrize("action", ["none", "edit", "new"])
def test_export_captures_table_and_never_changes_native_save_state(
    qapp, tmp_path, file_operations, monkeypatch, action
):
    connector, operations, _, results, notifications = file_operations
    operations.service._current_filepath = str(tmp_path / "original-lens.json")
    connector._undo_redo_manager.add_state(connector.get_optic().to_dict())
    artifact = CsvArtifact(
        ("source", "record", "n_d"),
        (("user:A", "reference-1", 1.5168000345005883),),
        (("fingerprint", "sha256:original"),),
    )
    stall(operations, "prepare_artifact_output", delay=0.1)

    def no_lens_snapshot(*args):
        raise AssertionError("Artifact export must not capture the live lens")

    monkeypatch.setattr(OpticSnapshot, "capture", no_lens_snapshot)
    request = operations.request_artifact(tmp_path / "materials.csv", artifact)
    assert request is not None and request.snapshot is None
    operation = request.context
    if action == "edit":
        edit_comment(connector, "new lens edit after export capture")
    elif action == "new":
        connector.new_system()
    expected = document_state(connector)
    wait_for(qapp, lambda: not operations.busy, timeout=30)
    assert document_state(connector) == expected
    with open(tmp_path / "materials.csv", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["record"] == "reference-1"
    assert rows[0]["provenance:fingerprint"] == "sha256:original"
    assert float(rows[0]["n_d"]) == artifact.rows[0][2]
    assert operation.artifact is None
    assert all(
        "artifact" not in result.request.parameters
        for result in results
        if not result.request.handler.endswith("prepare_artifact_output")
    )
    assert any(
        "Exported" in text and severity == "success" for text, severity in notifications
    )


def test_real_png_worker_keeps_pixels_and_embedded_provenance(
    qapp, tmp_path, file_operations
):
    connector, operations, *_ = file_operations
    artifact = RgbaArtifact(
        2,
        1,
        8,
        bytes((0, 64, 255, 255, 255, 128, 0, 100)),
        (("optiland.provenance", '{"view":"n(λ)","units":"µm"}'),),
    )
    before = document_state(connector)
    operations.request_artifact(tmp_path / "plot.png", artifact)
    wait_for(qapp, lambda: not operations.busy, timeout=30)
    with Image.open(tmp_path / "plot.png") as image:
        assert image.tobytes() == artifact.pixels
        assert image.info["optiland.provenance"] == artifact.metadata[0][1]
    assert document_state(connector) == before


def test_artifact_cancel_after_staging_preserves_destination(
    qapp, tmp_path, file_operations
):
    connector, operations, progress, results, _ = file_operations
    path = tmp_path / "preset.json"
    path.write_bytes(b"previous output")
    before = document_state(connector)
    stall(operations, "prepare_artifact_output", "after", 10)
    request = operations.request_artifact(path, BytesArtifact(b"captured replacement"))
    wait_for(qapp, lambda: "Fixture completed prepare_artifact_output" in progress)
    operations.cancel_pending()
    wait_for(qapp, lambda: not operations.busy, timeout=30)
    assert path.read_bytes() == b"previous output"
    assert document_state(connector) == before
    assert any(result.status == "cancelled" for result in results)
    assert request.context.artifact is None
    assert not list(tmp_path.glob(".optiland-*.tmp"))


def test_artifact_same_path_supersession_and_close_drain(
    qapp, tmp_path, file_operations
):
    connector, operations, *_ = file_operations
    path = tmp_path / "setup.json"
    before = document_state(connector)
    stall(operations, "prepare_artifact_output", delay=0.1)
    operations.request_artifact(path, BytesArtifact(b"first"))
    operations.request_artifact(path, BytesArtifact(b"latest"))
    assert operations.path_busy(path)
    operations.begin_close()
    assert operations.request_artifact(path, BytesArtifact(b"closed")) is None
    wait_for(qapp, lambda: not operations.busy, timeout=30)
    assert path.read_bytes() == b"latest"
    assert document_state(connector) == before
    assert not list(tmp_path.glob(".optiland-*.tmp"))


def test_uncertain_artifact_publication_reconciles_without_cleaning_lens(
    qapp, tmp_path, file_operations
):
    connector, operations, progress, results, notifications = file_operations
    path = tmp_path / "captured.json"
    before = document_state(connector)
    stall(operations, "publish_output", "after", 10)
    operations.jobs._publication_deadline_ms = 500
    operations.request_artifact(path, BytesArtifact(b'{"view":"captured"}'))
    wait_for(qapp, lambda: "Fixture completed publish_output" in progress)
    operations.begin_close()
    wait_for(qapp, lambda: not operations.busy, timeout=30)
    assert path.read_bytes() == b'{"view":"captured"}'
    assert any(result.outcome_unknown for result in results)
    assert any(severity == "success" for _, severity in notifications)
    assert document_state(connector) == before
    assert not list(tmp_path.glob(".optiland-*.tmp"))


def test_artifact_rejects_bad_admission_and_busy_guard_without_file_effects(
    tmp_path, file_operations
):
    connector, operations, _, _, notifications = file_operations
    path = tmp_path / "preserve.json"
    path.write_bytes(b"original")
    before = document_state(connector)
    artifact = BytesArtifact(b"valid")
    object.__setattr__(artifact, "data", bytearray(b"mutable"))
    assert operations.request_artifact(path, artifact) is None
    connector.document_acceptance._publishing = True
    try:
        assert operations.request_artifact(path, BytesArtifact(b"new")) is None
    finally:
        connector.document_acceptance._publishing = False
    assert not operations.busy
    assert len(notifications) == 2
    assert path.read_bytes() == b"original"
    assert document_state(connector) == before


def test_synchronous_queued_cancel_cannot_leave_an_orphan_artifact(
    qapp, tmp_path, file_operations
):
    connector, operations, *_ = file_operations
    path = tmp_path / "cancelled.json"
    path.write_bytes(b"original")
    before = document_state(connector)

    def queued(request, state):
        if state == "queued" and request.handler.endswith("prepare_artifact_output"):
            operations.cancel_pending()

    operations.jobs.state_changed.connect(queued)
    assert operations.request_artifact(path, BytesArtifact(b"new")) is None
    wait_for(qapp, lambda: not operations.busy, timeout=30)
    assert path.read_bytes() == b"original"
    assert document_state(connector) == before
    assert not list(tmp_path.glob(".optiland-*.tmp"))


def test_duplicate_terminal_callback_cannot_reclaim_owned_artifact_cleanup(
    qapp, tmp_path, file_operations
):
    connector, operations, _, _, notifications = file_operations
    path = tmp_path / "cancelled.json"
    path.write_bytes(b"original")
    before = document_state(connector)
    request = operations.request_artifact(path, BytesArtifact(b"new"))
    operations.jobs._pending.remove(request)
    result = JobResult(request, "failed", error="preparation failed")
    operations.jobs.finished.emit(result)
    assert request.context.phase == "cleaning"
    assert request.context.artifact is None
    errors = len(notifications)
    operations.jobs.finished.emit(result)
    assert len(notifications) == errors
    wait_for(qapp, lambda: not operations.busy, timeout=30)
    operations.jobs.finished.emit(result)
    assert not operations.busy and len(notifications) == errors
    assert path.read_bytes() == b"original"
    assert document_state(connector) == before
