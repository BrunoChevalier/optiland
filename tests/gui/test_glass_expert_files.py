"""Native shared-queue file ownership, supersession and reentrant delivery."""

from __future__ import annotations

from dataclasses import replace

import pytest

from optiland_gui.services.glass_expert_file_jobs import (
    LoadedManifest,
    ManifestExport,
    OpenedCandidate,
)
from optiland_gui.services.glass_expert_files import GlassExpertFiles
from tests.gui.test_calculation_jobs import wait_for
from tests.gui.test_glass_expert_jobs import prepare
from tests.gui.test_glass_expert_service import complete
from tests.gui.test_glass_expert_service import frozen as frozen


@pytest.fixture
def controller(frozen):
    files = GlassExpertFiles(frozen.connector)
    yield files
    files.close()


def artifact(request):
    return ManifestExport(
        request.parameters["operation_id"],
        request.parameters["kind"],
        b"owned",
        None,
        (),
    )


def test_constructor_no_file_io_and_exact_export_delivery(
    frozen, controller, monkeypatch
):
    from pathlib import Path

    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("GUI file I/O"))
    prepared = prepare(frozen.setup)
    emitted = []
    controller.exported.connect(emitted.append)
    request = controller.export("manifest", prepared)
    assert request.target.startswith("glass-expert-files:")
    assert request.parameters["prepared"] == prepared
    assert (
        request.progress_policy == "detached" and not request.cancel_on_document_change
    )
    data = artifact(request)
    complete(frozen, request, data)
    assert emitted == [data] and controller.state == "ready" and not controller.busy


def test_native_operand_dialog_export_replays_exact_objective(frozen, tmp_path):
    from optiland.optimization.glass_expert_replay import (
        decode_glass_expert_manifest,
        replay_glass_expert,
    )
    from optiland_gui.optimization_panel import AddOperandDialog
    from tests.gui.test_glass_expert_file_jobs import export, load

    dialog = AddOperandDialog(frozen.connector)
    try:
        dialog.cmbCategory.setCurrentText("Paraxial")
        dialog.cmbType.setCurrentText("f2")
        dialog.spnTarget.setValue(60.0)
        dialog.accept()
        operand = dialog.get_operand_dict()
    finally:
        dialog.deleteLater()
    assert operand["category"] == "Paraxial" and operand["type"] == "f2"
    frozen.connector.set_optimization_operand(0, operand)
    setup = frozen.service.capture_glass_expert_setup(
        (frozen.target,), frozen.setup.settings
    )
    prepared = prepare(setup)
    manifest = export(prepared)
    path = tmp_path / "native-dialog-setup.json"
    path.write_bytes(manifest.data)
    loaded = load(path, source_stamps=setup.source_stamps)
    assert loaded.prepared.resolved_operands == prepared.resolved_operands
    assert loaded.prepared.resolved_operands[0].as_dict()["category"] == "Paraxial"
    replay = replay_glass_expert(decode_glass_expert_manifest(manifest.data))
    assert replay.report.baseline.merit == pytest.approx(prepared.baseline_merit)
    assert replay.report.candidate_eligible
    assert prepared.resolved_operands[0].as_dict()["target"] == 60.0


@pytest.mark.parametrize("corruption", ["kind", "operation", "type"])
def test_wrong_result_kind_operation_or_type_cannot_deliver(
    frozen, controller, corruption
):
    emitted = []
    controller.exported.connect(emitted.append)
    request = controller.export("script", prepare(frozen.setup))
    data = artifact(request)
    if corruption == "kind":
        data = replace(data, kind="candidate")
    elif corruption == "operation":
        data = replace(data, operation_id="different")
    else:
        data = {"kind": "script", "data": b"not typed"}
    complete(frozen, request, data)
    assert not emitted and controller.state == "failed"


def test_new_operation_stop_and_close_revoke_old_delivery(frozen, controller):
    emitted = []
    controller.exported.connect(emitted.append)
    prepared = prepare(frozen.setup)
    first = controller.export("manifest", prepared)
    second = controller.export("script", prepared)
    complete(frozen, first, artifact(first))
    assert not emitted and controller.busy
    controller.stop()
    complete(frozen, second, artifact(second))
    assert not emitted and not controller.busy
    third = controller.export("manifest", prepared)
    controller.close()
    complete(frozen, third, artifact(third))
    assert not emitted and controller.state == "closed"
    assert controller.load("never-open") is None


def test_ready_callback_supersedes_before_payload_signal(frozen, controller):
    prepared = prepare(frozen.setup)
    emitted, next_request = [], []
    controller.exported.connect(emitted.append)

    def replace_ready():
        if controller.state == "ready" and not next_request:
            next_request.append(controller.export("script", prepared))

    controller.statusChanged.connect(replace_ready)
    request = controller.export("manifest", prepared)
    complete(frozen, request, artifact(request))
    assert next_request and not emitted and controller.busy
    complete(frozen, next_request[0], artifact(next_request[0]))
    assert len(emitted) == 1 and emitted[0].kind == "script"


def test_queued_stop_and_synchronous_completion(frozen, controller):
    prepared = prepare(frozen.setup)

    def stop_queued():
        if controller.state == "queued":
            controller.stop()

    controller.statusChanged.connect(stop_queued)
    assert controller.export("manifest", prepared) is None
    assert controller.state == "cancelled"
    controller.statusChanged.disconnect(stop_queued)
    emitted = []
    controller.exported.connect(emitted.append)

    def finish_queued(request, state):
        if request.target == controller.target and state == "queued":
            complete(frozen, request, artifact(request))

    frozen.jobs.state_changed.connect(finish_queued)
    assert controller.export("manifest", prepared) is None
    assert len(emitted) == 1 and controller.state == "ready"


def test_unrelated_document_edit_preserves_export_and_does_not_publish(
    frozen, controller
):
    optic = frozen.connector.get_optic()
    history = frozen.connector._undo_redo_manager.revision
    request = controller.export("manifest", prepare(frozen.setup))
    frozen.connector.document_state.record("optical")
    emitted = []
    controller.exported.connect(emitted.append)
    complete(frozen, request, artifact(request))
    assert emitted and frozen.connector.get_optic() is optic
    assert frozen.connector._undo_redo_manager.revision == history


def test_load_rechecks_current_source_stamps(frozen, controller):
    emitted = []
    controller.loaded.connect(emitted.append)
    prepared = prepare(frozen.setup)
    request = controller.load("owned-run.json")
    prepared = replace(
        prepared, setup=replace(prepared.setup, run_id=request.parameters["run_id"])
    )
    source_id = prepared.setup.source_stamps[0].source_id
    frozen.connector.materials.remove_source(source_id)
    data = LoadedManifest(request.parameters["operation_id"], prepared, "digest", ())
    complete(frozen, request, data)
    assert emitted and any("missing" in warning for warning in emitted[0].warnings)
    assert emitted[0].prepared is prepared


def test_load_identity_queue_failure_and_worker_failure(
    frozen, controller, monkeypatch
):
    request = controller.load("never-open")
    prepared = prepare(frozen.setup)
    complete(frozen, request, LoadedManifest("wrong", prepared, "digest", ()))
    assert controller.state == "failed"
    request = controller.load("never-open")
    complete(frozen, request, status="failed", error="Read failed")
    assert controller.message == "Read failed"
    monkeypatch.setattr(
        frozen.jobs,
        "submit",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue full")),
    )
    assert controller.load("never-open") is None
    assert controller.message == "queue full" and not controller.busy


def test_open_candidate_kind_run_identity_and_late_close(frozen, controller):
    prepared = prepare(frozen.setup)
    emitted = []
    controller.opened.connect(emitted.append)
    request = controller.open_candidate(prepared, None)
    data = OpenedCandidate(
        request.parameters["operation_id"],
        prepared.setup.baseline,
        prepared.setup.run_id,
        prepared.setup.input_digest,
    )
    complete(frozen, request, replace(data, input_digest="different"))
    assert not emitted and controller.state == "failed"
    request = controller.open_candidate(prepared, None)
    data = replace(data, operation_id=request.parameters["operation_id"])
    complete(frozen, request, data)
    assert emitted == [data]
    request = controller.open_candidate(prepared, None)
    controller.statusChanged.connect(
        lambda: controller.close() if controller.state == "ready" else None
    )
    complete(
        frozen, request, replace(data, operation_id=request.parameters["operation_id"])
    )
    assert emitted == [data] and controller.state == "closed"


def test_invalid_action_and_revoked_generation(frozen, controller):
    prepared = prepare(frozen.setup)
    with pytest.raises(ValueError, match="supported export"):
        controller.export("other", prepared)
    with pytest.raises(ValueError, match="exact prepared"):
        controller.open_candidate(None, None)
    emitted = []
    controller.exported.connect(emitted.append)
    request = controller.export("manifest", prepared)
    frozen.jobs.set_target_visible(controller.target, False)
    complete(frozen, request, artifact(request))
    assert not emitted
    controller.close()
    controller.stop()


@pytest.mark.parametrize(
    "data_change",
    [
        {"data": "not bytes"},
        {"data": None},
        {"warnings": (["mutable"],)},
    ],
)
def test_malformed_artifact_return_is_failed_without_a_qt_exception(
    frozen, controller, data_change
):
    request = controller.export("manifest", prepare(frozen.setup))
    emitted = []
    controller.exported.connect(emitted.append)
    complete(frozen, request, replace(artifact(request), **data_change))
    assert not emitted and controller.state == "failed"


def test_actual_shared_process_export_load_without_document_acceptance(
    frozen, controller, qapp, tmp_path, monkeypatch
):
    prepared = prepare(frozen.setup)
    monkeypatch.setattr(frozen.jobs, "_dispatch", frozen.dispatch)
    exported, loaded = [], []
    controller.exported.connect(exported.append)
    controller.loaded.connect(loaded.append)
    optic = frozen.connector.get_optic()
    history = frozen.connector._undo_redo_manager.revision
    controller.export("manifest", prepared)
    wait_for(qapp, lambda: bool(exported) or controller.state == "failed", timeout=30)
    assert exported, controller.message
    path = tmp_path / "run.json"
    path.write_bytes(exported[0].data)
    controller.load(path)
    wait_for(qapp, lambda: bool(loaded) or controller.state == "failed", timeout=30)
    assert loaded, controller.message
    assert loaded[0].prepared.setup.run_id != prepared.setup.run_id
    assert frozen.connector.get_optic() is optic
    assert frozen.connector._undo_redo_manager.revision == history
    request = frozen.service.prepare_glass_expert(loaded[0].prepared.setup)
    assert request is not None
    wait_for(
        qapp,
        lambda: (
            frozen.service.glass_expert_prepared() is not None
            or frozen.service.glass_expert_status()[0] == "failed"
        ),
        timeout=30,
    )
    assert not frozen.service.glass_expert_freshness().allowed
