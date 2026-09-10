"""Row/cell hover is visual feedback, never an edit or selection operation."""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QLineEdit

from tests.gui.test_surface_interaction import make_editor, move_pointer, move_to_cell


@pytest.fixture(params=["dark", "light"])
def themed_editor(qapp, minimal_optic, request):
    previous = qapp.styleSheet()
    path = Path(__file__).parents[2] / "optiland_gui/resources/styles"
    qapp.setStyleSheet((path / f"{request.param}_theme.qss").read_text())
    editor, connector = make_editor(minimal_optic)
    yield editor, connector, request.param
    editor.close()
    editor.deleteLater()
    qapp.setStyleSheet(previous)


def sample(table, row, column):
    rect = table.visualRect(table.model().index(row, column))
    point = rect.topLeft() + QPoint(10, 5)
    image = table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(point.x() * ratio), round(point.y() * ratio))


def test_whole_row_changes_and_pointed_cell_is_lighter(themed_editor, qapp):
    editor, connector, theme = themed_editor
    table = editor.tableWidget
    move_pointer(editor.btnAddSurface, QPoint(5, 5))
    baseline = [sample(table, 1, column) for column in range(1, 7)]
    move_to_cell(editor, 1, 3)
    hovered = [sample(table, 1, column) for column in range(1, 7)]
    assert all(before != after for before, after in zip(baseline, hovered))
    assert hovered[2].lightnessF() > hovered[1].lightnessF()
    move_to_cell(editor, 1, 4)
    assert sample(table, 1, 4).lightnessF() > sample(table, 1, 3).lightnessF()
    move_to_cell(editor, 2, 4)
    assert [sample(table, 1, column) for column in range(1, 7)] == baseline
    connector.set_surface_data.assert_not_called()
    connector.opticChanged.emit.assert_not_called()


def test_embedded_type_and_header_use_same_row_state(themed_editor):
    editor, _, _ = themed_editor
    table = editor.tableWidget
    embedded = table.cellWidget(1, 0)
    move_pointer(embedded.type_edit, embedded.type_edit.rect().center())
    assert embedded.type_edit.property("ldeHoverBackground") is True
    assert editor.interaction_state.hovered_column == 0
    assert editor.hover_presentation.tint(1, 0).lightnessF() >= 0
    header = table.verticalHeader()
    move_pointer(header.viewport(), QPoint(5, header.sectionViewportPosition(1) + 10))
    assert editor.interaction_state.hovered_column is None
    assert editor.hover_presentation.tint(1, 0) == editor.hover_presentation.tint(1, 3)
    move_pointer(editor.btnAddSurface, QPoint(5, 5))
    assert embedded.type_edit.property("ldeHoverBackground") is False
    assert editor.hover_presentation.hovered_row() == -1


def test_selection_variable_and_active_editor_survive_hover(themed_editor, qapp):
    editor, connector, _ = themed_editor
    table = editor.tableWidget
    table.selectRow(1)
    item = table.item(1, 2)
    variable = QBrush(QColor(100, 150, 255, 80))
    table.blockSignals(True)
    item.setBackground(variable)
    table.blockSignals(False)
    selected = editor.interaction_state.selected_surfaces
    move_to_cell(editor, 1, 3)
    assert sample(table, 1, 3).lightnessF() > sample(table, 1, 4).lightnessF()
    assert editor.interaction_state.selected_surfaces == selected
    assert item.background() == variable
    rect = table.visualRect(table.model().index(1, 2))
    image = table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    marker = image.pixelColor(
        round((rect.x() + 3) * ratio), round((rect.y() + 8) * ratio)
    )
    assert marker.blue() > marker.red()
    index = table.model().index(1, 1)
    table.edit(index)
    qapp.processEvents()
    active = next(
        widget
        for widget in table.viewport().findChildren(QLineEdit)
        if widget.objectName() != "SurfaceTypeLineEdit"
    )
    active.setText("editing remains intact")
    focus = qapp.focusWidget()
    for row, column in ((1, 1), (2, 3), (1, 4)):
        move_to_cell(editor, row, column)
        assert active.text() == "editing remains intact"
        assert qapp.focusWidget() is focus
        assert editor.interaction_state.selected_surfaces == selected
        assert item.background() == variable
    connector.set_surface_data.assert_not_called()


def test_expanded_panel_hover_paints_source_row_only(themed_editor, qapp):
    editor, _, _ = themed_editor
    editor.toggle_properties_widget(1)
    qapp.processEvents()
    panel = editor.tableWidget.cellWidget(2, 0)
    move_pointer(panel, QPoint(15, 15))
    assert editor.hover_presentation.hovered_row() == 1
    assert editor.hover_presentation.tint(1, 3) is not None
    assert editor.hover_presentation.tint(2, 3) is None
    assert panel.property("ldeHoverBackground") is None
