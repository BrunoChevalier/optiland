"""Live editing and file boundaries respect the short acceptance critical section."""

from __future__ import annotations

import pickle
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSignalBlocker

from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.services.job_records import OpticSnapshot
from optiland_gui.system_properties_panel import (
    ApertureEditor,
    FieldsEditor,
    WavelengthsEditor,
)


@pytest.fixture
def guarded_document(qapp, minimal_optic):
    connector = OptilandConnector()
    connector._optic = minimal_optic
    connector.notify_change("replacement")
    notifications = []
    connector.toast_manager = SimpleNamespace(
        notify=lambda text, severity, **kwargs: notifications.append((text, severity))
    )
    yield connector, notifications
    connector.document_acceptance._publishing = False
    connector.calculation_jobs.shutdown()
    connector.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def state(connector):
    return (
        pickle.dumps(connector.get_optic().to_dict()),
        connector.document_state.edit_token,
        connector._undo_redo_manager.revision,
        connector._file_service.get_current_filepath(),
        connector.is_modified(),
    )


@pytest.mark.parametrize(
    "operation",
    ["comment", "radius", "type", "add", "remove", "stop", "geometry", "polarization"],
)
def test_service_edit_rejected_before_undo_capture(
    guarded_document, monkeypatch, operation
):
    connector, _ = guarded_document
    surface = connector._surface_service
    operations = {
        "comment": lambda: surface.set_surface_data(1, connector.COL_COMMENT, "draft"),
        "radius": lambda: surface.set_surface_data(1, connector.COL_RADIUS, "65"),
        "type": lambda: surface.set_surface_type(1, "biconic"),
        "add": surface.add_surface,
        "remove": lambda: surface.remove_surface(1),
        "stop": lambda: surface.set_stop_surface(1),
        "geometry": lambda: surface.set_surface_geometry_params(1, {}),
        "polarization": lambda: connector.set_polarization_state("unpolarized"),
    }
    before = state(connector)
    monkeypatch.setattr(
        connector, "_capture_optic_state", lambda: pytest.fail("captured blocked edit")
    )
    connector.document_acceptance._publishing = True
    with pytest.raises(ValueError, match="being applied"):
        operations[operation]()
    assert state(connector) == before


@pytest.mark.parametrize(
    "operation",
    [
        "aperture",
        "field_type",
        "add_field",
        "remove_field",
        "field_table",
        "add_wavelength",
        "remove_wavelength",
        "primary",
        "wavelength_table",
    ],
)
@pytest.mark.parametrize("phase", ["publication", "preparation"])
def test_native_property_slots_reject_without_partial_edits(
    guarded_document, monkeypatch, operation, phase
):
    connector, notifications = guarded_document
    optic = connector.get_optic()
    optic.fields.add(y=1)
    optic.wavelengths.add(0.63)
    if operation == "aperture":
        editor = ApertureEditor(connector)
        editor.load_data()
        with QSignalBlocker(editor.spnApertureValue):
            editor.spnApertureValue.setValue(12)
        apply = editor.apply_aperture_changes
    elif "field" in operation:
        editor = FieldsEditor(connector)
        editor.load_data()
        editor.tableFields.setCurrentCell(1, 0)
        if operation == "field_type":
            with QSignalBlocker(editor.cmbFieldType):
                editor.cmbFieldType.setCurrentIndex(
                    editor.cmbFieldType.findData("object_height")
                )
        editor.tableFields.item(0, 0).setText("2")
        apply = {
            "field_type": editor.apply_field_type_change,
            "add_field": editor.add_field,
            "remove_field": editor.remove_field,
            "field_table": editor.apply_table_field_changes,
        }[operation]
    else:
        editor = WavelengthsEditor(connector)
        editor.load_data()
        editor.tableWavelengths.setCurrentCell(1, 0)
        editor.tableWavelengths.item(0, 0).setText("0.6")
        apply = {
            "add_wavelength": editor.add_wavelength,
            "remove_wavelength": editor.remove_wavelength,
            "primary": editor.set_primary_wavelength,
            "wavelength_table": editor.apply_table_wavelength_changes,
        }[operation]
    try:
        before = state(connector)
        if phase == "publication":
            connector.document_acceptance._publishing = True
        else:
            monkeypatch.setattr(
                type(connector.material_apply), "busy", property(lambda _: True)
            )
        apply()
        if phase == "publication":
            assert state(connector) == before
            assert notifications[-1] == (
                "A prepared document change is being applied.",
                "warning",
            )
        else:
            assert state(connector)[0] != before[0]
            assert (
                connector.document_state.edit_token.revision == before[1].revision + 1
            )
            assert not notifications
    finally:
        editor.deleteLater()


def test_preparation_does_not_lock_ordinary_edits(guarded_document, monkeypatch):
    connector, _ = guarded_document
    monkeypatch.setattr(
        type(connector.material_apply), "busy", property(lambda _: True)
    )
    before = connector.document_state.edit_token
    connector.set_surface_data(1, connector.COL_COMMENT, "edited while preparing")
    editor = WavelengthsEditor(connector)
    try:
        editor.add_wavelength()
        connector.set_polarization_state("unpolarized")
        assert connector.get_optic().surfaces[1].comment == "edited while preparing"
        assert connector.get_optic().wavelengths.num_wavelengths == 2
        assert connector.document_state.edit_token.revision == before.revision + 3
        assert connector.is_modified()
    finally:
        editor.deleteLater()


@pytest.mark.parametrize(
    "operation",
    [
        "new_system",
        "load",
        "save",
        "load_from_object",
        "import_zemax",
        "import_codev",
        "import_oslo",
        "export_zemax",
        "export_codev",
    ],
)
@pytest.mark.parametrize("blocked_by", ["publication", "preparation"])
def test_sync_file_boundaries_do_not_capture_parse_or_write(
    guarded_document, tmp_path, monkeypatch, operation, blocked_by
):
    connector, notifications = guarded_document
    service = connector._file_service
    if blocked_by == "publication":
        connector.document_acceptance._publishing = True
    else:
        monkeypatch.setattr(
            type(connector.material_apply), "busy", property(lambda _: True)
        )
    destination = tmp_path / "preserved.json"
    destination.write_bytes(b"existing contents")
    before = state(connector)
    monkeypatch.setattr(
        connector, "_capture_optic_state", lambda: pytest.fail("captured blocked save")
    )
    arguments = (
        ()
        if operation == "new_system"
        else (connector.get_optic(),)
        if operation == "load_from_object"
        else (str(destination),)
    )
    getattr(service, operation)(*arguments)
    assert state(connector) == before
    assert destination.read_bytes() == b"existing contents"
    assert list(tmp_path.iterdir()) == [destination]
    assert notifications[-1][1] == "error"
    assert (
        "being applied" in notifications[-1][0]
        or "material Apply" in notifications[-1][0]
    )


@pytest.mark.parametrize("operation", ["request_load", "request_output"])
@pytest.mark.parametrize("blocked_by", ["publication", "preparation"])
def test_async_file_start_keeps_existing_candidate_and_does_not_submit(
    guarded_document, monkeypatch, operation, blocked_by
):
    connector, notifications = guarded_document
    operations = connector.file_operations
    candidate = object()
    operations._candidate = candidate
    if blocked_by == "publication":
        connector.document_acceptance._publishing = True
    else:
        monkeypatch.setattr(
            type(connector.material_apply), "busy", property(lambda _: True)
        )
    monkeypatch.setattr(
        operations.jobs, "submit", lambda *a, **k: pytest.fail("submitted blocked file")
    )
    monkeypatch.setattr(
        OpticSnapshot, "capture", lambda *a: pytest.fail("captured blocked file")
    )
    assert getattr(operations, operation)("unused.json") is None
    assert operations._candidate is candidate
    assert not operations.busy
    assert notifications


def test_file_candidate_rechecks_guard_after_validation(guarded_document, monkeypatch):
    connector, _ = guarded_document
    before = state(connector)
    original = connector.get_optic()

    def validation(*args, **kwargs):
        connector.document_acceptance._publishing = True

    monkeypatch.setattr(connector, "_initialize_optic_structure", validation)
    with pytest.raises(ValueError, match="being applied"):
        connector._file_service._publish_candidate(object(), "new.json", modified=True)
    assert connector.get_optic() is original
    assert state(connector) == before


def test_sync_new_and_oslo_install_validated_candidates(
    guarded_document, monkeypatch, minimal_optic
):
    from optiland_gui.services import file_service

    connector, notifications = guarded_document
    service = connector._file_service
    original = connector.get_optic()
    connector._undo_redo_manager.add_state(original.to_dict())
    service._current_filepath = "previous.json"
    service.new_system()
    assert connector.get_optic() is not original
    assert len(connector.get_optic().surfaces) == 3
    assert not connector._undo_redo_manager.can_undo()
    assert service.get_current_filepath() is None
    assert not connector.is_modified()
    monkeypatch.setattr(
        file_service, "load_oslo_file", lambda *args, **kwargs: minimal_optic
    )
    assert service.import_oslo("synthetic.len") is None
    assert connector.get_optic() is minimal_optic
    assert connector.is_modified()
    assert notifications[-1][1] == "success"


def test_editor_without_notification_host_still_rejects(guarded_document):
    connector, _ = guarded_document
    connector.toast_manager = None
    editor = FieldsEditor(connector)
    before = state(connector)
    connector.document_acceptance._publishing = True
    try:
        editor.add_field()
        assert state(connector) == before
    finally:
        editor.deleteLater()


def test_async_output_rechecks_after_snapshot_capture(guarded_document, monkeypatch):
    connector, notifications = guarded_document

    def capture(optic):
        connector.document_acceptance._publishing = True
        return object()

    monkeypatch.setattr(OpticSnapshot, "capture", capture)
    assert connector.file_operations.request_output("unused.json") is None
    assert not connector.file_operations.busy
    assert "being applied" in notifications[-1][0]


def test_loaded_candidate_guard_precedes_snapshot_restore(guarded_document):
    connector, notifications = guarded_document
    operations = connector.file_operations
    op = operations._new_operation("open", "input.json", "optiland", "file-open", None)
    operations._latest_open = op.identifier
    assert operations.has_pending_load
    before = state(connector)
    connector.document_acceptance._publishing = True
    # No snapshot exists: attempting restoration instead of checking the guard
    # would produce a different diagnostic and miss the acceptance boundary.
    assert not operations._accept(op, {}, op.edit_token)
    assert "being applied" in notifications[-1][0]
    assert state(connector) == before
    operations._complete(op)
    assert not operations.has_pending_load


def test_sync_save_rechecks_before_replacing_file(
    guarded_document, tmp_path, monkeypatch
):
    from optiland_gui.services import file_service

    connector, notifications = guarded_document
    destination = tmp_path / "preserved.json"
    destination.write_bytes(b"existing contents")
    before = state(connector)
    original_dump = file_service.json.dump

    def dump(*args, **kwargs):
        original_dump(*args, **kwargs)
        connector.document_acceptance._publishing = True

    monkeypatch.setattr(file_service.json, "dump", dump)
    connector._file_service.save(str(destination))
    assert state(connector) == before
    assert destination.read_bytes() == b"existing contents"
    assert list(tmp_path.iterdir()) == [destination]
    assert "being applied" in notifications[-1][0]


def test_close_cancels_apply_before_job_drain_and_exposes_pending_load(
    guarded_document, monkeypatch
):
    connector, _ = guarded_document
    operations = connector.file_operations
    events = []
    monkeypatch.setattr(
        connector.material_apply, "cancel", lambda: events.append("apply")
    )
    monkeypatch.setattr(
        operations.jobs, "cancel_cancellable", lambda **kwargs: events.append("jobs")
    )
    operations._candidate = (object(), {})
    assert operations.has_pending_load and not operations.busy
    assert not connector.document_acceptance.check_start("material").allowed
    operations.begin_close()
    assert events == ["apply", "jobs"]
    assert operations.closing and not operations.has_pending_load
    assert not connector.document_acceptance.check_start("material").allowed
    assert operations.request_load("unused.json") is None
    assert operations.request_output("unused.json") is None
    operations.cancel_close()
    assert not operations.closing
    assert connector.document_acceptance.check_start("material").allowed


@pytest.mark.parametrize("closing", [False, True])
def test_accepted_write_may_drain_but_not_during_publication(
    guarded_document, monkeypatch, closing
):
    connector, notifications = guarded_document
    operations = connector.file_operations
    queued = []
    monkeypatch.setattr(
        operations, "_queue", lambda op, handler: queued.append(handler)
    )
    operations._closing = closing
    op = operations._new_operation("save", "output.json", "optiland", "output", None)
    operations._requests["prepare"] = op
    result = SimpleNamespace(
        request=SimpleNamespace(job_id="prepare"),
        status="succeeded",
        data={"digest": "abc"},
    )
    operations._finished(result)
    assert queued == ["publish_output"]
    op.phase = "preparing"
    op.staged_path = "staged.tmp"
    operations._requests["prepare"] = op
    connector.document_acceptance._publishing = True
    operations._finished(result)
    assert queued == ["publish_output", "cleanup_output"]
    assert "not started" in notifications[-1][0]
    assert not operations.closing


def test_late_save_confirmation_preserves_document_on_guard_rejection(
    guarded_document, monkeypatch
):
    connector, notifications = guarded_document
    operations = connector.file_operations
    op = operations._new_operation("save", "saved.json", "optiland", "output", None)
    op.save_sequence = operations.next_save_sequence()
    before = state(connector)
    connector.document_acceptance._publishing = True
    operations._published(op)
    assert state(connector) == before
    assert not operations.busy
    assert (
        "File saved, but active document state was not updated" in notifications[-1][0]
    )


def test_confirmed_export_keeps_native_file_and_modified_state(guarded_document):
    connector, notifications = guarded_document
    operations = connector.file_operations
    before = state(connector)
    op = operations._new_operation("export", "lens.zmx", "zemax", "output", None)
    operations._published(op)
    assert state(connector) == before
    assert not operations.busy
    assert notifications[-1][1] == "success"
