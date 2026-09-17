"""Public import and property actions publish one classified document change."""

from __future__ import annotations

import json

import pytest
from PySide6.QtTest import QTest

from optiland_gui.lens_editor import LensEditor
from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.services import file_service
from optiland_gui.system_properties_panel import SystemPropertiesPanel


@pytest.mark.parametrize("kind", ["json", "zemax", "codev", "restore"])
def test_successful_public_load_publishes_single_replacement(
    qapp, minimal_optic, tmp_path, monkeypatch, kind
):
    connector = OptilandConnector()
    before = connector.document_state.token
    commits, invalidations, loaded, changed = [], [], [], []
    connector.document_state.committed.connect(commits.append)
    connector.document_state.changed.connect(invalidations.append)
    connector.opticLoaded.connect(lambda: loaded.append(True))
    connector.opticChanged.connect(lambda: changed.append(True))
    if kind == "json":
        path = tmp_path / "sample.json"
        path.write_text(json.dumps(minimal_optic.to_dict()), encoding="utf-8")
        connector._file_service.load(str(path))
    elif kind == "restore":
        connector._restore_optic_state(minimal_optic.to_dict())
    else:
        # Parsing belongs to the file-format tests; exercise the real successful
        # publication adapter with a prepared optical model here.
        monkeypatch.setattr(
            file_service, f"load_{kind}_file", lambda path: minimal_optic
        )
        getattr(connector._file_service, f"import_{kind}")("sample.input")
    assert connector.get_optic().name == minimal_optic.name
    assert connector.document_state.token.document_id != before.document_id
    assert len(commits) == len(invalidations) == len(loaded) == len(changed) == 1
    assert commits[0].categories == {"replacement"}


def test_system_property_actions_commit_once_and_refresh_their_tables(qapp):
    connector = OptilandConnector()
    panel = SystemPropertiesPanel(connector)
    commits = []
    connector.document_state.committed.connect(commits.append)
    fields = panel.fieldsEditor
    waves = panel.wavelengthsEditor
    try:
        fields.add_field()
        assert fields.tableFields.rowCount() == 2
        fields.tableFields.setCurrentCell(1, 0)
        fields.remove_field()
        assert fields.tableFields.rowCount() == 1
        waves.add_wavelength()
        assert waves.tableWavelengths.rowCount() == 2
        waves.tableWavelengths.setCurrentCell(1, 0)
        waves.set_primary_wavelength()
        assert waves.tableWavelengths.item(1, 2).text() == "Yes"
        waves.tableWavelengths.setCurrentCell(1, 0)
        waves.remove_wavelength()
        assert waves.tableWavelengths.rowCount() == 1
        assert connector.get_optic().wavelengths.primary_index == 0
        assert len(commits) == 5
        assert all(change.affects_optics for change in commits)
    finally:
        panel.deleteLater()


@pytest.mark.parametrize("has_focus", [True, False])
def test_expanded_parameter_refresh_retains_focused_draft(
    qapp, minimal_optic, has_focus
):
    connector = OptilandConnector()
    connector.load_optic_from_object(minimal_optic)
    connector.set_surface_type(1, "biconic")
    connector.set_surface_geometry_params(1, {"Radius X": "50.123456789"})
    editor = LensEditor(connector)
    editor.resize(1000, 500)
    editor.show()
    editor.toggle_properties_widget(1)
    try:
        properties = editor.tableWidget.cellWidget(2, 0)
        properties.tabs.setCurrentIndex(1)
        qapp.processEvents()
        editor.activateWindow()
        focused = properties.input_widgets["Radius X"]
        (focused if has_focus else editor.tableWidget).setFocus()
        QTest.qWait(10)
        assert focused.hasFocus() == has_focus
        focused.setText("Uncommitted radius draft")
        draft = properties.capture_shape_draft()
        connector.set_surface_geometry_params(1, {"Conic X": "-0.25"})
        assert focused.hasFocus() == has_focus
        assert focused.text() == "Uncommitted radius draft"
        assert float(properties.input_widgets["Conic X"].text()) == -0.25
        assert editor.tableWidget.cellWidget(2, 0) is properties
        assert properties.capture_shape_draft() == draft
        assert "design changed" in properties.shape_status.text()
        assert float(properties.surface.geometry.Rx) == 50.123456789
        token = connector.document_state.edit_token
        revision = connector._undo_redo_manager.revision
        properties.apply_changes()
        assert connector.document_state.edit_token == token
        assert connector._undo_redo_manager.revision == revision
        properties.reload_shape()
        assert not properties.capture_shape_draft()["changes"]
        assert float(focused.text()) == 50.123456789
        assert float(properties.surface.geometry.Rx) == 50.123456789
    finally:
        editor.close()
        editor.deleteLater()
        connector.calculation_jobs.shutdown()
        connector.deleteLater()
