"""Native Browse/Compare/preset/export journeys across detached worker results."""

from __future__ import annotations

import importlib
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog

from optiland.materials.properties import MaterialKey, MaterialQuery, SourceStamp
from optiland_gui.materials_panel import MaterialsPanel
from optiland_gui.services.file_artifacts import CsvArtifact, RgbaArtifact
from optiland_gui.services.job_records import JobRequest, JobResult
from optiland_gui.services.material_presets import MaterialViewPreset
from optiland_gui.widgets.material_compare_panel import ComparisonPin
from tests.gui.test_material_browser import make_connector
from tests.gui.test_materials_panel import install, material_record


class ManualQueue(QObject):
    """Hold the shared worker boundary while testing native presentation."""

    finished = Signal(object)
    progress = Signal(object, dict)
    state_changed = Signal()
    stopped = Signal()

    def __init__(self, document):
        super().__init__()
        self.document = document
        self.requests = []
        self.generations = {}

    def cancel_target(self, target):
        self.generations[target] = self.generations.get(target, 0) + 1

    def is_current(self, request):
        return (
            request.document == self.document.token
            and request.generation == self.generations.get(request.target, 0)
        )

    def submit(
        self,
        target,
        handler,
        snapshot,
        parameters,
        *,
        context,
        cancel_on_document_change,
    ):
        self.cancel_target(target)
        request = JobRequest(
            len(self.requests) + 1,
            self.document.token,
            target,
            self.generations[target],
            handler,
            snapshot,
            parameters,
            cancel_on_document_change,
            context,
        )
        self.requests.append(request)
        return request

    def complete(self, request=None):
        request = request or self.requests[-1]
        module, name = request.handler.split(":")
        handler = getattr(importlib.import_module(module), name)
        result = handler(
            None, request.parameters, lambda *args: None, threading.Event()
        )
        self.finished.emit(
            JobResult(request, "succeeded", result, current=self.is_current(request))
        )
        return result


@pytest.fixture
def workspace(qapp, tmp_path):
    connector = make_connector()
    connector.calculation_jobs = ManualQueue(connector.document_state)
    records = (
        material_record("catalog:a", "a"),
        material_record("catalog:b", "b", base=1.6),
    )
    for record in records:
        install(connector.materials, record.key.source_id, (record,))
    settings = QSettings(str(tmp_path / "views.ini"), QSettings.Format.IniFormat)
    widget = MaterialsPanel(connector, settings=settings, autoload=False)
    widget.resize(1280, 900)
    widget.show()
    qapp.processEvents()
    widget.find_matches()
    connector.materials.complete()
    yield widget, connector, records
    widget.export_dialog.close()
    widget.computations.close()
    widget.sources.close()
    widget.hide()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def switch_view(widget, connector, view):
    widget.view_combo.setCurrentIndex(widget.view_combo.findData(view))
    return connector.calculation_jobs.complete()


@pytest.mark.parametrize(
    "view", ["nd-dispersion", "partial-fd", "partial-gf", "partial-dc"]
)
def test_alternate_view_links_context_hidden_selection_and_patent_overlay(
    workspace, view
):
    widget, connector, (a, b) = workspace
    design = material_record("design:context", "s1", base=1.7)
    install(connector.materials, design.key.source_id, (design,))
    widget.select_key(b.key)
    widget.patent.setChecked(True)
    widget.find_matches()
    connector.materials.complete()
    assert widget.map._target_box.get_visible()
    widget.view_combo.setCurrentIndex(widget.view_combo.findData(view))
    assert not widget.map._target_box.get_visible()
    assert not widget.map._points and not widget.map._context
    assert widget.selected_key == b.key and not widget.locate_button.isEnabled()
    result = connector.calculation_jobs.complete()
    by_key = {c.key: c for c in result.coordinates}
    assert widget.map._selected_point.x == by_key[b.key].x.value
    assert widget.map._selected_point.y == by_key[b.key].y.value
    assert widget.map._context[0].x == by_key[design.key].x.value
    assert widget.map._context[0].y == by_key[design.key].y.value
    assert result.xlabel in widget.map.ax.get_xlabel()
    assert widget.locate_button.isEnabled()
    widget.view_combo.setCurrentIndex(widget.view_combo.findData("nd-vd"))
    assert widget.map._target_box.get_visible()
    assert widget.map._selected_point.x == b.vd.value
    assert widget.selected_key == b.key


def test_filter_reuses_coordinates_but_source_change_revokes_them(workspace):
    widget, connector, (a, b) = workspace
    result = switch_view(widget, connector, "partial-gf")
    count = len(connector.calculation_jobs.requests)
    widget.name_search.setText("a missing name")
    widget.find_matches()
    connector.materials.complete()
    assert widget._view_result is result and not widget.map._points
    assert len(connector.calculation_jobs.requests) == count
    changed = replace(a, n_g=replace(a.n_g, value=a.n_g.value + 0.01))
    install(connector.materials, a.key.source_id, (changed,))
    old = connector.materials.accepted[a.key.source_id]
    connector.materials.accepted[a.key.source_id] = replace(
        old,
        snapshot=replace(old.snapshot, stamps=(SourceStamp(a.key.source_id, "new"),)),
    )
    connector.materials.source_changed.emit(a.key.source_id)
    assert widget._view_result is None and not widget.map._points
    fresh = connector.calculation_jobs.complete()
    assert fresh.stamps != result.stamps


def test_name_query_preserves_map_frame_until_worker_returns(workspace, qapp):
    widget, connector, records = workspace
    qapp.processEvents()
    widget.map.canvas.draw()
    frames = []
    callback = widget.map.canvas.mpl_connect(
        "draw_event", lambda event: frames.append(event)
    )
    try:
        widget.name_search.setText("another name")
        widget.find_matches()
        qapp.processEvents()
        assert not frames
        connector.materials.complete()
        qapp.processEvents()
        assert frames
    finally:
        widget.map.canvas.mpl_disconnect(callback)


def test_pin_and_spectral_selection_never_retarget_browse_or_apply(workspace, qapp):
    widget, connector, (a, b) = workspace
    widget.select_key(a.key)
    QTest.mouseClick(widget.pin_button, Qt.MouseButton.LeftButton)
    widget.select_key(b.key)
    QTest.mouseClick(widget.pin_button, Qt.MouseButton.LeftButton)
    assert widget.selected_key == b.key and widget.compare.pin_keys == (a.key, b.key)
    widget.tabs.setCurrentWidget(widget.compare)
    widget.compare.recompute()
    result = connector.calculation_jobs.complete()
    assert widget.compare.result is result
    assert widget.compare.model.result is result
    widget.compare.pin_table.setCurrentCell(0, 0)
    assert widget.selected_key == b.key and connector.material_apply.target is None
    widget.compare.selection_requested.emit(a.key)
    assert widget.tabs.currentWidget() is widget.browse and widget.selected_key == a.key
    assert connector.material_apply.target is None
    widget.computations.cancel("spectral")
    assert widget.compare.result is None and not widget.compare.csv_button.isEnabled()


def test_presets_keep_missing_catalog_and_pin_without_broadening(workspace, qapp):
    widget, connector, (a, b) = workspace
    missing = MaterialKey("catalog:missing", "Hidden", "same-name")
    preset = MaterialViewPreset(
        "partial-fd",
        MaterialQuery(catalog_keys=((missing.source_id, missing.catalog),), name="Dup"),
        ((missing, "Retained label", "Local catalog"),),
        (80, 10, 0.1, 0.9),
        False,
    )
    widget.presets.save("Missing", preset)
    widget.preset_name.setEditText("Missing")
    widget.load_view()
    assert widget.make_query().catalog_keys == ()
    assert widget.compare.pin_keys == (missing,)
    assert "unavailable" in widget.preset_notice.text()
    assert missing.source_id in widget.preset_notice.text()
    connector.calculation_jobs.complete()
    assert widget.map.view_bounds() == (80, 10, 0.1, 0.9)
    widget.preset_name.setEditText("Saved again")
    widget.save_view()
    assert widget.presets.load("Saved again").query.catalog_keys == (
        (missing.source_id, missing.catalog),
    )
    record = material_record(
        missing.source_id, missing.record_id, catalog=missing.catalog
    )
    install(connector.materials, missing.source_id, (record,))
    connector.materials.source_changed.emit(missing.source_id)
    assert widget.make_query().catalog_keys == ((missing.source_id, missing.catalog),)
    assert widget.preset_notice.isHidden()
    assert widget.compare.pin_keys == (missing,)
    widget._set_all_catalogs(False)
    assert widget.make_query().catalog_keys == () and widget._preset_catalogs is None


def test_preset_native_controls_round_trip_and_invalid_load_is_atomic(workspace):
    widget, connector, (a, b) = workspace
    widget.name_search.setText("Duplicate")
    widget.patent.setChecked(True)
    widget.target_nd.setValue(1.62)
    widget.target_vd.setValue(22.5)
    widget.tolerance_nd.setValue(0.2)
    widget.tolerance_vd.setValue(50)
    widget.support.setChecked(True)
    widget.wave_min.setValue(0.48)
    widget.wave_max.setValue(0.68)
    widget.select_key(a.key)
    widget._pin_selected()
    widget.map.set_view_bounds(10, 90, 1.3, 1.9)
    widget.preset_name.setEditText("Visible glasses")
    widget.save_view()
    saved = widget.presets.load("Visible glasses")
    widget.name_search.setText("changed")
    widget.compare.set_pins(())
    widget.load_view()
    assert widget.name_search.text() == "Duplicate"
    assert widget.compare.pin_keys == (a.key,)
    assert widget.map.view_bounds() == saved.bounds
    assert widget.make_query() == saved.query
    invalid = replace(
        saved,
        query=MaterialQuery(
            catalog_keys=(), target_nd=100, nd_tolerance=1, target_vd=50, vd_tolerance=1
        ),
    )
    widget.presets.save("Invalid", invalid)
    widget.preset_name.setEditText("Invalid")
    old = widget.make_query()
    widget.load_view()
    assert widget.make_query() == old
    assert "exceed" in widget.status.text()
    widget.remove_view()
    assert widget.presets.names() == ("Visible glasses",)


def test_export_captures_old_accepted_view_before_async_chooser_changes(
    workspace, monkeypatch, tmp_path
):
    widget, connector, (a, b) = workspace
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    widget._export_browse("csv")
    chooser = widget.export_dialog._dialog
    artifact = widget.export_dialog._artifact
    assert isinstance(artifact, CsvArtifact)
    assert [row[2] for row in artifact.rows] == [a.key.record_id, b.key.record_id]
    assert artifact.rows[0][6] == a.nd.value
    provenance = json.loads(artifact.provenance[0][1])
    assert provenance["sources"][0]["generation"] == "generation-1"
    path = str(tmp_path / "Unicode glass.csv")
    chooser.selectFile(path)
    widget.name_search.setText("new criteria")
    widget.export_dialog._chosen(QDialog.DialogCode.Accepted)
    saved_path, saved_artifact = connector.file_operations.requests[0]
    assert Path(saved_path) == Path(path) and saved_artifact is artifact
    assert not widget._can_export_browse()
    widget._export_browse("csv")
    assert widget.export_dialog._dialog is None
    assert "Refresh" in widget.status.text()


def test_png_routes_through_writer_and_includes_actual_view_and_selection(
    workspace, monkeypatch
):
    widget, connector, (a, b) = workspace
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    design = material_record("design:context", "s1", base=1.7)
    install(connector.materials, design.key.source_id, (design,))
    widget._refresh_context()
    widget.select_key(a.key)
    widget.map.set_view_bounds(1, 90, 1.4, 2)
    action = next(a for a in widget.map.toolbar.actions() if a.text() == "Export PNG…")
    action.trigger()
    artifact = widget.export_dialog._artifact
    assert isinstance(artifact, RgbaArtifact)
    assert len(artifact.pixels) == artifact.width * artifact.height * 4
    provenance = json.loads(dict(artifact.metadata)["Optiland"])
    assert provenance["plot_bounds"] == [90, 1, 1.4, 2]
    assert provenance["selection"]["record_id"] == a.key.record_id
    assert provenance["context"][0]["source_id"] == design.key.source_id
    assert sum(group["count"] for group in provenance["native_condition_groups"]) == 3
    assert all(item[3] != "save_figure" for item in widget.map.toolbar.toolitems)
    widget.export_dialog.close()
    widget.export_dialog._chosen(QDialog.DialogCode.Accepted)
    assert not connector.file_operations.requests


def test_comparison_export_retains_sample_gaps_and_refuses_stale_result(
    workspace, monkeypatch
):
    widget, connector, (a, b) = workspace
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    widget.compare.set_pins((ComparisonPin(a.key, a.display_name),))
    widget.compare.recompute()
    result = connector.calculation_jobs.complete()
    widget._export_comparison("csv")
    artifact = widget.export_dialog._artifact
    assert isinstance(artifact, CsvArtifact)
    assert len(artifact.rows) == len(result.wavelengths_um)
    assert artifact.rows[0][7] == result.wavelengths_um[0]
    assert artifact.rows[0][9] == result.series[0].values[0].value
    widget.export_dialog._chosen(QDialog.DialogCode.Rejected)
    widget.computations.cancel("spectral")
    widget._export_comparison("png")
    assert widget.export_dialog._dialog is None
    assert "Recompute" in widget.compare.status.text()


def test_ctrl_f_from_compare_returns_to_browse(workspace, qapp):
    widget, connector, records = workspace
    widget.tabs.setCurrentWidget(widget.compare)
    widget.compare.pin_table.setFocus()
    QTest.keyClick(
        widget.compare.pin_table, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier
    )
    qapp.processEvents()
    assert (
        widget.tabs.currentWidget() is widget.browse and widget.name_search.hasFocus()
    )


def test_compact_full_panel_accepts_long_local_path_and_saved_name(workspace, qapp):
    widget, connector, (a, b) = workspace
    path = (
        r"GLC · C:\Users\Bruno\Documents\projects\embedded_knight\projecten"
        r"\optiland\reports\glassmap_gui\worktree\docs\samples\glassmap\tutorial.glc"
    )
    widget.sources.labels[a.key.source_id] = path
    widget.select_key(a.key)
    widget._pin_selected()
    widget.preset_name.setEditText("Long view name " * 7)
    widget.save_view()
    widget.tabs.setCurrentWidget(widget.compare)
    qapp.processEvents()
    widget.resize(680, 760)
    qapp.processEvents()
    assert widget.width() == 680 and widget.height() == 760
    assert path in widget.selection_summary.text()
    assert path in widget.inspector.toPlainText()
    assert widget.preset_name.currentText() in widget.preset_name.toolTip()


def test_unrelated_edit_preserves_pending_catalog_view_and_spectrum(workspace):
    widget, connector, (a, b) = workspace
    widget.view_combo.setCurrentIndex(widget.view_combo.findData("partial-gf"))
    map_request = connector.calculation_jobs.requests[-1]
    widget.compare.set_pins((ComparisonPin(a.key, a.display_name),))
    widget.compare.recompute()
    spectral_request = connector.calculation_jobs.requests[-1]
    count = len(connector.calculation_jobs.requests)
    connector.document_state.change()
    assert len(connector.calculation_jobs.requests) == count
    connector.calculation_jobs.complete(map_request)
    connector.calculation_jobs.complete(spectral_request)
    assert widget._view_result is not None and widget.compare.result is not None


def test_backend_change_recomputes_map_and_comparison(workspace, monkeypatch):
    pytest.importorskip("torch")
    from optiland_gui.services.job_records import BackendConfig

    widget, connector, (a, b) = workspace
    original = BackendConfig.capture()
    switch_view(widget, connector, "partial-gf")
    widget.compare.set_pins((ComparisonPin(a.key, a.display_name),))
    widget.compare.recompute()
    connector.calculation_jobs.complete()
    old_view, old_spectral = widget._view_result, widget.compare.result
    count = len(connector.calculation_jobs.requests)
    try:
        BackendConfig("torch").apply()
        connector.document_state.change()
        assert len(connector.calculation_jobs.requests) == count + 2
        for request in connector.calculation_jobs.requests[count:]:
            connector.calculation_jobs.complete(request)
        assert widget._view_result is not old_view
        assert widget.compare.result is not old_spectral
        assert widget.compare.result.request == old_spectral.request
    finally:
        original.apply()


def test_alternate_map_bounds_survive_hide_show_and_source_reload(workspace, qapp):
    widget, connector, (a, b) = workspace
    switch_view(widget, connector, "partial-fd")
    widget.map.set_view_bounds(20, 80, 0.2, 0.8)
    expected = widget.map.view_bounds()
    widget.set_dock_visible(False)
    widget.set_dock_visible(True)
    qapp.processEvents()
    connector.calculation_jobs.complete()
    assert widget.map.view_bounds() == expected
    widget.hide()
    widget.show()
    qapp.processEvents()
    connector.calculation_jobs.complete()
    assert widget.map.view_bounds() == expected
    old = connector.materials.accepted[a.key.source_id]
    # Reloading an unchanged catalog preserves the view just as a new generation
    # does, even though the comparison owner need not invalidate its data.
    connector.materials.source_changed.emit(a.key.source_id)
    connector.calculation_jobs.complete()
    assert widget.map.view_bounds() == expected
    connector.materials.accepted[a.key.source_id] = replace(
        old,
        snapshot=replace(
            old.snapshot, stamps=(SourceStamp(a.key.source_id, "reload"),)
        ),
    )
    connector.materials.source_changed.emit(a.key.source_id)
    connector.calculation_jobs.complete()
    assert widget.map.view_bounds() == expected


def test_csv_matches_visible_numeric_sort_and_patent_metrics(workspace, monkeypatch):
    widget, connector, (a, b) = workspace
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    widget.patent.setChecked(True)
    widget.target_nd.setValue(1.65)
    widget.tolerance_nd.setValue(0.2)
    widget.target_vd.setValue(20)
    widget.tolerance_vd.setValue(50)
    widget.find_matches()
    connector.materials.complete()
    widget.table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
    widget._export_browse("csv")
    artifact = widget.export_dialog._artifact
    assert [row[2] for row in artifact.rows] == ["b", "a"]
    matches = {match.key.record_id: match for match in widget._accepted_result.matches}
    columns = dict(zip(artifact.columns, range(len(artifact.columns)), strict=True))
    for row in artifact.rows:
        assert row[columns["delta_nd"]] == matches[row[2]].delta_nd
        assert row[columns["delta_vd"]] == matches[row[2]].delta_vd
        assert row[columns["match_distance"]] == matches[row[2]].distance
    assert json.loads(artifact.provenance[0][1])["sort"]["order"] == "descending"


def test_real_workers_compare_export_and_preserve_the_lens(qapp, tmp_path, monkeypatch):
    import csv

    from PIL import Image

    from optiland.materials.sources import OwnedEntry, OwnedSource
    from optiland_gui.optiland_connector import OptilandConnector
    from tests.gui.test_artifact_operations import document_state
    from tests.gui.test_calculation_jobs import wait_for

    connector = OptilandConnector()
    widget = MaterialsPanel(connector, autoload=False)
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    record_a = material_record("local:comparison", "a")
    record_b = material_record("local:comparison", "b", base=1.6)
    try:
        widget.resize(1180, 850)
        widget.show()
        connector.materials.load_source(
            OwnedSource(
                "local:comparison",
                tuple(
                    OwnedEntry(
                        record.key.record_id, record.display_name, record.owned_data
                    )
                    for record in (record_a, record_b)
                ),
            )
        )
        wait_for(
            qapp,
            lambda: "local:comparison" in connector.materials.source_ids,
            timeout=30,
        )
        widget.find_matches()
        wait_for(qapp, lambda: widget.model.rowCount() == 2, timeout=30)
        before = document_state(connector)
        for row in range(2):
            widget.select_key(widget.model.key_at(row))
            QTest.mouseClick(widget.pin_button, Qt.MouseButton.LeftButton)
        widget.view_combo.setCurrentIndex(widget.view_combo.findData("partial-gf"))
        wait_for(qapp, lambda: widget._view_result is not None, timeout=30)
        widget.map.set_view_bounds(5, 40, 0.2, 1.1)
        widget._export_browse("csv")
        dialog = widget.export_dialog._dialog
        dialog.selectFile(str(tmp_path / "map.csv"))
        widget.export_dialog._chosen(QDialog.DialogCode.Accepted)
        wait_for(qapp, lambda: not connector.file_operations.busy, timeout=30)
        with (tmp_path / "map.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert {row["record_id"] for row in rows} == {"a", "b"}
        assert json.loads(rows[0]["provenance:material_view"])["view"] == "partial-gf"
        assert (
            float(rows[0]["plot_y"])
            == widget._coordinates[widget._records[0].key].y.value
        )
        widget.tabs.setCurrentWidget(widget.compare)
        widget.compare.recompute()
        wait_for(qapp, lambda: widget.compare.result is not None, timeout=30)
        result = widget.compare.result
        for extension in ("csv", "png"):
            widget._export_comparison(extension)
            dialog = widget.export_dialog._dialog
            assert dialog is not None
            dialog.selectFile(str(tmp_path / f"comparison.{extension}"))
            widget.export_dialog._chosen(QDialog.DialogCode.Accepted)
            wait_for(qapp, lambda: not connector.file_operations.busy, timeout=30)
        with (tmp_path / "comparison.csv").open(encoding="utf-8", newline="") as stream:
            samples = list(csv.DictReader(stream))
        assert len(samples) == 2 * len(result.wavelengths_um)
        assert float(samples[0]["value"]) == result.series[0].values[0].value
        with Image.open(tmp_path / "comparison.png") as image:
            metadata = json.loads(image.info["Optiland"])
            assert metadata["request"]["keys"][0]["record_id"] == "a"
            assert metadata["materials"][0]["conditions"]["temperature_c"] == 20.0
        assert document_state(connector) == before
    finally:
        widget.export_dialog.close()
        widget.hide()
        connector._backend_timer.stop()
        connector.calculation_jobs.shutdown()
        wait_for(qapp, lambda: connector.calculation_jobs._process is None, timeout=30)
        widget.deleteLater()
        connector.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_nearby_inspection_and_lost_source_are_explicit(workspace):
    widget, connector, (a, b) = workspace
    widget.select_key(a.key)
    widget._pin_selected()
    widget.tabs.setCurrentWidget(widget.compare)
    widget.compare.find_nearby()
    result = connector.calculation_jobs.complete()
    assert widget.compare.nearby_model.rowCount() == 1
    assert result.matches[0].key == b.key
    widget.compare.nearby_table.setCurrentIndex(widget.compare.nearby_proxy.index(0, 0))
    assert widget.selected_key == a.key
    widget.compare.inspect_nearby()
    assert widget.selected_key == b.key and widget.tabs.currentWidget() is widget.browse
    connector.materials.remove_source(b.key.source_id)
    assert widget.compare.nearby_model.rowCount() == 0
    widget.compare.find_nearby()
    request = connector.calculation_jobs.requests[-1]
    connector.calculation_jobs.finished.emit(
        JobResult(request, "failed", error="Proximity worker stopped")
    )
    assert "stopped" in widget.compare.nearby_status.text()


@pytest.mark.parametrize("kind", ["view", "spectral"])
def test_worker_failure_is_visible_and_not_exportable(workspace, kind):
    widget, connector, (a, b) = workspace
    if kind == "view":
        widget.view_combo.setCurrentIndex(widget.view_combo.findData("partial-fd"))
    else:
        widget.compare.set_pins((ComparisonPin(a.key, a.display_name),))
        widget.compare.recompute()
    request = connector.calculation_jobs.requests[-1]
    connector.calculation_jobs.finished.emit(
        JobResult(request, "failed", error="Captured data unavailable")
    )
    assert "unavailable" in (
        widget.status.text() if kind == "view" else widget.compare.status.text()
    )
    if kind == "view":
        assert not widget._can_export_browse()
    else:
        assert widget.compare.result is None


def test_native_export_dialog_reentry_rejection_and_shutdown(workspace, monkeypatch):
    widget, connector, records = workspace
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    widget._export_browse("csv")
    dialog = widget.export_dialog._dialog
    artifact = widget.export_dialog._artifact
    with pytest.raises(ValueError, match="Unsupported"):
        widget.export_dialog.choose(artifact, "invalid", "ignored")
    widget.export_dialog.choose(RgbaArtifact(1, 1, 4, bytes(4)), "png", "another")
    assert widget.export_dialog._dialog is dialog
    assert widget.export_dialog._artifact is artifact
    widget._export_browse("csv")
    assert widget.export_dialog._dialog is dialog
    monkeypatch.setattr(
        connector.file_operations, "request_artifact", lambda *args: None
    )
    widget.tabs.setCurrentWidget(widget.compare)
    widget.export_dialog._chosen(QDialog.DialogCode.Accepted)
    assert "not started" in widget.compare.status.text()
    widget.export_dialog.close()
    widget.export_dialog.choose(artifact, "csv", "closed")
    assert not widget.export_dialog.choosing


def test_invalid_numeric_draft_prevents_export_without_erasing_prior_rows(workspace):
    widget, connector, records = workspace
    widget.patent.setChecked(True)
    widget.target_nd.lineEdit().setText("-")
    assert not widget._can_export_browse()
    widget._export_browse("csv")
    assert widget.model.rowCount() == 2
    assert "Refresh" in widget.status.text()


def test_malformed_saved_view_does_not_change_controls(workspace):
    widget, connector, records = workspace
    before = widget.make_query()
    widget.settings.setValue(widget.presets.KEY, "{not json}")
    widget._refresh_preset_names()
    assert "not valid JSON" in widget.status.text()
    widget.load_view()
    assert widget.make_query() == before
    widget.save_view()
    widget.remove_view()
    assert widget.make_query() == before


def test_failed_artifact_capture_reports_limits_in_active_view(workspace, monkeypatch):
    import optiland_gui.materials_panel as module

    widget, connector, (a, b) = workspace

    def oversized(*args):
        raise ValueError("Captured image exceeds the byte limit")

    monkeypatch.setattr(module, "plot_png", oversized)
    widget._export_browse("png")
    assert "byte limit" in widget.status.text() and not widget.export_dialog.choosing
    widget.compare.set_pins((ComparisonPin(a.key, a.display_name),))
    widget.compare.recompute()
    result = connector.calculation_jobs.complete()
    widget._export_comparison("png")
    assert "byte limit" in widget.compare.status.text()
    assert widget.compare.result is result
    assert not widget.export_dialog.choosing
