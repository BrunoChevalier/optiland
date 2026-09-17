"""Large LDE installation yields, preserves identities and abandons obsolete rows."""

from __future__ import annotations

from unittest.mock import PropertyMock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, QTimer

import optiland.backend as be
from optiland.optic import Optic
from optiland.solves import MarginalRayHeightThicknessSolve
from optiland_gui.lens_editor import LensEditor, SurfacePropertiesWidget
from optiland_gui.optiland_connector import OptilandConnector
from tests.gui.test_calculation_jobs import wait_for
from tests.test_folded_paraxial import folded, straight


def make_large_connector(count=100):
    optic = Optic()
    optic.set_aperture("EPD", 2)
    optic.fields.set_type("angle")
    optic.fields.add(y=0)
    optic.wavelengths.add(0.55, is_primary=True)
    for index in range(count):
        optic.surfaces.add(
            index=index,
            radius=float("inf"),
            thickness=5,
            material="air",
            is_stop=index == 1,
            comment=f"Surface {index}",
        )
    connector = OptilandConnector()
    connector._optic = optic
    connector.notify_change("replacement")
    return connector


def test_display_capture_computes_positions_once_and_matches_public_cells(
    qapp, monkeypatch
):
    c = make_large_connector()
    group = type(c.get_optic().surfaces)
    getter = group.positions.fget
    calls = PropertyMock(side_effect=lambda: getter(c.get_optic().surfaces))
    monkeypatch.setattr(group, "positions", calls)
    rows = c.get_surface_display_rows()
    assert calls.call_count == 1
    for row in (0, 1, 50, 99):
        for column in range(7):
            assert rows[row][column] == c.get_surface_data(row, column)
    c.deleteLater()


@pytest.mark.parametrize("backend", ["numpy", "torch"])
def test_folded_spacing_and_solve_changes_match_public_cells(qapp, backend):
    if backend == "torch":
        pytest.importorskip("torch")
    be.set_backend(backend)
    if backend == "torch":
        be.set_device("cpu")
        be.set_precision("float64")
    c = OptilandConnector()
    try:
        c._optic = folded()
        c.notify_change("replacement")
        rows = c.get_surface_display_rows()
        assert rows[3][c.COL_THICKNESS] == "-26.0000"
        for row, cells in rows.items():
            for column in range(7):
                assert cells[column] == c.get_surface_data(row, column)
        c._optic = straight()
        c.notify_change("replacement")
        c._optic.solves.solves.append(MarginalRayHeightThicknessSolve(c._optic, 3, 0))
        c._optic.updater.update()
        before = c.get_surface_display_rows()[2][c.COL_THICKNESS]
        c.set_surface_data(1, c.COL_RADIUS, "35.0")
        after = c.get_surface_display_rows()[2][c.COL_THICKNESS]
        assert before != after
        assert after == c.get_surface_data(2, c.COL_THICKNESS)
        c.undo()
        assert c.get_surface_display_rows()[2][c.COL_THICKNESS] == before
    finally:
        c.deleteLater()
        be.set_backend("numpy")


def test_large_table_yields_and_supersedes_partial_refresh(qapp, monkeypatch):
    c = make_large_connector()
    monkeypatch.setattr(LensEditor, "_BATCH_SECONDS", 0)
    editor = LensEditor(c)
    completed = []
    editor.loadingFinished.connect(lambda: completed.append(True))
    assert editor._table_loading and not editor.tableWidget.isEnabled()
    turns = []
    QTimer.singleShot(0, lambda: turns.append(editor._next_load_row))
    wait_for(qapp, lambda: bool(turns))
    assert turns[0] < 100
    c.get_optic().surfaces[20].comment = "Changed while loading"
    c.notify_change("metadata", surface_indices=(20,), columns=(c.COL_COMMENT,))
    wait_for(qapp, lambda: not editor._table_loading)
    assert completed == [True]
    assert editor.tableWidget.item(20, c.COL_COMMENT).text() == "Changed while loading"
    assert editor.tableWidget.isEnabled()
    assert not editor.hover_tracker._suspended
    assert editor.tableWidget.cellWidget(1, 0).surface_menu.actions() == []
    editor.tableWidget.cellWidget(1, 0)._populate_surface_menu()
    menu = editor.tableWidget.cellWidget(1, 0)
    actions = menu.surface_menu.actions()
    assert actions
    menu._populate_surface_menu()
    assert menu.surface_menu.actions() == actions
    editor.deleteLater()
    c.deleteLater()


def test_requested_display_columns_skip_missing_rows_without_computing_positions(
    qapp, monkeypatch
):
    c = make_large_connector()
    monkeypatch.setattr(
        type(c.get_optic().surfaces),
        "positions",
        PropertyMock(side_effect=AssertionError("Unrequested positions computed")),
    )
    assert c.get_surface_display_rows((-1, 2, 999), (c.COL_COMMENT,)) == {
        2: {c.COL_COMMENT: "Surface 2"}
    }
    c.deleteLater()


def test_pending_flash_does_not_modify_a_replacement_item(qapp):
    from PySide6.QtTest import QTest

    c = make_large_connector(4)
    editor = LensEditor(c)
    editor._flash_cell(2, c.COL_COMMENT, True, duration_ms=1)
    editor.load_data()
    replacement = editor.tableWidget.item(2, c.COL_COMMENT)
    background = replacement.background()
    QTest.qWait(20)
    assert replacement.background() == background
    # An already queued timer may arrive after a synchronous completion.
    editor._load_table_batch()
    assert not editor._table_loading and editor.tableWidget.isEnabled()
    editor.deleteLater()
    c.deleteLater()


@pytest.mark.parametrize("failure", ["capture", "properties", "selection", "empty"])
def test_failed_restoration_releases_controls_and_retains_panel_owners_for_retry(
    qapp, monkeypatch, failure
):
    c = make_large_connector()
    editor = LensEditor(c)
    wait_for(qapp, lambda: not editor._table_loading)
    editor.toggle_properties_widget(20)
    editor.toggle_properties_widget(50)
    selected = c.get_optic().surfaces[50]
    editor.tableWidget.setCurrentCell(
        editor.map_surface_index_to_ui_row(50), c.COL_RADIUS
    )
    errors = []
    editor.loadingFailed.connect(errors.append)
    target, name = {
        "capture": (c, "get_surface_display_rows"),
        "properties": (editor, "_insert_properties_widget"),
        "selection": (editor, "_restore_table_state"),
        "empty": (editor, "_insert_properties_widget"),
    }[failure]

    def fail(*args, **kwargs):
        if failure == "empty":
            raise ValueError
        raise ValueError("Unable to rebuild the editor")

    with monkeypatch.context() as scope:
        scope.setattr(target, name, fail)
        editor.load_data()
        wait_for(qapp, lambda: not editor._table_loading)
    assert errors == [
        "ValueError" if failure == "empty" else "Unable to rebuild the editor"
    ]
    assert editor.tableWidget.isEnabled()
    assert not editor.tableWidget.signalsBlocked()
    assert editor.btnAddSurface.isEnabled() and editor.btnRemoveSurface.isEnabled()
    assert not editor.hover_tracker._suspended
    assert not editor._load_timer.isActive()

    editor.load_data()
    wait_for(qapp, lambda: not editor._table_loading)
    assert editor.open_prop_source_rows == {20, 50}
    assert editor.interaction_state.selected_surfaces == (selected,)
    assert editor.tableWidget.currentRow() == editor.map_surface_index_to_ui_row(50)
    for owner in (20, 50):
        assert isinstance(
            editor.tableWidget.cellWidget(
                editor.map_surface_index_to_ui_row(owner) + 1, 0
            ),
            SurfacePropertiesWidget,
        )
    assert editor._load_progress.isHidden()
    editor.deleteLater()
    c.deleteLater()


def test_batched_structural_refresh_restores_surviving_surface_selection(qapp):
    c = make_large_connector()
    editor = LensEditor(c)
    wait_for(qapp, lambda: not editor._table_loading)
    selected = c.get_optic().surfaces[50]
    editor.tableWidget.setCurrentCell(50, c.COL_RADIUS)
    c.add_surface(index=20)
    assert editor._table_loading
    wait_for(qapp, lambda: not editor._table_loading)
    assert editor.interaction_state.selected_surfaces == (selected,)
    assert editor.tableWidget.currentRow() == 51
    assert editor.tableWidget.currentColumn() == c.COL_RADIUS
    editor.deleteLater()
    c.deleteLater()


def test_batched_refresh_preserves_multiple_panels_and_closes_shifted_owner(
    qapp, monkeypatch
):
    from PySide6.QtWidgets import QWidget

    original = SurfacePropertiesWidget._populate_properties_form

    def two_tabs(panel):
        original(panel)
        panel.tabs.addTab(QWidget(), "Extra properties")

    monkeypatch.setattr(SurfacePropertiesWidget, "_populate_properties_form", two_tabs)
    monkeypatch.setattr(LensEditor, "_BATCH_SECONDS", 0)
    c = make_large_connector()
    editor = LensEditor(c)
    wait_for(qapp, lambda: not editor._table_loading)
    for owner in (20, 50):
        editor.toggle_properties_widget(owner)
    editor.tableWidget.cellWidget(
        editor.map_surface_index_to_ui_row(50) + 1, 0
    ).tabs.setCurrentIndex(1)
    selected = c.get_optic().surfaces[50]
    editor.tableWidget.setCurrentCell(
        editor.map_surface_index_to_ui_row(50), c.COL_RADIUS
    )
    c.add_surface(index=10)
    wait_for(qapp, lambda: not editor._table_loading)
    assert editor.open_prop_source_rows == {21, 51}
    assert editor.interaction_state.selected_surfaces == (selected,)
    assert editor.tableWidget.currentRow() == editor.map_surface_index_to_ui_row(51)
    first = editor.tableWidget.cellWidget(editor.map_surface_index_to_ui_row(21) + 1, 0)
    second = editor.tableWidget.cellWidget(
        editor.map_surface_index_to_ui_row(51) + 1, 0
    )
    assert first.tabs.currentIndex() == 0
    assert second.tabs.currentIndex() == 1
    second.close_button.click()
    assert editor.open_prop_source_rows == {21}
    assert (
        editor.tableWidget.cellWidget(editor.map_surface_index_to_ui_row(21) + 1, 0)
        is first
    )
    editor.deleteLater()
    c.deleteLater()


def test_failed_batch_is_recoverable_and_destruction_cancels_pending_timers(
    qapp, monkeypatch
):
    c = make_large_connector()
    editor = LensEditor(c)
    wait_for(qapp, lambda: not editor._table_loading)
    original = editor._process_table_row
    errors = []
    editor.loadingFailed.connect(errors.append)
    with monkeypatch.context() as scope:

        def fail(*args):
            raise ValueError("Invalid display data")

        scope.setattr(editor, "_process_table_row", fail)
        editor.load_data()
        wait_for(qapp, lambda: not editor._table_loading)
    assert errors == ["Invalid display data"]
    assert not editor.tableWidget.signalsBlocked()
    assert "Invalid display data" in editor._load_progress.format()
    assert editor._process_table_row == original
    editor.load_data()
    wait_for(qapp, lambda: not editor._table_loading)
    editor._flash_cell(2, c.COL_COMMENT, True, duration_ms=1)
    editor.load_data()
    assert editor._load_timer.isActive()
    editor.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()
    c.deleteLater()


def test_multiple_properties_and_drafts_survive_superseded_batches(qapp, monkeypatch):
    """Canonical independent panels retain identity during deferred row installs."""
    c = make_large_connector()
    c.set_surface_type(20, "biconic")
    c.set_surface_type(70, "biconic")
    editor = LensEditor(c)
    try:
        wait_for(qapp, lambda: not editor._table_loading)
        editor.toggle_properties_widget(20)
        editor.toggle_properties_widget(70)
        first = editor.tableWidget.cellWidget(21, 0)
        second = editor.tableWidget.cellWidget(72, 0)
        first.tabs.setCurrentIndex(1)
        first.input_widgets["Conic X"].setText("-0.375")
        first.input_widgets["Conic X"].setModified(True)
        second.placement.frame.setCurrentIndex(1)
        second.placement.inputs["x"].setText("0.75")
        draft_token = second.placement._edit_token
        identities = (c.get_optic().surfaces[20], c.get_optic().surfaces[70])
        monkeypatch.setattr(LensEditor, "_BATCH_SECONDS", 0)
        c.add_surface(index=10)
        assert editor._table_loading
        # Ignore controls while their row mapping is only partially installed.
        editor.close_properties_widget(21)
        editor.toggle_properties_widget(50)
        c.set_surface_data(5, c.COL_COMMENT, "Supersede the row batch")
        wait_for(qapp, lambda: not editor._table_loading)
        assert editor.open_prop_source_rows == {21, 71}
        first = editor.tableWidget.cellWidget(22, 0)
        second = editor.tableWidget.cellWidget(73, 0)
        assert c.get_optic().surfaces[21] is identities[0]
        assert c.get_optic().surfaces[71] is identities[1]
        assert first.tabs.currentIndex() == 1
        assert first.input_widgets["Conic X"].text() == "-0.375"
        assert first.input_widgets["Conic X"].isModified()
        assert second.placement.frame.currentData() == "reference"
        assert second.placement.inputs["x"].text() == "0.75"
        assert second.placement._edit_token == draft_token
        assert "design changed" in second.placement.status.text()
        second.placement.apply()
        assert not c.placement.busy
        assert float(c.get_optic().surfaces[71].geometry.cs.x) == 0
    finally:
        editor.deleteLater()
        c.deleteLater()


@pytest.mark.parametrize("during_batch", [False, True])
def test_prepared_mapping_preserves_panels_selection_scroll_and_stale_drafts(
    qapp, monkeypatch, during_batch
):
    from optiland_gui.services.prepared_optic import PreparedOptic

    c = make_large_connector()
    c.set_surface_type(20, "biconic")
    c.set_surface_type(70, "biconic")
    editor = LensEditor(c)
    editor.resize(1000, 700)
    editor.show()
    try:
        wait_for(qapp, lambda: not editor._table_loading)
        editor.toggle_properties_widget(20)
        editor.toggle_properties_widget(70)
        qapp.processEvents()
        shape = editor.tableWidget.cellWidget(21, 0)
        placement = editor.tableWidget.cellWidget(72, 0).placement
        shape.tabs.setCurrentIndex(1)
        shape.input_widgets["Conic X"].setText("-0.375")
        placement.frame.setCurrentIndex(1)
        placement.inputs["x"].setText("0.75")
        original = c.get_optic()
        shape_token = shape.capture_shape_draft()["edit_token"]
        placement_token = placement._edit_token
        editor.tableWidget.setCurrentCell(
            71, c.COL_MATERIAL, QItemSelectionModel.NoUpdate
        )
        editor.tableWidget.selectionModel().select(
            editor.tableWidget.model().index(71, 0),
            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
        )
        assert editor.interaction_state.selected_surfaces == (original.surfaces[70],)
        editor.tableWidget.verticalScrollBar().setValue(600)
        editor.interaction_state.set_hover(20, c.COL_MATERIAL)
        scroll = editor.tableWidget.verticalScrollBar().value()
        candidate = PreparedOptic.capture(original).restore()
        monkeypatch.setattr(LensEditor, "_BATCH_SECONDS", 0)
        if during_batch:
            editor.load_data()
            assert editor._table_loading
        c.document_acceptance.publish(
            candidate,
            owner="material",
            expected_optic=original,
            expected_edit_token=c.document_state.edit_token,
            expected_history_revision=c._undo_redo_manager.revision,
            validate=lambda: True,
            undo=c._capture_optic_state(),
            row_mapping=tuple((i, i) for i in range(100)),
            affected_rows=(20,),
        )
        wait_for(qapp, lambda: not editor._table_loading)
        assert editor.open_prop_source_rows == {20, 70}
        assert editor.interaction_state.selected_surfaces == (candidate.surfaces[70],)
        assert editor.interaction_state.hovered_surface is candidate.surfaces[20]
        assert editor.tableWidget.verticalScrollBar().value() == scroll
        shape = editor.tableWidget.cellWidget(21, 0)
        placement = editor.tableWidget.cellWidget(72, 0).placement
        assert shape.surface is candidate.surfaces[20]
        assert shape.tabs.currentIndex() == 1
        assert shape.input_widgets["Conic X"].text() == "-0.375"
        assert shape.capture_shape_draft()["edit_token"] == shape_token
        assert "design changed" in shape.shape_status.text()
        assert placement.surface is candidate.surfaces[70]
        assert placement.frame.currentData() == "reference"
        assert placement.inputs["x"].text() == "0.75"
        assert placement._edit_token == placement_token
        before = c.document_state.edit_token
        revision = c._undo_redo_manager.revision
        shape.apply_changes()
        placement.apply()
        assert c.document_state.edit_token == before
        assert c._undo_redo_manager.revision == revision
        assert float(candidate.surfaces[20].geometry.kx) == 0
        assert float(candidate.surfaces[70].geometry.cs.x) == 0
    finally:
        editor.deleteLater()
        c.calculation_jobs.shutdown()
        c.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_unknown_prepared_mapping_drops_old_editor_authority(qapp):
    from optiland_gui.services.prepared_optic import PreparedOptic

    c = make_large_connector(10)
    editor = LensEditor(c)
    try:
        editor.toggle_properties_widget(2)
        editor.tableWidget.selectRow(2)
        original = c.get_optic()
        candidate = PreparedOptic.capture(original).restore()
        c.document_acceptance.publish(
            candidate,
            owner="material",
            expected_optic=original,
            expected_edit_token=c.document_state.edit_token,
            expected_history_revision=c._undo_redo_manager.revision,
            validate=lambda: True,
            undo=c._capture_optic_state(),
        )
        assert not editor.open_prop_source_rows
        assert not editor.interaction_state.selected_surfaces
        assert editor.interaction_state.hovered_surface is None
    finally:
        editor.deleteLater()
        c.calculation_jobs.shutdown()
        c.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
