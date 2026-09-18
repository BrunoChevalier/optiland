"""Native Materials interactions retain exact identities and document ownership."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest

from optiland.materials import DataMaterial, IdealMaterial
from optiland.materials.properties import (
    MaterialKey,
    MaterialSnapshot,
    SourceStamp,
    describe_material,
)
from optiland.materials.sources import SourceDiagnostic, SourceResult
from optiland_gui.materials_panel import MaterialsPanel
from tests.gui.test_material_browser import make_connector


def material_record(source, record_id, *, base=1.5, catalog="Shared", name="Duplicate"):
    material = DataMaterial.from_coefficients(
        "formula 5",
        [base, 0.02, -2],
        name=name,
        wavelength_range=(0.4, 0.8),
        metadata={
            "rii": {
                "SPECS": {
                    "n_is_absolute": False,
                    "wavelength_is_vacuum": False,
                    "temperature": "20.0 °C",
                    "nd": 1.55,
                    "Vd": 64.2,
                },
                "REFERENCE": "<script>alert('source text')</script>",
            },
            "provenance": [{"catalog": catalog, "sha256": "fixture-hash"}],
        },
    )
    return describe_material(material, MaterialKey(source, catalog, record_id))


def install(service, source_id, records, *, diagnostics=()):
    service.accepted[source_id] = SourceResult(
        MaterialSnapshot((SourceStamp(source_id, "generation-1"),), tuple(records)),
        diagnostics,
    )


@pytest.fixture
def panel(qapp):
    connector = make_connector()
    a = material_record("catalog:a", "exact-a")
    b = material_record("catalog:b", "exact-b", base=1.6)
    missing = describe_material(
        IdealMaterial(1.8), MaterialKey("catalog:a", "Other", "constant")
    )
    install(connector.materials, "catalog:a", (a, missing))
    install(connector.materials, "catalog:b", (b,))
    widget = MaterialsPanel(connector, autoload=False)
    widget.resize(1280, 900)
    widget.show()
    qapp.processEvents()
    widget._query_timer.stop()
    widget.find_matches()
    connector.materials.complete()
    yield widget, connector, (a, b, missing)
    widget.hide()
    widget.sources.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def finish(panel):
    panel.find_matches()
    return panel.service.complete()


def test_expert_pool_entries_use_exact_selection_pins_or_current_results(panel):
    widget, connector, (a, b, missing) = panel
    widget.select_key(a.key)
    assert [r.key for r, _ in widget._expert_entries("selection")] == [a.key]
    for record in (b, a):
        widget.compare.pin(
            record,
            connector.materials.source_result(record.key.source_id).snapshot.stamps[0],
        )
    assert [r.key for r, _ in widget._expert_entries("pins")] == [b.key, a.key]
    assert {r.key for r, _ in widget._expert_entries("results")} == {
        a.key,
        b.key,
        missing.key,
    }
    widget.name_search.setText("no matches")
    with pytest.raises(ValueError, match="current Browse"):
        widget._expert_entries("results")
    finish(widget)
    assert widget._expert_entries("results") == ()
    connector.materials.accepted.pop("catalog:b")
    with pytest.raises(ValueError, match="unavailable"):
        widget._expert_entries("pins")
    with pytest.raises(ValueError, match="Choose"):
        widget._expert_entries("implicit catalog")


def test_expert_tab_has_distinct_apply_and_objective_handoff(panel):
    widget, _, _ = panel
    events = []
    widget.edit_objective_requested.connect(lambda: events.append("objective"))
    widget.tabs.setCurrentWidget(widget.expert)
    assert widget.footer.isHidden()
    QTest.mouseClick(widget.expert.objective_button, Qt.MouseButton.LeftButton)
    assert events == ["objective"]
    widget.tabs.setCurrentIndex(0)
    assert not widget.footer.isHidden()


def test_proxy_sort_and_keyboard_selection_keep_exact_same_name_identity(panel, qapp):
    widget, connector, records = panel
    original_token = connector.document_state.token
    widget.table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
    index = widget.proxy.index(1, 0)
    expected = widget.model.key_at(widget.proxy.mapToSource(index).row())
    widget.table.setCurrentIndex(index)
    assert widget.selected_key == expected
    assert expected.record_id in widget.inspector.toPlainText()
    widget.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    assert widget.selected_key == expected
    current = widget.table.currentIndex()
    assert widget.model.key_at(widget.proxy.mapToSource(current).row()) == expected
    widget.table.setFocus()
    QTest.keyClick(widget.table, Qt.Key.Key_Return)
    widget.table.doubleClicked.emit(current)
    assert connector.document_state.token == original_token
    assert widget.selected_key == expected
    widget.map.selection_requested.emit(records[0].key)
    assert widget.selected_key == records[0].key
    assert (
        widget.model.key_at(widget.proxy.mapToSource(widget.table.currentIndex()).row())
        == records[0].key
    )


def test_native_model_contract_header_sort_and_reset_preserve_selection(panel, qapp):
    import sys

    from PySide6.QtCore import QPoint, qInstallMessageHandler
    from PySide6.QtTest import QAbstractItemModelTester

    widget, _, (a, _, _) = panel
    model_failures = []

    def capture_message(kind, context, message):
        if context.category == "qt.modeltest":
            model_failures.append(message)
        elif previous is not None:
            previous(kind, context, message)
        else:
            print(message, file=sys.stderr)

    previous = qInstallMessageHandler(capture_message)
    tester = QAbstractItemModelTester(
        widget.proxy, QAbstractItemModelTester.FailureReportingMode.Warning, widget
    )
    try:
        widget.select_key(a.key)
        header = widget.table.horizontalHeader()
        position = QPoint(
            header.sectionViewportPosition(4) + header.sectionSize(4) // 2,
            header.height() // 2,
        )
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)
        assert header.sortIndicatorSection() == 4
        assert widget.selected_key == a.key
        assert widget.table.currentIndex().data(widget.model.KeyRole) == a.key
        widget.table.setCurrentIndex(widget.proxy.index(0, 0))
        expected = widget.proxy.index(1, 0).data(widget.model.KeyRole)
        QTest.keyClick(widget.table, Qt.Key.Key_Down)
        assert widget.selected_key == expected
        widget.name_search.setText("no matching record")
        finish(widget)
        assert widget.proxy.rowCount() == 0 and widget.selected_key == expected
        widget.name_search.clear()
        finish(widget)
        assert widget.table.currentIndex().data(widget.model.KeyRole) == expected
        assert not model_failures
    finally:
        qInstallMessageHandler(previous)
        tester.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_inspector_tab_and_reverse_tab_leave_read_only_text(panel, qapp):
    widget, _, (a, _, _) = panel
    widget.select_key(a.key)
    original = widget.inspector.toPlainText()
    widget.inspector.setFocus()
    QTest.keyClick(widget.inspector, Qt.Key.Key_Tab)
    assert not widget.inspector.hasFocus()
    widget.inspector.setFocus()
    QTest.keyClick(widget.inspector, Qt.Key.Key_Backtab)
    assert not widget.inspector.hasFocus()
    assert widget.inspector.toPlainText() == original


def test_burst_typing_coalesces_into_one_query_for_final_text(panel, qapp):
    from tests.gui.test_calculation_jobs import wait_for

    widget, connector, _ = panel
    count = len(connector.materials.queries)
    token = connector.document_state.token
    widget.name_search.setFocus()
    QTest.keyClicks(widget.name_search, "Dupli", delay=0)
    QTest.keyClick(widget.name_search, Qt.Key.Key_Backspace)
    QTest.keyClicks(widget.name_search, "icate", delay=0)
    wait_for(qapp, lambda: len(connector.materials.queries) > count, timeout=5)
    assert len(connector.materials.queries) == count + 1
    assert connector.materials.queries[-1].name == "Duplicate"
    assert not widget._query_timer.isActive()
    assert connector.document_state.token == token


def test_catalog_checkboxes_select_one_qualified_same_name_catalog(panel):
    widget, connector, (a, b, _) = panel
    widget.no_catalogs.click()
    widget._query_timer.stop()
    parent = next(
        widget.catalogs.topLevelItem(i)
        for i in range(widget.catalogs.topLevelItemCount())
        if widget.catalogs.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
        == b.key.source_id
    )
    parent.child(0).setCheckState(0, Qt.CheckState.Checked)
    result = finish(widget)
    assert tuple(match.key for match in result.matches) == (b.key,)
    assert widget.model.rowCount() == 1
    assert widget.make_query().catalog_keys == ((b.key.source_id, b.key.catalog),)
    widget.no_catalogs.click()
    result = finish(widget)
    assert not result.matches and widget.model.rowCount() == 0
    assert "Select" in widget.status.text()


def test_none_selection_remains_none_when_pending_new_source_arrives(panel):
    widget, connector, (a, _, _) = panel
    widget.no_catalogs.click()
    widget._query_timer.stop()
    late = replace(a, key=MaterialKey("late:catalog", "New catalog", "late-glass"))
    install(connector.materials, late.key.source_id, (late,))
    connector.materials.source_changed.emit(late.key.source_id)
    assert widget.make_query().catalog_keys == ()
    assert not finish(widget).matches
    widget.all_catalogs.click()
    assert (late.key.source_id, late.key.catalog) in widget.make_query().catalog_keys


@pytest.mark.parametrize(
    "order", [Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder]
)
def test_missing_numeric_coordinates_sort_last_in_either_direction(panel, order):
    widget, _, (_, _, missing) = panel
    widget.table.sortByColumn(4, order)
    last = widget.proxy.index(widget.proxy.rowCount() - 1, 0)
    assert widget.model.key_at(widget.proxy.mapToSource(last).row()) == missing.key


def test_large_query_publication_does_not_scan_source_for_each_result(
    panel, monkeypatch
):
    widget, connector, (a, _, _) = panel
    records = tuple(
        replace(a, key=MaterialKey(a.key.source_id, a.key.catalog, f"record-{index}"))
        for index in range(1000)
    )
    install(connector.materials, a.key.source_id, records)
    widget._rebuild_catalogs()
    widget.select_key(records[500].key)
    lookup = connector.materials.material
    calls = []

    def count_lookup(key):
        calls.append(key)
        return lookup(key)

    monkeypatch.setattr(connector.materials, "material", count_lookup)
    finish(widget)
    assert widget.model.rowCount() == 1001
    assert widget.selected_key == records[500].key
    # Source resolution must be linear overall. Only the retained selection
    # requires individual lookups; result assembly must use one indexed pass.
    assert len(calls) <= 3


def test_map_points_are_reused_across_filters_for_unchanged_sources(panel):
    widget, _, (a, b, missing) = panel
    first = {point.key: point for point in widget.map._points}
    assert missing.key not in widget._plot_points[a.key.source_id]
    widget.name_search.setText(a.key.record_id)
    finish(widget)
    assert widget.map._points == (first[a.key],)
    assert widget.map._points[0] is first[a.key]
    widget.name_search.clear()
    finish(widget)
    current = {point.key: point for point in widget.map._points}
    assert current[a.key] is first[a.key] and current[b.key] is first[b.key]


def test_source_point_refresh_rebuilds_same_key_coordinates_and_label(panel):
    widget, connector, (a, b, missing) = panel
    first = {point.key: point for point in widget.map._points}
    updated = replace(
        a,
        display_name="Reloaded glass",
        nd=replace(a.nd, value=1.75),
        vd=replace(a.vd, value=45.0),
    )
    install(connector.materials, a.key.source_id, (updated, missing))
    widget.sources.labels[a.key.source_id] = "Reloaded source"
    connector.materials.source_changed.emit(a.key.source_id)
    assert a.key.source_id not in widget._plot_points
    assert widget._plot_points[b.key.source_id][b.key] is first[b.key]
    finish(widget)
    current = {point.key: point for point in widget.map._points}
    assert current[a.key] is not first[a.key]
    assert (current[a.key].x, current[a.key].y) == (45.0, 1.75)
    assert "Reloaded glass" in current[a.key].label
    assert "Reloaded source" in current[a.key].label
    assert current[b.key] is first[b.key]

    connector.materials.errors[a.key.source_id] = "Source unavailable on reload"
    connector.materials.source_failed.emit(
        a.key.source_id, connector.materials.errors[a.key.source_id]
    )
    finish(widget)
    retained = {point.key: point for point in widget.map._points}
    assert retained[a.key] is current[a.key] and retained[b.key] is first[b.key]


def test_removed_source_and_shutdown_evict_point_cache(panel):
    widget, connector, (a, b, _) = panel
    retained = widget._plot_points[b.key.source_id][b.key]
    connector.materials.remove_source(a.key.source_id)
    assert a.key.source_id not in widget._plot_points
    assert widget._plot_points[b.key.source_id][b.key] is retained
    finish(widget)
    assert widget.map._points == (retained,)
    connector.calculation_jobs.stopped.emit()
    assert not widget._plot_points


def test_hidden_selection_reveal_and_locate_preserve_identity_and_view(panel):
    widget, connector, (a, _, _) = panel
    widget.select_key(a.key)
    widget.map.ax.set_xlim(70, 10)
    widget.map.ax.set_ylim(1.4, 1.9)
    viewport = widget.map.ax.get_xlim(), widget.map.ax.get_ylim()
    widget.name_search.setText("not present")
    finish(widget)
    assert widget.model.rowCount() == 0 and widget.selected_key == a.key
    assert "outside these filters" in widget.selection_summary.text()
    assert widget.map.ax.get_xlim() == viewport[0]
    assert widget.map.ax.get_ylim() == viewport[1]
    query_count = len(connector.materials.queries)
    widget.locate_button.click()
    assert widget.name_search.text() == "not present"
    assert len(connector.materials.queries) == query_count
    assert widget.map.ax.get_xlim() != viewport[0]
    widget.patent.setChecked(True)
    widget.support.setChecked(True)
    widget.reveal_button.click()
    assert not widget.name_search.text()
    assert not widget.patent.isChecked() and not widget.support.isChecked()
    connector.materials.complete()
    assert widget.selected_key == a.key
    assert widget.table.currentIndex().isValid()


def test_native_conditions_unavailable_coordinates_and_diagnostics_are_inspectable(
    panel,
):
    widget, connector, (a, _, missing) = panel
    source = connector.materials.accepted[a.key.source_id]
    connector.materials.accepted[a.key.source_id] = replace(
        source,
        diagnostics=(
            SourceDiagnostic(
                a.key.source_id, a.key.record_id, "Capture unavailable: example"
            ),
        ),
    )
    widget.select_key(a.key)
    text = widget.inspector.toPlainText()
    for expected in (
        "Computed",
        "Declared n_d",
        "Declared V_d",
        "air-relative",
        "20",
        "RII SPECS.nd",
        "generation-1",
        "Capture unavailable",
    ):
        assert expected in text
    assert "not normalized" in widget.conditions_notice.text()
    widget.select_key(missing.key)
    assert "zero dispersion" in widget.inspector.toPlainText()
    assert "Unknown" in widget.inspector.toPlainText()
    assert "1 without map coordinates" in widget.status.text()
    assert len(widget.map._points) == 2 and widget.model.rowCount() == 3


def test_inspector_reference_metadata_is_plain_text_and_safe(panel):
    widget, connector, (a, _, _) = panel
    widget.select_key(a.key)
    text = widget.inspector.toPlainText()
    assert "fixture-hash" in text
    assert "source text" in text
    assert widget.inspector.isReadOnly()
    assert widget.selection_summary.textFormat() == Qt.TextFormat.PlainText


def test_invalid_patent_or_support_input_retains_last_valid_rows(panel):
    widget, connector, (a, _, _) = panel
    previous = tuple(widget.model.key_at(row) for row in range(widget.model.rowCount()))
    count = len(connector.materials.queries)
    widget.support.setChecked(True)
    widget.wave_min.setValue(0.8)
    widget.wave_max.setValue(0.4)
    widget.find_matches()
    assert len(connector.materials.queries) == count
    assert (
        tuple(widget.model.key_at(row) for row in range(widget.model.rowCount()))
        == previous
    )
    assert "Previous results" in widget.status.text()
    widget.support.setChecked(False)
    widget.patent.setChecked(True)
    widget.target_nd.lineEdit().setText("-")
    widget.find_matches()
    assert len(connector.materials.queries) == count
    assert "Finish" in widget.status.text()
    widget.target_nd.setValue(a.nd.value)
    widget.target_vd.setValue(a.vd.value)
    widget.tolerance_nd.setValue(0.00001)
    widget.tolerance_vd.setValue(0.0001)
    result = finish(widget)
    assert tuple(match.key for match in result.matches) == (a.key,)


def test_refresh_of_excluded_design_context_keeps_matches_and_query(panel):
    widget, connector, (a, _, _) = panel
    query_count = len(connector.materials.queries)
    design_id = f"design:{connector.document_state.token.document_id}"
    design = replace(a, key=MaterialKey(design_id, "Current design", "surface-id"))
    install(connector.materials, design_id, (design,))
    connector.materials.source_changed.emit(design_id)
    assert not widget._query_timer.isActive()
    assert len(connector.materials.queries) == query_count
    assert widget.model.rowCount() == 3 and len(widget.map._context) == 1
    widget.map.selection_requested.emit(design.key)
    assert widget.selected_key == design.key
    assert "not counted as a match" in widget.selection_summary.text()
    assert not widget.reveal_button.isEnabled()
    widget.context_check.setChecked(False)
    assert not widget.map._context


def test_source_removal_keeps_last_inspected_identity_without_name_rebinding(panel):
    widget, connector, (a, b, _) = panel
    widget.select_key(a.key)
    connector.materials.remove_source(a.key.source_id)
    finish(widget)
    assert widget.selected_key == a.key and widget.selected_material is None
    assert "Source unavailable" in widget.selection_summary.text()
    assert a.key.record_id in widget.inspector.toPlainText()
    assert widget.model.rowCount() == 1
    assert widget.model.key_at(0) == b.key
    assert not widget.reveal_button.isEnabled()


def test_background_results_do_not_steal_focus_and_compact_regions_restore_it(
    panel, qapp
):
    widget, connector, (a, _, _) = panel
    widget.name_search.setFocus()
    qapp.processEvents()
    assert widget.name_search.hasFocus()
    finish(widget)
    widget.select_key(a.key)
    assert widget.name_search.hasFocus()
    widget.catalogs.setFocus()
    widget.filters_toggle.setChecked(False)
    assert widget.filters.isHidden() and widget.filters_toggle.hasFocus()
    widget.resize(690, 700)
    qapp.processEvents()
    widget.table.setFocus()
    widget.details_toggle.setChecked(True)
    assert widget.center.isHidden()
    assert widget.details_toggle.hasFocus()
    widget.inspector.setFocus()
    widget.details_toggle.setChecked(False)
    assert widget.inspector.isHidden()
    assert widget.details_toggle.hasFocus()


def test_f6_cycles_visible_regions_without_refocusing_inspection(panel, qapp):
    widget, connector, _ = panel
    widget.name_search.setFocus()
    widget._cycle_focus(1)
    assert widget.catalogs.hasFocus()
    widget._cycle_focus(1)
    assert widget.map.canvas.hasFocus()
    widget._cycle_focus(1)
    assert widget.table.hasFocus()
    widget._cycle_focus(1)
    assert widget.inspector.hasFocus()
    widget._cycle_focus(-1)
    assert widget.table.hasFocus()


def test_hide_and_worker_shutdown_cancel_browser_activity(panel, qapp):
    widget, connector, _ = panel
    widget.name_search.setText("pending")
    assert widget._query_timer.isActive()
    widget.hide()
    assert not widget._query_timer.isActive()
    assert connector.materials.query_cancelled >= 1
    previous_focus = qapp.focusWidget()
    widget._cycle_focus(-1)
    assert qapp.focusWidget() is previous_focus
    count = len(connector.materials.queries)
    connector.calculation_jobs.stopped.emit()
    widget.show()
    qapp.processEvents()
    widget.find_matches()
    assert len(connector.materials.queries) == count
    assert not widget.sources._timer.isActive()


def test_splitter_preferences_are_saved_without_optic_state(panel, tmp_path):
    widget, connector, _ = panel
    settings = QSettings(str(tmp_path / "materials.ini"), QSettings.Format.IniFormat)
    widget.settings = settings
    token = connector.document_state.token
    widget.browse_splitter.setSizes([220, 700, 240])
    expected = widget.browse_splitter.saveState()
    widget.hide()
    assert settings.value("Materials/browseSplitter") == expected
    assert connector.document_state.token == token


def test_stale_delivery_and_failed_query_preserve_accepted_rows(panel):
    widget, connector, _ = panel
    accepted = connector.materials.last_query_result
    stale = replace(accepted, request_id=-99, matches=())
    connector.materials.query_finished.emit(stale)
    assert widget.model.rowCount() == 3
    connector.materials.query_failed.emit("worker unavailable")
    assert "worker unavailable" in widget.status.text()
    assert widget.model.rowCount() == 3


def test_invalidated_rows_are_explicitly_stale_until_current_result_arrives(panel):
    widget, connector, (a, _, _) = panel
    widget.select_key(a.key)
    connector.materials.last_query_result = None
    connector.materials.query_invalidated.emit()
    assert not widget._results_current
    assert "refresh" in widget.status.text().lower()
    assert widget.model.rowCount() == 3
    assert widget.selected_key == a.key
    finish(widget)
    assert widget._results_current
    assert "matches" in widget.status.text()


@pytest.mark.parametrize("initial", [False, True])
def test_failed_source_remains_visible_after_successful_query(panel, initial):
    from optiland.materials.sources import InstalledSource

    widget, connector, (a, _, _) = panel
    source_id = "rii:installed" if initial else a.key.source_id
    if initial:
        widget.sources.requests[source_id] = InstalledSource()
        widget.sources.labels[source_id] = "Installed glass catalogs"
    connector.materials.errors[source_id] = "<b>Catalog could not be read</b>"
    connector.materials.source_failed.emit(
        source_id, connector.materials.errors[source_id]
    )
    finish(widget)
    rows = [
        widget.catalogs.topLevelItem(index)
        for index in range(widget.catalogs.topLevelItemCount())
    ]
    row = next(
        item for item in rows if item.data(0, Qt.ItemDataRole.UserRole) == source_id
    )
    assert "failed" in row.text(0).lower()
    assert "Catalog could not be read" in row.toolTip(0)
    assert not row.toolTip(0).startswith("<b>")
    if not initial:
        widget.select_key(a.key)
        assert "Catalog could not be read" in widget.inspector.toPlainText()
        assert widget.model.rowCount() == 3
    connector.materials.errors.pop(source_id)
    connector.materials.state_changed.emit(source_id, "loading")
    refreshed = next(
        widget.catalogs.topLevelItem(i)
        for i in range(widget.catalogs.topLevelItemCount())
        if widget.catalogs.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
        == source_id
    )
    assert "failed" not in refreshed.text(0).lower()
    if initial:
        assert "loading" in refreshed.text(0).lower()


def test_cancelled_first_load_has_explicit_source_state_and_failure_wins(panel):
    from optiland.materials.sources import InstalledSource

    widget, connector, _ = panel
    source_id = "rii:installed"
    widget.sources.requests[source_id] = InstalledSource()
    widget.sources.labels[source_id] = "Installed glass catalogs"

    def source_text():
        return next(
            widget.catalogs.topLevelItem(index).text(0)
            for index in range(widget.catalogs.topLevelItemCount())
            if widget.catalogs.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            == source_id
        ).lower()

    widget._rebuild_catalogs()
    assert "not loaded" in source_text()
    connector.materials.state_changed.emit(source_id, "loading")
    assert "loading" in source_text()
    connector.materials.state_changed.emit(source_id, "cancelled")
    assert "cancelled" in source_text() and "loading" not in source_text()
    assert connector.materials.source_result(source_id) is None
    connector.materials.errors[source_id] = "Persistent catalog failure"
    connector.materials.source_failed.emit(source_id, "Persistent catalog failure")
    connector.materials.state_changed.emit(source_id, "cancelled")
    assert "failed" in source_text() and "cancelled" not in source_text()


def test_ctrl_f_selects_search_and_ctrl_c_copies_full_qualified_row(panel, qapp):
    widget, connector, (a, _, _) = panel
    widget.name_search.setText("Duplicate")
    widget._query_timer.stop()
    widget.table.setFocus()
    widget.activateWindow()
    qapp.processEvents()
    QTest.keyClick(widget.table, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert widget.name_search.hasFocus()
    assert widget.name_search.selectedText() == "Duplicate"
    widget.select_key(a.key)
    widget.table.setFocus()
    QTest.keyClick(widget.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    text = qapp.clipboard().text()
    assert "Source ID" in text and "Record ID" in text
    assert a.key.source_id in text and a.key.catalog in text and a.key.record_id in text
    assert "n_d" in text and "V_d" in text


def test_patent_activation_shows_distance_rank_without_overriding_later_sort(panel):
    widget, connector, (a, _, _) = panel
    widget.patent.setChecked(True)
    assert widget.table.horizontalHeader().sortIndicatorSection() == 7
    assert (
        widget.table.horizontalHeader().sortIndicatorOrder()
        == Qt.SortOrder.AscendingOrder
    )
    widget.table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
    widget.target_nd.setValue(a.nd.value)
    widget.target_vd.setValue(a.vd.value)
    finish(widget)
    assert widget.table.horizontalHeader().sortIndicatorSection() == 3
    assert (
        widget.table.horizontalHeader().sortIndicatorOrder()
        == Qt.SortOrder.DescendingOrder
    )


def test_synchronous_submission_failure_keeps_actionable_status(panel, monkeypatch):
    widget, connector, _ = panel
    monkeypatch.setattr(
        connector.materials,
        "query",
        lambda _: connector.materials.query_failed.emit("queue is full"),
    )
    widget.find_matches()
    assert "queue is full" in widget.status.text()
    assert "Finding materials" not in widget.status.text()


def test_reverse_region_navigation_from_secondary_button_reaches_last_region(panel):
    widget, _, _ = panel
    widget.help_button.setFocus()
    widget._cycle_focus(-1)
    assert widget.inspector.hasFocus()


def test_oversized_native_payload_does_not_decode_on_gui_thread(panel, monkeypatch):
    import optiland_gui.materials_panel as module

    widget, connector, (a, _, missing) = panel
    data = json.loads(a.owned_data)
    data["metadata"]["large_reference"] = "x" * (300 * 1024)
    large = replace(a, owned_data=json.dumps(data).encode())
    install(connector.materials, a.key.source_id, (large, missing))
    monkeypatch.setattr(
        module.json, "loads", lambda _: pytest.fail("Large native data decoded on Qt")
    )
    widget.select_key(a.key)
    text = widget.inspector.toPlainText()
    assert "256" in text and "Metadata preview unavailable" in text
    assert "air-relative" in text and "Computed" in text
    assert widget.selected_material.owned_data == large.owned_data


def test_metadata_preview_truncates_text_without_losing_optical_selection(panel):
    widget, connector, (a, _, missing) = panel
    data = json.loads(a.owned_data)
    data["metadata"]["reference"] = "x" * 25000
    long_record = replace(a, owned_data=json.dumps(data).encode())
    install(connector.materials, a.key.source_id, (long_record, missing))
    widget.select_key(a.key)
    assert "truncated" in widget.inspector.toPlainText()
    assert len(widget.inspector.toPlainText()) < 23000
    assert widget.selected_key == a.key
    data["metadata"] = {}
    empty = replace(a, owned_data=json.dumps(data).encode())
    install(connector.materials, a.key.source_id, (empty, missing))
    widget.select_key(a.key)
    assert "Source metadata and references" not in widget.inspector.toPlainText()


def test_unknown_key_empty_copy_and_design_reveal_are_harmless(panel, qapp):
    widget, connector, (a, _, _) = panel
    widget.table.clearSelection()
    qapp.clipboard().setText("keep previous clipboard")
    widget.copy_selected()
    assert qapp.clipboard().text() == "keep previous clipboard"
    widget.select_key(a.key)
    widget.select_key(MaterialKey("missing", "missing", "missing"))
    assert widget.selected_key == a.key
    design_id = f"design:{connector.document_state.token.document_id}"
    context = replace(a, key=MaterialKey(design_id, "Current design", "surface"))
    offmap = describe_material(
        IdealMaterial(1), MaterialKey(design_id, "Current design", "air")
    )
    install(connector.materials, design_id, (context, offmap))
    connector.materials.source_changed.emit(design_id)
    widget.select_key(context.key)
    count = len(connector.materials.queries)
    widget.reveal_selected()
    assert len(connector.materials.queries) == count
    widget._rebuild_catalogs()
    assert all(
        widget.catalogs.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) != design_id
        for i in range(widget.catalogs.topLevelItemCount())
    )


def test_source_update_during_incomplete_input_retains_rows_and_reports_cancel(panel):
    widget, connector, (a, _, _) = panel
    widget.patent.setChecked(True)
    widget.target_nd.lineEdit().setText("-")
    widget._query_timer.stop()
    connector.materials.source_changed.emit(a.key.source_id)
    assert not widget._query_timer.isActive() and widget.model.rowCount() == 3
    connector.materials.state_changed.emit(a.key.source_id, "cancelled")
    assert "cancelled" in widget.status.text().lower()


def test_settings_restore_lazy_activation_and_theme_without_loading_catalog_io(
    qapp, tmp_path, monkeypatch
):
    connector = make_connector()
    settings = QSettings(str(tmp_path / "restore.ini"), QSettings.Format.IniFormat)
    first = MaterialsPanel(connector, autoload=False, settings=settings)
    first.resize(1200, 800)
    first.show()
    qapp.processEvents()
    first._query_timer.stop()
    first.hide()
    saved = settings.value("Materials/browseSplitter")
    second = MaterialsPanel(connector, settings=settings)
    active = []
    monkeypatch.setattr(second.sources, "set_active", active.append)
    second.show()
    qapp.processEvents()
    assert active == [True]
    assert settings.value("Materials/browseSplitter") == saved
    second.update_theme("dark")
    assert second.map.figure.get_facecolor()[0] < 0.5
    second.update_theme("light")
    assert second.map.figure.get_facecolor()[0] > 0.5
    first.deleteLater()
    second.hide()
    second._jobs_stopped()
    second.queue_query()
    assert not second._query_timer.isActive()
    second.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_real_connector_and_worker_publish_portable_details_without_design_edit(qapp):
    from optiland.materials.sources import OwnedEntry, OwnedSource
    from optiland_gui.optiland_connector import OptilandConnector
    from tests.gui.test_calculation_jobs import wait_for

    connector = OptilandConnector()
    widget = MaterialsPanel(connector, autoload=False)
    record = material_record("local:fixture", "qualified")
    original = connector.document_state.token
    try:
        widget.resize(1180, 800)
        widget.show()
        qapp.processEvents()
        widget._query_timer.stop()
        connector.materials.load_source(
            OwnedSource(
                record.key.source_id,
                (
                    OwnedEntry(
                        record.key.record_id, record.display_name, record.owned_data
                    ),
                ),
            )
        )
        wait_for(
            qapp,
            lambda: record.key.source_id in connector.materials.source_ids,
            timeout=30,
        )
        widget.find_matches()
        wait_for(qapp, lambda: widget.model.rowCount() == 1, timeout=30)
        # Owned sources intentionally use the Owned catalog; exact record and
        # source identity survive the worker's native-material reconstruction.
        key = widget.model.key_at(0)
        assert key.source_id == record.key.source_id
        assert key.record_id == record.key.record_id
        widget.select_key(key)
        assert "RII SPECS.nd" in widget.inspector.toPlainText()
        assert "fixture-hash" in widget.inspector.toPlainText()
        assert connector.document_state.token == original
        connector.calculation_jobs.shutdown()
        wait_for(qapp, lambda: connector.calculation_jobs._process is None, timeout=30)
        assert widget._closed and widget.sources._closed
    finally:
        widget.hide()
        connector._backend_timer.stop()
        connector.calculation_jobs.shutdown()
        wait_for(qapp, lambda: connector.calculation_jobs._process is None, timeout=30)
        widget.deleteLater()
        connector.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
