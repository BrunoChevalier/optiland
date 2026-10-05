"""Native pin identity, shared spectral cells, stale requests and export seams."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester, QTest
from PySide6.QtWidgets import QTabWidget, QWidget

from optiland.materials import DataMaterial
from optiland.materials.comparison import (
    MaterialDetails,
    NearbyResult,
    SpectralResult,
    SpectralSeries,
)
from optiland.materials.properties import (
    MaterialKey,
    MaterialMatch,
    PropertyValue,
    SourceStamp,
    describe_material,
)
from optiland_gui.widgets.material_compare_panel import (
    ComparisonPin,
    MaterialComparePanel,
)
from tests.gui.test_calculation_jobs import wait_for


@pytest.fixture
def records():
    return tuple(
        describe_material(
            DataMaterial.from_coefficients(
                "formula 5",
                [1.5 + i * 0.02, 0.005, -2],
                wavelength_range=(0.4, 0.8),
                name="Same <name>",
            ),
            MaterialKey(f"source-{i}", "Catalog", f"reference-{i}"),
        )
        for i in range(5)
    )


def stamp(record):
    return SourceStamp(record.key.source_id, "generation-1")


@pytest.fixture
def compare(qapp):
    widget = MaterialComparePanel()
    widget.resize(1000, 900)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close_service()
    widget.hide()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def add(compare, records, count=1):
    for record in records[:count]:
        assert compare.pin(
            record, stamp(record), source_label="Source " + record.key.source_id
        )
    compare.samples.setValue(3)
    compare.recompute()
    assert compare.request is not None


def result(compare, *, gap=False, quantity=None, no_domain=False):
    request = compare.request
    if quantity is not None:
        request = replace(request, quantity=quantity)
    wavelengths = () if no_domain else (0.4, 0.6, 0.8)
    spectra = tuple(
        SpectralSeries(
            pin.key,
            pin.display_name,
            compare._records[pin.key].conditions,
            (0.4, 0.8),
            ()
            if no_domain
            else (
                PropertyValue(
                    0 if request.quantity == "transmission" else 1.5 + i * 0.1,
                    "available",
                ),
                PropertyValue(None, "missing", reason="Outside declared support")
                if gap
                else PropertyValue(
                    0.7 if request.quantity == "transmission" else 1.4 + i * 0.1,
                    "available",
                ),
                PropertyValue(
                    1 if request.quantity == "transmission" else 1.3 + i * 0.1,
                    "available",
                ),
            ),
            MaterialDetails(
                "Formula 5",
                (("coefficients", "1.5 0.005 -2"), ("source", "<supplier>")),
                ("Native definition",),
            ),
            ("Unknown support excluded",) if no_domain else (),
        )
        for i, pin in enumerate(compare.pins)
    )
    return SpectralResult(
        tuple(compare._stamps.values()),
        request,
        None if no_domain else (0.4, 0.8),
        wavelengths,
        spectra,
        (),
    )


def test_four_exact_pins_reorder_without_selection_or_silent_eviction(compare, records):
    selected, changes, messages = [], [], []
    compare.selection_requested.connect(selected.append)
    compare.pins_changed.connect(changes.append)
    compare.message.connect(messages.append)
    add(compare, records, 4)
    keys = tuple(record.key for record in records[:4])
    assert compare.pin_keys == keys
    assert not selected
    assert not compare.pin(records[0], stamp(records[0]))
    assert not compare.pin(records[4], stamp(records[4]))
    assert compare.pin_keys == keys
    assert "Remove one" in messages[-1]
    compare.pin_table.setCurrentCell(2, 0)
    QTest.mouseClick(compare.up_button, Qt.MouseButton.LeftButton)
    assert compare.pin_keys == (keys[0], keys[2], keys[1], keys[3])
    assert compare.current_key() == keys[2]
    assert not selected
    QTest.mouseClick(compare.inspect_button, Qt.MouseButton.LeftButton)
    assert selected == [keys[2]]
    QTest.mouseClick(compare.remove_button, Qt.MouseButton.LeftButton)
    assert keys[2] not in compare.pin_keys and len(compare.pins) == 3
    assert compare.pin(records[4], stamp(records[4]))
    assert compare.pin_keys[-1] == records[4].key
    assert len(changes) == 7


def test_lost_source_retains_identity_and_restores_only_fresh_labels(compare, records):
    add(compare, records)
    old_result = result(compare)
    assert compare.set_result(old_result)
    key = records[0].key
    compare.refresh_pins({}, {}, {key: "Catalog removed"})
    assert compare.pin_keys == (key,)
    assert compare.pins[0].display_name == "Same <name>"
    assert compare._records == {}
    assert compare.result is None and not compare.csv_button.isEnabled()
    assert compare.pin_table.item(0, 4).text() == "Catalog removed"
    assert not compare.inspect_button.isEnabled()
    assert not compare.set_result(old_result)
    new = replace(records[0], display_name="New accepted name")
    compare.refresh_pins(
        {key: new}, {key.source_id: SourceStamp(key.source_id, "generation-2")}
    )
    assert compare.pins[0].display_name == "New accepted name"
    assert compare.inspect_button.isEnabled()
    assert compare._records[key] is new
    assert compare.result is None
    compare.recompute()
    fresh = result(compare)
    compare.set_result(fresh)
    compare.refresh_pins({key: new}, compare._stamps)
    assert compare.result is fresh


def test_presets_retain_unavailable_pins_and_validate_before_changing(compare, records):
    pins = tuple(
        ComparisonPin(r.key, r.display_name, "Saved source") for r in records[:4]
    )
    compare.set_pins(pins)
    assert compare.pins == pins and not compare._records
    assert compare.pin_table.rowCount() == 4
    with pytest.raises(ValueError, match="unique"):
        compare.set_pins((pins[0], pins[0]))
    with pytest.raises(ValueError, match="four"):
        compare.set_pins(pins + (ComparisonPin(records[4].key, "fifth"),))
    with pytest.raises(ValueError, match="exact key"):
        ComparisonPin("name", "invalid")
    assert compare.pins == pins
    with pytest.raises(ValueError, match="stamp"):
        compare.pin(records[4], stamp(records[0]))
    with pytest.raises(ValueError, match="identities"):
        compare.refresh_pins(
            {records[0].key: records[1]}, {records[0].key.source_id: stamp(records[0])}
        )
    assert compare.pins == pins


@pytest.mark.parametrize("quantity", ["n", "transmission"])
def test_curves_and_table_share_values_gaps_and_zero_without_optical_calls(
    compare, records, monkeypatch, quantity
):
    add(compare, records, 4)
    monkeypatch.setattr(DataMaterial, "n", lambda *a: pytest.fail("optics on Qt"))
    compare.quantity.setCurrentIndex(compare.quantity.findData(quantity))
    compare.recompute()
    accepted = result(compare, gap=True)
    assert compare.set_result(accepted)
    tester = QAbstractItemModelTester(
        compare.model, QAbstractItemModelTester.FailureReportingMode.Warning
    )
    assert tester.model() is compare.model
    assert compare.model.result is accepted and compare.result is accepted
    assert compare.model.rowCount() == 3 and compare.model.columnCount() == 5
    for column, series in enumerate(accepted.series, 1):
        line = compare.ax.lines[column - 1]
        np.testing.assert_equal(line.get_xdata(), accepted.wavelengths_um)
        for row, cell in enumerate(series.values):
            index = compare.model.index(row, column)
            assert compare.model.data(index, Qt.ItemDataRole.UserRole) == cell.value
            if cell.value is None:
                assert np.isnan(line.get_ydata()[row])
                assert compare.model.data(index) == "Missing"
                assert "Outside declared support" in compare.model.data(
                    index, Qt.ItemDataRole.ToolTipRole
                )
            else:
                assert line.get_ydata()[row] == cell.value
    assert len({line.get_linestyle() for line in compare.ax.lines}) == 4
    assert len({line.get_marker() for line in compare.ax.lines}) == 4
    assert "<supplier>" in compare.details.toPlainText()
    assert "Formula 5" in compare.details.toPlainText()
    assert compare.csv_button.isEnabled() and compare.png_button.isEnabled()
    assert "save_figure" not in compare.toolbar._actions
    assert not any(action.text() == "Save" for action in compare.toolbar.actions())
    width, height, rgba = compare.capture_rgba()
    assert len(rgba) == width * height * 4
    compare.canvas.resize(400, 150)
    compare._resize_plot(None)
    compare.canvas.draw()
    extent = compare.ax.yaxis.label.get_window_extent(compare.canvas.get_renderer())
    assert extent.x0 >= 0
    assert extent.y0 >= 0 and extent.y1 <= compare.canvas.get_width_height()[1]


def test_quantity_edit_revokes_old_result_and_exports_immediately(compare, records):
    add(compare, records)
    accepted = result(compare)
    compare.set_result(accepted)
    compare.quantity.setFocus()
    QTest.keyClick(compare.quantity, Qt.Key.Key_Down)
    assert compare.quantity.currentData() == "transmission"
    assert compare.request is None and compare.result is None
    assert not compare.csv_button.isEnabled()
    assert compare.model.rowCount() == 0
    assert not compare.set_result(accepted)
    with pytest.raises(ValueError, match="current comparison"):
        compare.capture_rgba()
    compare.recompute()
    assert compare.request.quantity == "transmission"
    assert compare.thickness.isEnabled()
    assert compare.set_result(result(compare))


def test_locale_partial_interval_and_zero_thickness(compare, records):
    add(compare, records)
    for control in (compare.wave_min, compare.wave_max, compare.thickness):
        control.setLocale(QLocale("nl_BE"))
    compare.interval_mode.setCurrentIndex(1)
    compare.quantity.setCurrentIndex(1)
    compare.wave_min.lineEdit().setFocus()
    QTest.keyClick(
        compare.wave_min.lineEdit(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier
    )
    QTest.keyClick(compare.wave_min.lineEdit(), Qt.Key.Key_Backspace)
    compare.recompute()
    assert compare.request is None
    assert "Finish" in compare.status.text()
    compare.wave_min.setValue(0.9)
    compare.wave_max.setValue(0.8)
    compare.recompute()
    assert compare.request is None and "increasing" in compare.status.text()
    compare.wave_min.setValue(0.45)
    compare.thickness.setValue(0)
    compare.recompute()
    assert compare.request.interval_um == (0.45, 0.8)
    assert compare.request.thickness_mm == 0
    assert "," in compare.wave_min.text()


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [("wave_min", "0.52", 0.52), ("thickness", "7.5", 7.5), ("samples", "501", 501)],
)
def test_focused_numeric_draft_matches_debounced_request(
    compare, records, name, text, expected, qapp
):
    add(compare, records)
    compare.interval_mode.setCurrentIndex(1)
    compare.quantity.setCurrentIndex(1)
    control = getattr(compare, name)
    control.setLocale(QLocale("en_US"))
    edit = control.lineEdit()
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(edit, text)
    wait_for(
        qapp,
        lambda: compare.request is not None and control.value() == expected,
        timeout=2,
    )
    assert edit.hasFocus()  # No Enter or focus-out masked a stale draft.
    assert control.value() == expected
    request = compare.request
    actual = {
        "wave_min": request.interval_um[0],
        "thickness": request.thickness_mm,
        "samples": request.sample_count,
    }
    assert actual[name] == expected


def test_nearby_commits_valid_draft_and_owner_can_revoke_result(compare, records):
    add(compare, records)
    compare.data_tabs.setCurrentIndex(2)
    requests = []
    compare.nearby_requested.connect(lambda *args: requests.append(args))
    edit = compare.nd_scale.lineEdit()
    compare.nd_scale.setLocale(QLocale("en_US"))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(edit, "0.02")
    compare.find_nearby()
    assert requests[-1] == (records[0].key, 0.02, 1)
    nearby = NearbyResult((), records[0].key, 0.02, 1, (), ())
    assert compare.set_nearby(nearby, records)
    compare.clear_nearby()
    assert compare._nearby is None and not compare.set_nearby(nearby, records)
    compare.set_nearby_error("The sources changed")
    assert compare.nearby_status.text() == "The sources changed"


def test_hidden_compare_tab_does_not_force_large_desktop_minimum(compare, qapp):
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Browse")
    tabs.addTab(compare, "Compare")
    tabs.resize(680, 730)
    tabs.show()
    qapp.processEvents()
    try:
        assert tabs.width() == 680
        assert tabs.height() == 730
        assert tabs.minimumSizeHint().width() <= 680
        assert tabs.minimumSizeHint().height() <= 730
    finally:
        tabs.removeTab(1)
        compare.setParent(None)
        tabs.close()


def test_compact_samples_can_use_full_height_and_restore_plot(compare, records, qapp):
    add(compare, records)
    compare.set_result(result(compare))
    compare.resize(650, 620)
    qapp.processEvents()
    before = compare.table.height()
    split = compare.splitter.sizes()
    compare.expand_data.setFocus()
    QTest.keyClick(compare.expand_data, Qt.Key.Key_Space)
    qapp.processEvents()
    assert not compare.plot.isVisible()
    assert compare.table.height() > before + 100
    assert compare.model.rowCount() == 3
    compare.table.setFocus()
    QTest.keyClick(compare.table, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    assert compare.table.currentIndex().row() == 2
    QTest.mouseClick(compare.expand_data, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert compare.plot.isVisible()
    assert compare.expand_data.text() == "Expand data"
    assert compare.splitter.sizes() == split
    compare.canvas.setFocus()
    compare.expand_data.setChecked(True)
    assert compare.table.hasFocus()


def test_no_common_domain_keeps_slots_and_explains_recovery(compare, records):
    add(compare, records, 2)
    accepted = result(compare, no_domain=True)
    assert compare.set_result(accepted)
    assert compare.pin_table.rowCount() == 2
    assert compare.model.rowCount() == 0
    assert "explicit interval" in compare.status.text()
    assert "Pin 2: Unknown support excluded" in compare.status.text()
    assert not compare.csv_button.isEnabled()
    compare.pin_table.setCurrentCell(1, 0)
    assert "Unknown support excluded" in compare.details.toPlainText()


def test_late_nearby_result_does_not_overwrite_changed_target_or_partial_scale(
    compare, records
):
    add(compare, records, 2)
    compare.pin_table.setCurrentCell(0, 0)
    requests, selected = [], []
    compare.nearby_requested.connect(lambda *args: requests.append(args))
    compare.selection_requested.connect(selected.append)
    compare.find_nearby()
    key, nd, vd = requests[-1]
    match = MaterialMatch(records[2].key, delta_nd=0.01, delta_vd=-0.2, distance=0.2)
    nearby = NearbyResult((), key, nd, vd, (match,), (("missing", 1),))
    assert compare.set_nearby(nearby, records)
    compare.nearby_table.setCurrentIndex(compare.nearby_proxy.index(0, 0))
    compare.inspect_nearby()
    assert selected == [records[2].key]
    assert "across loaded sources" in compare.nearby_status.text()
    compare.nd_scale.lineEdit().setFocus()
    QTest.keyClick(
        compare.nd_scale.lineEdit(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier
    )
    QTest.keyClick(compare.nd_scale.lineEdit(), Qt.Key.Key_Backspace)
    assert not compare.set_nearby(nearby, records)
    assert compare.nearby_model.rowCount() == 0
    compare.find_nearby()
    assert "Finish" in compare.nearby_status.text()
    compare.nd_scale.setValue(nd)
    compare.pin_table.setCurrentCell(1, 0)
    assert not compare.set_nearby(nearby, records)


def test_native_table_copy_plot_keyboard_theme_and_export_buttons(
    compare, records, qapp
):
    add(compare, records, 2)
    compare.set_result(result(compare))
    exports = []
    compare.export_requested.connect(exports.append)
    QTest.mouseClick(compare.csv_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(compare.png_button, Qt.MouseButton.LeftButton)
    assert exports == ["csv", "png"]
    compare.table.setFocus()
    compare.table.selectRow(0)
    QTest.keyClick(compare.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert len(qapp.clipboard().text().split("\t")) == 3
    before = compare.view_bounds()
    QTest.keyClick(compare.canvas, Qt.Key.Key_Right)
    assert compare.view_bounds()[0] > before[0]
    QTest.keyClick(compare.canvas, Qt.Key.Key_Plus)
    assert compare.view_bounds()[1] - compare.view_bounds()[0] < before[1] - before[0]
    QTest.keyClick(compare.canvas, Qt.Key.Key_Home)
    np.testing.assert_allclose(compare.view_bounds(), before)
    compare.toolbar.pan()
    QTest.keyClick(compare.canvas, Qt.Key.Key_Escape)
    assert not compare.toolbar.mode
    compare.update_theme("dark")
    assert compare.ax.lines[0].get_color() == compare.DARK_COLORS[0]
    compare.resize(680, 760)
    qapp.processEvents()
    compare.canvas.draw()
    renderer = compare.canvas.get_renderer()
    assert compare.ax.xaxis.label.get_window_extent(renderer).y0 >= 0
    assert compare.ax.yaxis.label.get_window_extent(renderer).x0 >= 0
    compare.update_theme("light")
    assert compare.ax.lines[0].get_color() == compare.LIGHT_COLORS[0]


def test_no_late_work_or_results_after_shutdown(compare, records):
    add(compare, records)
    accepted = result(compare)
    compare.close_service()
    assert not compare.set_result(accepted)
    assert not compare.pin(records[1], stamp(records[1]))
    compare.recompute()
    assert compare.request is None
    compare.set_pins(())
    compare.refresh_pins({}, {})
    compare.inspect_current()
    compare.find_nearby()
    assert not compare.isEnabled()


def test_spectral_model_invalid_indexes_and_header_identity(compare, records):
    add(compare, records)
    compare.set_result(result(compare))
    model = compare.model
    assert model.data(QModelIndex()) is None
    assert model.data(model.index(0, 1), Qt.ItemDataRole.DecorationRole) is None
    assert model.data(model.index(0, 0), Qt.ItemDataRole.UserRole) == 0.4
    assert (
        model.rowCount(model.index(0, 0)) == model.columnCount(model.index(0, 0)) == 0
    )
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Wavelength (µm)"
    assert records[0].key.record_id in model.headerData(
        1, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole
    )
    assert model.headerData(0, Qt.Orientation.Vertical) == 1
    broken = replace(
        result(compare), series=(replace(result(compare).series[0], values=()),)
    )
    with pytest.raises(ValueError, match="sample grid"):
        compare.set_result(broken)


def test_unavailable_pin_uses_fallback_label_without_stale_payload(compare, records):
    add(compare, records)
    original = result(compare)
    pin = ComparisonPin(records[0].key, "Saved human label", "Unavailable source")
    compare.set_pins((pin,))
    compare.recompute()
    unavailable = replace(
        original,
        request=compare.request,
        series=(
            replace(
                original.series[0],
                display_name=pin.key.record_id,
                details=None,
                values=tuple(PropertyValue(None, "missing") for _ in range(3)),
            ),
        ),
    )
    assert compare.set_result(unavailable)
    assert compare._records == {}
    assert "Saved human label" in compare.model.headerData(1, Qt.Orientation.Horizontal)
    assert "Saved human label" in compare.ax.lines[0].get_label()
    assert "formula" not in compare.details.toPlainText()


def test_empty_and_closed_actions_do_not_request_work(compare, records):
    calls = []
    compare.request_changed.connect(calls.append)
    compare.recompute()
    compare.remove_current()
    compare.move_current(1)
    compare.inspect_nearby()
    assert calls == [None]
    assert "Pin up to four" in compare.status.text()
    add(compare, records)
    compare.move_current(-1)
    assert compare.pin_keys == (records[0].key,)
    compare.close_service()
    compare._settings_changed()
    assert compare.request is None


def test_plot_navigation_history_guard_and_other_keys(compare, records):
    add(compare, records)
    compare.set_result(result(compare))
    initial = compare.view_bounds()
    for key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Minus):
        QTest.keyClick(compare.canvas, key)
    assert len(compare.toolbar._nav_stack) == 5
    for _ in range(4):
        compare.toolbar.back()
    np.testing.assert_allclose(compare.view_bounds(), initial)
    compare.toolbar.zoom()
    QTest.keyClick(compare.canvas, Qt.Key.Key_Escape)
    assert not compare.toolbar.mode
    QTest.keyClick(compare.canvas, Qt.Key.Key_Escape)
    QTest.keyClick(compare.canvas, Qt.Key.Key_A)
    compare.ax.set_xlim(-1e100, 1e100)
    before = compare.view_bounds()
    QTest.keyClick(compare.canvas, Qt.Key.Key_Right)
    assert compare.view_bounds() == before  # Reject overflow-sized view changes.
    compare.ax.set_xlim(1, 1 + 2e-12)
    for _ in range(8):
        QTest.keyClick(compare.canvas, Qt.Key.Key_Plus)
    assert compare.view_bounds()[1] - compare.view_bounds()[0] >= 1e-12
    compare.toolbar.update()
    compare.ax.set_xlim(0.4, 0.8)
    QTest.keyClick(compare.canvas, Qt.Key.Key_Right)
    assert len(compare.toolbar._nav_stack) == 2


def test_hidden_numeric_draft_eventually_publishes_current_value(compare, records):
    compare.hide()
    add(compare, records)
    compare.interval_mode.setCurrentIndex(1)
    compare.recompute()
    compare.wave_min.setLocale(QLocale("en_US"))
    line = compare.wave_min.lineEdit()
    line.setFocus()
    line.selectAll()
    QTest.keyClicks(line, "0.52")
    for _ in range(20):
        QTest.qWait(25)
        if compare.request is not None:
            break
    assert compare.request is not None
    assert compare.request.interval_um == (0.52, 0.8)
    assert compare.wave_min.value() == 0.52


def test_wrong_identity_details_bound_and_non_display_model_roles(compare, records):
    add(compare, records)
    accepted = result(compare)
    wrong = replace(accepted, series=(replace(accepted.series[0], key=records[1].key),))
    assert not compare.set_result(wrong)
    long = replace(
        accepted,
        series=(
            replace(
                accepted.series[0],
                details=MaterialDetails("Owned", (("Field", "x" * 40000),), ()),
            ),
        ),
    )
    assert compare.set_result(long)
    assert compare.details.toPlainText().endswith("[Display truncated]")
    assert len(compare.details.toPlainText()) < 32800
    model = compare.model
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DecorationRole) is None
    assert model.data(model.index(0, 1), Qt.ItemDataRole.TextAlignmentRole)
    assert (
        model.data(model.index(0, 1), int(Qt.ItemDataRole.UserRole) + 1)
        == records[0].key
    )
    assert model.headerData(-1, Qt.Orientation.Horizontal) is None
    assert (
        model.headerData(0, Qt.Orientation.Vertical, Qt.ItemDataRole.UserRole) is None
    )
