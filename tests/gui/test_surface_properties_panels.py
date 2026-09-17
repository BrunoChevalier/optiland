"""Independent properties panels retain ownership, drafts and menu state."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from optiland_gui.lens_editor import LensEditor, SurfacePropertiesWidget
from optiland_gui.optiland_connector import OptilandConnector


@pytest.fixture
def editor(qapp, minimal_optic):
    connector = OptilandConnector()
    connector.load_optic_from_object(minimal_optic)
    connector.set_surface_type(1, "biconic")
    connector.set_surface_type(2, "biconic")
    connector._undo_redo_manager.clear_stacks()
    widget = LensEditor(connector)
    widget.resize(1000, 950)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    connector.calculation_jobs.shutdown()
    connector.deleteLater()
    qapp.processEvents()


def panel(editor, owner):
    widget = editor.tableWidget.cellWidget(
        editor.map_surface_index_to_ui_row(owner) + 1, 0
    )
    assert isinstance(widget, SurfacePropertiesWidget)
    return widget


def menu_action(editor, owner, monkeypatch):
    actions = []

    class InspectMenu(QMenu):
        def exec(self, position):
            actions.extend(
                action
                for action in self.actions()
                if action.text() == "Toggle Surface Properties"
            )

    monkeypatch.setattr("optiland_gui.lens_editor.QMenu", InspectMenu)
    table = editor.tableWidget
    row = editor.map_surface_index_to_ui_row(owner)
    table.scrollToItem(table.item(row, 1))
    position = table.visualItemRect(table.item(row, 1)).center()
    editor.show_context_menu(position)
    assert len(actions) == 1
    return actions[0]


def test_close_keeps_other_panels_drafts_selection_and_optical_state(
    editor, monkeypatch
):
    connector = editor.connector
    token = connector.document_state.edit_token
    changed = []
    connector.document_state.committed.connect(changed.append)
    monkeypatch.setattr(editor, "load_data", MagicMock(side_effect=AssertionError))
    table = editor.tableWidget
    table.selectRow(2)
    selected = editor.interaction_state.selected_surfaces
    type_widget = table.cellWidget(2, 0)

    editor.toggle_properties_widget(2)
    second = panel(editor, 2)
    next(iter(second.input_widgets.values())).setText("1.234")
    editor.toggle_properties_widget(0)
    editor.toggle_properties_widget(1)
    first = panel(editor, 1)
    first.close_button.click()

    assert editor.open_prop_source_rows == {0, 2}
    assert panel(editor, 2) is second
    assert next(iter(second.input_widgets.values())).text() == "1.234"
    assert table.cellWidget(editor.map_surface_index_to_ui_row(2), 0) is type_widget
    assert editor.interaction_state.selected_surfaces == selected
    assert connector.document_state.edit_token == token
    assert changed == []
    assert not connector._undo_redo_manager.can_undo()
    # Closing an earlier panel has shifted this close button's visual row.
    second.close_button.click()
    assert editor.open_prop_source_rows == {0}
    assert table.rowCount() == 5
    editor.toggle_properties_widget(2)
    assert next(iter(panel(editor, 2).input_widgets.values())).text() != "1.234"


@pytest.mark.parametrize("owner", range(4))
def test_context_toggle_checked_for_its_owner_including_object_and_image(
    editor, monkeypatch, owner
):
    other = (owner + 1) % 4
    editor.toggle_properties_widget(other)
    action = menu_action(editor, owner, monkeypatch)
    assert action.isCheckable() and action.isEnabled() and not action.isChecked()
    action.trigger()
    assert editor.open_prop_source_rows == {other, owner}
    action = menu_action(editor, owner, monkeypatch)
    assert action.isChecked()
    action.trigger()
    assert editor.open_prop_source_rows == {other}
    editor.toggle_properties_widget(owner)
    panel(editor, owner).close_button.click()
    assert not menu_action(editor, owner, monkeypatch).isChecked()


def test_adjacent_panel_mappings_and_edits_after_non_lifo_closing(editor):
    for owner in range(4):
        editor.toggle_properties_widget(owner)
    assert [editor.map_ui_row_to_surface_index(row) for row in range(8)] == [
        0,
        0,
        1,
        1,
        2,
        2,
        3,
        3,
    ]
    assert [editor.is_properties_row(row) for row in range(8)] == [False, True] * 4
    panel(editor, 1).close_button.click()
    assert [editor.map_ui_row_to_surface_index(row) for row in range(7)] == [
        0,
        0,
        1,
        2,
        2,
        3,
        3,
    ]
    table = editor.tableWidget
    table.item(editor.map_surface_index_to_ui_row(2), 1).setText("Right owner")
    assert editor.connector.get_optic().surfaces[2].comment == "Right owner"
    assert editor.connector.get_optic().surfaces[1].comment != "Right owner"
    for owner in (0, 2, 3):
        row = editor.map_surface_index_to_ui_row(owner) + 1
        assert table.columnSpan(row, 0) == table.columnCount()


def test_structural_changes_retain_each_owner(editor):
    editor.toggle_properties_widget(1)
    editor.toggle_properties_widget(2)
    editor.connector.add_surface(index=1)
    assert editor.open_prop_source_rows == {2, 3}
    assert panel(editor, 2).input_widgets
    assert panel(editor, 3).input_widgets
    editor.connector.remove_surface(2)
    assert editor.open_prop_source_rows == {2}
    assert panel(editor, 2).input_widgets
    panel(editor, 2).close_button.click()
    assert not editor.open_prop_source_rows


def test_type_tool_and_keyboard_close_share_state(editor, qapp):
    editor.tableWidget.cellWidget(1, 0).props_button.click()
    editor.toggle_properties_widget(2)
    close = panel(editor, 2).close_button
    assert "surface 2" in close.accessibleName()
    assert close.toolTip()
    close.setFocus()
    qapp.processEvents()
    QTest.keyClick(close, Qt.Key_Space)
    assert editor.open_prop_source_rows == {1}
    assert editor.tableWidget.hasFocus()
    assert editor.map_ui_row_to_surface_index(editor.tableWidget.currentRow()) == 2
    editor.tableWidget.cellWidget(1, 0).props_button.click()
    assert not editor.open_prop_source_rows


def test_new_document_drops_old_panels(editor):
    editor.toggle_properties_widget(1)
    editor.toggle_properties_widget(2)
    editor.connector.new_system()
    assert not editor.open_prop_source_rows
    assert editor.tableWidget.rowCount() == editor.connector.get_surface_count()


def test_shape_applies_only_dirty_field_without_rounding_other_parameters(
    editor, monkeypatch
):
    connector = editor.connector
    connector.set_surface_geometry_params(1, {"Radius X": "50.123456789"})
    editor.toggle_properties_widget(1)
    properties = panel(editor, 1)
    before = float(connector.get_optic().surfaces[1].geometry.Rx)
    calls = []
    setter = connector.set_surface_geometry_params

    def record(row, changes):
        calls.append((row, changes))
        setter(row, changes)

    monkeypatch.setattr(connector, "set_surface_geometry_params", record)
    properties.input_widgets["Conic X"].setText("-0.75")
    properties.input_widgets["Conic X"].editingFinished.emit()
    assert calls == [(1, {"Conic X": "-0.75"})]
    assert float(connector.get_optic().surfaces[1].geometry.Rx) == before
    assert float(connector.get_optic().surfaces[1].geometry.kx) == -0.75
    assert not properties.capture_shape_draft()["changes"]
    properties.input_widgets["Radius X"].editingFinished.emit()
    assert len(calls) == 1


def test_unrelated_edit_stales_shape_draft_until_explicit_reload(editor):
    connector = editor.connector
    editor.toggle_properties_widget(1)
    properties = panel(editor, 1)
    properties.input_widgets["Conic X"].setText("-0.5")
    original_token = properties.capture_shape_draft()["edit_token"]
    connector.set_surface_data(2, connector.COL_COMMENT, "Other surface metadata")
    assert original_token != connector.document_state.edit_token
    assert "design changed" in properties.shape_status.text()
    revision = connector._undo_redo_manager.revision
    properties.apply_changes()
    assert float(connector.get_optic().surfaces[1].geometry.kx) == 0
    assert connector._undo_redo_manager.revision == revision
    assert properties.input_widgets["Conic X"].text() == "-0.5"
    properties.shape_reload.click()
    assert not properties.capture_shape_draft()["changes"]
    assert not properties.shape_status.text()
    properties.input_widgets["Conic X"].setText("-0.25")
    properties.apply_changes()
    assert float(connector.get_optic().surfaces[1].geometry.kx) == -0.25


def test_mouse_reload_discards_focused_fresh_draft_without_applying(
    editor, qapp, monkeypatch
):
    connector = editor.connector
    editor.toggle_properties_widget(1)
    properties = panel(editor, 1)
    properties.tabs.setCurrentIndex(1)
    qapp.processEvents()
    editor.activateWindow()
    field = properties.input_widgets["Conic X"]
    field.setFocus()
    qapp.processEvents()
    assert field.hasFocus()
    field.selectAll()
    QTest.keyClicks(field, "-0.375")
    calls = []
    setter = connector.set_surface_geometry_params

    def record(row, changes):
        calls.append((row, changes))
        setter(row, changes)

    monkeypatch.setattr(connector, "set_surface_geometry_params", record)
    token = connector.document_state.edit_token
    QTest.mouseClick(properties.shape_reload, Qt.LeftButton)
    qapp.processEvents()
    assert connector.document_state.edit_token == token
    assert not calls
    assert float(connector.get_optic().surfaces[1].geometry.kx) == 0
    assert not properties.capture_shape_draft()["changes"]


def test_shape_reload_and_apply_reject_deleted_exact_owner(editor):
    connector = editor.connector
    editor.toggle_properties_widget(1)
    properties = panel(editor, 1)
    initial = properties.input_widgets["Conic X"].text()
    properties.input_widgets["Conic X"].setText("-0.5")
    # Retain the widget without delivering a rebuild to probe a delayed callback.
    connector.document_state.committed.disconnect(editor._on_document_change)
    connector.remove_surface(1)
    remaining = connector.get_optic().surfaces[1]
    before = connector.document_state.edit_token
    properties.apply_changes()
    properties.input_widgets["Conic X"].setText(initial)
    properties.refresh_values()
    assert "unavailable" in properties.shape_status.text()
    properties.reload_shape()
    assert connector.document_state.edit_token == before
    assert connector.get_optic().surfaces[1] is remaining
    assert "unavailable" in properties.shape_status.text()


def test_material_context_and_keyboard_actions_capture_retained_surface(
    editor, monkeypatch
):
    connector = editor.connector
    captured = []
    connector.requestBrowseMaterials.connect(captured.append)
    editor.toggle_properties_widget(0)
    editor.toggle_properties_widget(1)
    target = connector.get_optic().surfaces[2]
    actions = []

    class InspectMenu(QMenu):
        def exec(self, position):
            actions.extend(
                action
                for action in self.actions()
                if action.text().startswith("Browse Materials")
            )

    monkeypatch.setattr("optiland_gui.lens_editor.QMenu", InspectMenu)
    table = editor.tableWidget
    row = editor.map_surface_index_to_ui_row(2)
    item = table.item(row, connector.COL_MATERIAL)
    table.scrollToItem(item)
    editor.show_context_menu(table.visualItemRect(item).center())
    assert len(actions) == 1 and not captured
    table.setCurrentCell(editor.map_surface_index_to_ui_row(1), connector.COL_MATERIAL)
    actions[0].trigger()
    assert captured == [target]
    # Subsequent selection does not retarget; a new explicit key action does.
    table.setCurrentCell(editor.map_surface_index_to_ui_row(0), connector.COL_MATERIAL)
    assert captured == [target]
    QTest.keyClick(table, Qt.Key_M, Qt.ControlModifier)
    assert captured[-1] is connector.get_optic().surfaces[0]
    assert not table.currentItem().flags() & Qt.ItemIsEditable
    table.setCurrentCell(editor.map_surface_index_to_ui_row(3), connector.COL_MATERIAL)
    QTest.keyClick(table, Qt.Key_M, Qt.ControlModifier)
    assert captured[-1] is connector.get_optic().surfaces[3]
    table.setCurrentCell(editor.map_surface_index_to_ui_row(1), connector.COL_COMMENT)
    QTest.keyClick(table, Qt.Key_M, Qt.ControlModifier)
    assert len(captured) == 3


def test_material_action_rejects_removed_owner_and_loading_rows(editor, monkeypatch):
    connector = editor.connector
    captured = []
    connector.requestBrowseMaterials.connect(captured.append)
    retained = connector.get_optic().surfaces[1]
    connector.remove_surface(1)
    editor._request_browse_materials(retained)
    assert not captured
    editor._table_loading = True
    try:
        menu = MagicMock(side_effect=AssertionError("A loading table has no actions"))
        monkeypatch.setattr("optiland_gui.lens_editor.QMenu", menu)
        editor.show_context_menu(editor.tableWidget.rect().center())
        editor._request_browse_materials(connector.get_optic().surfaces[1])
        assert not captured
        menu.assert_not_called()
    finally:
        editor._table_loading = False


def test_shape_guard_failure_keeps_draft_and_allows_fresh_retry(editor, monkeypatch):
    connector = editor.connector
    editor.toggle_properties_widget(1)
    properties = panel(editor, 1)
    properties.input_widgets["Conic X"].setText("-0.75")
    token = connector.document_state.edit_token
    with monkeypatch.context() as guard:
        guard.setattr(
            connector.document_acceptance,
            "assert_mutation_allowed",
            MagicMock(side_effect=ValueError("A prepared change is being applied.")),
        )
        properties.apply_changes()
    assert "prepared change" in properties.shape_status.text()
    assert connector.document_state.edit_token == token
    assert float(properties.surface.geometry.kx) == 0
    assert properties.capture_shape_draft()["changes"] == {"Conic X": "-0.75"}
    properties.apply_changes()
    assert float(properties.surface.geometry.kx) == -0.75
    assert not properties.capture_shape_draft()["changes"]


def test_shape_refresh_updates_clean_fields_but_keeps_dirty_text_stale(editor):
    connector = editor.connector
    editor.toggle_properties_widget(1)
    properties = panel(editor, 1)
    field = properties.input_widgets["Conic X"]
    initial_text = field.text()
    field.setText("-0.5")
    field.setText(initial_text)
    assert properties.capture_shape_draft()["surface"] is None
    assert properties.capture_shape_draft()["edit_token"] is None
    connector.set_surface_geometry_params(1, {"Conic X": "-0.25"})
    assert float(field.text()) == -0.25
    assert not properties.capture_shape_draft()["changes"]
    field.setText("-0.75")
    token = properties.capture_shape_draft()["edit_token"]
    connector.set_surface_geometry_params(1, {"Radius X": "63.125"})
    assert field.text() == "-0.75"
    assert float(properties.input_widgets["Radius X"].text()) == 63.125
    assert properties.capture_shape_draft()["edit_token"] == token
    assert "design changed" in properties.shape_status.text()
    properties.apply_changes()
    assert float(properties.surface.geometry.kx) == -0.25
    properties.reload_shape()
    assert float(properties.input_widgets["Radius X"].text()) == 63.125
    assert float(field.text()) == -0.25


def test_material_shortcut_from_properties_row_does_not_capture_a_target(editor):
    captured = []
    editor.connector.requestBrowseMaterials.connect(captured.append)
    editor.toggle_properties_widget(1)
    row = editor.map_surface_index_to_ui_row(1) + 1
    editor.tableWidget.setCurrentCell(row, editor.connector.COL_MATERIAL)
    QTest.keyClick(editor.tableWidget, Qt.Key_M, Qt.ControlModifier)
    assert not captured


def test_geometry_change_drops_incompatible_shape_draft_authority(editor):
    connector = editor.connector
    editor.toggle_properties_widget(1)
    original = panel(editor, 1)
    original.input_widgets["Conic X"].setText("-0.5")
    connector.set_surface_type(1, "toroidal")
    properties = panel(editor, 1)
    assert "Conic X" not in properties.input_widgets
    assert not properties.capture_shape_draft()["changes"]
    assert properties.capture_shape_draft()["edit_token"] is None
    assert properties.capture_shape_draft()["surface"] is None
    rotation = properties.input_widgets["Radius of Rotation (XZ)"]
    rotation.setText("125")
    assert (
        properties.capture_shape_draft()["edit_token"]
        == connector.document_state.edit_token
    )
    properties.apply_changes()
    assert not properties.shape_status.text()
    assert (
        float(connector.get_surface_geometry_params(1)["Radius of Rotation (XZ)"])
        == 125
    )
