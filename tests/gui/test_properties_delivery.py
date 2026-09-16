"""Properties-panel delivery contracts on the ordinary upstream editor."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionTabWidgetFrame

from optiland_gui import lens_editor
from optiland_gui.lens_editor import SurfacePropertiesWidget
from tests.gui.test_surface_properties_panels import editor as editor
from tests.gui.test_surface_properties_panels import panel


def test_invalid_toggles_and_repeated_close_leave_other_panels_alone(editor):
    editor.toggle_properties_widget(1)
    opened = panel(editor, 1)
    count = editor.tableWidget.rowCount()
    for owner in (-1, 100):
        editor.toggle_properties_widget(owner)
        editor.close_properties_widget(owner)
    editor.close_properties_widget(2)
    assert panel(editor, 1) is opened
    assert editor.tableWidget.rowCount() == count


def test_shape_edit_after_row_shift_targets_the_correct_surface(editor, qapp):
    editor.toggle_properties_widget(2)
    opened = panel(editor, 2)
    editor.toggle_properties_widget(0)
    field = opened.input_widgets["Conic X"]
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, "-0.25")
    QTest.keyClick(field, Qt.Key_Return)
    qapp.processEvents()
    assert editor.connector.get_surface_geometry_params(2)["Conic X"] == -0.25
    assert editor.connector.get_surface_geometry_params(1)["Conic X"] == 0
    assert panel(editor, 2) is opened


def test_hover_in_properties_has_owner_but_no_cell_tint(editor, qapp):
    editor.toggle_properties_widget(0)
    editor.toggle_properties_widget(2)
    opened = panel(editor, 2)
    editor.tableWidget.scrollToItem(
        editor.tableWidget.item(editor.map_surface_index_to_ui_row(2), 1)
    )
    QTest.mouseMove(opened.close_button, opened.close_button.rect().center())
    qapp.processEvents()
    editor.hover_tracker.refresh()
    state = editor.interaction_state
    assert state.hovered_surface is editor.connector.get_optic().surfaces[2]
    assert state.hovered_column is None


def test_reveal_panel_above_viewport_scrolls_to_its_top(editor, qapp):
    editor.resize(900, 220)
    editor.toggle_properties_widget(0)
    editor.toggle_properties_widget(2)
    table = editor.tableWidget
    table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
    qapp.processEvents()
    row = editor.map_surface_index_to_ui_row(0) + 1
    assert table.rowViewportPosition(row) < 0
    editor._scroll_to_properties(0)
    assert table.rowViewportPosition(row) == 0


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_close_alignment_and_theme_border_survive_resize_and_font_change(
    editor, qapp, theme
):
    previous_style = qapp.styleSheet()
    path = Path(lens_editor.__file__).parent / "resources/styles"
    qapp.setStyleSheet((path / f"{theme}_theme.qss").read_text(encoding="utf-8"))
    widget = SurfacePropertiesWidget(1, editor.connector)
    widget.show()
    try:
        for width in (650, 850):
            widget.resize(width, 180)
            for font_size in (10, 12):
                widget.setFont(QFont("Arial", font_size))
                widget.close_button.clearFocus()
                QTest.mouseMove(widget, QPoint(20, 140))
                QTest.qWait(20)
                option = QStyleOptionTabWidgetFrame()
                widget.tabs.initStyleOption(option)
                pane = widget.tabs.style().subElementRect(
                    QStyle.SE_TabWidgetTabPane, option, widget.tabs
                )
                button = widget.close_button.geometry()
                assert button.top() == widget.tabs.tabBar().geometry().top()
                assert button.right() == pane.right()
                pixels = widget.close_button.grab().toImage()
                border = "#909090" if theme == "dark" else "#6c757d"
                assert pixels.pixelColor(pixels.width() // 2, 0).name() == border
                assert (
                    pixels.pixelColor(pixels.width() - 1, pixels.height() // 2).name()
                    == border
                )
    finally:
        widget.close()
        widget.deleteLater()
        qapp.setStyleSheet(previous_style)
