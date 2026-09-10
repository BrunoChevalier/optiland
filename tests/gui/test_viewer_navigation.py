"""A navigation gesture has one owner and preserves the view until zoom release."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from matplotlib.backend_bases import MouseButton, MouseEvent
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from optiland_gui.viewer_panel import MatplotlibViewer


@pytest.fixture
def viewer(qapp):
    widget = MatplotlibViewer(SimpleNamespace(get_optic=lambda: None))
    widget.ax.clear()
    widget.ax.plot([0, 10], [0, 10])
    widget.ax.set_xlim(0, 10)
    widget.ax.set_ylim(0, 10)
    widget.canvas.draw()
    yield widget
    widget.close()


def limits(viewer):
    return np.array([viewer.ax.get_xlim(), viewer.ax.get_ylim()])


def emit(viewer, name, pixel, button=MouseButton.LEFT):
    kwargs = {"buttons": {button}} if name == "motion_notify_event" else {}
    event = MouseEvent(name, viewer.canvas, *pixel, button=button, **kwargs)
    viewer.canvas.callbacks.process(name, event)
    return event


@pytest.mark.parametrize("start,end", [((3, 3), (7, 7)), ((7, 7), (3, 3))])
def test_rectangle_zoom_changes_limits_only_on_release(viewer, start, end):
    before = limits(viewer)
    start_pixel, end_pixel = viewer.ax.transData.transform([start, end])
    viewer.toolbar.zoom()
    emit(viewer, "motion_notify_event", start_pixel)
    cursor = viewer.canvas.cursor().shape()
    emit(viewer, "button_press_event", start_pixel)
    assert not viewer._is_panning
    emit(viewer, "motion_notify_event", end_pixel)
    np.testing.assert_array_equal(limits(viewer), before)
    emit(viewer, "button_release_event", end_pixel)
    assert not np.array_equal(limits(viewer), before)
    assert viewer.canvas.cursor().shape() == cursor
    after = limits(viewer)
    viewer.toolbar.back()
    np.testing.assert_allclose(limits(viewer), before)
    viewer.toolbar.forward()
    np.testing.assert_allclose(limits(viewer), after)


def test_toolbar_pan_does_not_also_start_custom_pan(viewer):
    viewer.toolbar.pan()
    start, end = viewer.ax.transData.transform([(3, 3), (4, 4)])
    before = limits(viewer)
    emit(viewer, "button_press_event", start)
    assert not viewer._is_panning
    emit(viewer, "motion_notify_event", end)
    emit(viewer, "button_release_event", end)
    assert not np.array_equal(limits(viewer), before)
    viewer.toolbar.back()
    np.testing.assert_allclose(limits(viewer), before)


def test_default_pan_has_history_and_stops_on_outside_release(viewer):
    before = limits(viewer)
    start, end = viewer.ax.transData.transform([(3, 3), (4, 4)])
    emit(viewer, "button_press_event", start)
    assert viewer._is_panning
    emit(viewer, "motion_notify_event", end)
    emit(viewer, "button_release_event", (-10, -10))
    assert not viewer._is_panning
    after = limits(viewer)
    assert not np.array_equal(after, before)
    emit(viewer, "motion_notify_event", start)
    np.testing.assert_array_equal(limits(viewer), after)
    viewer.toolbar.back()
    np.testing.assert_allclose(limits(viewer), before)
    viewer.toolbar.forward()
    np.testing.assert_allclose(limits(viewer), after)


def test_other_widget_lock_prevents_custom_pan(viewer):
    owner = object()
    before = limits(viewer)
    start, end = viewer.ax.transData.transform([(3, 3), (7, 7)])
    viewer.canvas.widgetlock(owner)
    try:
        emit(viewer, "button_press_event", start)
        emit(viewer, "motion_notify_event", end)
        emit(viewer, "button_release_event", end)
        assert not viewer._is_panning
        np.testing.assert_array_equal(limits(viewer), before)
    finally:
        viewer.canvas.widgetlock.release(owner)


@pytest.mark.parametrize("interrupt", ["zoom", "lock", "focus_out", "hide"])
def test_interrupted_default_drag_cannot_keep_panning(viewer, interrupt):
    start, end = viewer.ax.transData.transform([(3, 3), (7, 7)])
    before = limits(viewer)
    emit(viewer, "button_press_event", start)
    assert viewer._is_panning
    owner = object()
    if interrupt == "zoom":
        viewer.toolbar.zoom()
    elif interrupt == "lock":
        viewer.canvas.widgetlock(owner)
    else:
        event_type = QEvent.Type.FocusOut if interrupt == "focus_out" else QEvent.Type.Hide
        QApplication.sendEvent(viewer.canvas, QEvent(event_type))
    try:
        emit(viewer, "motion_notify_event", end)
        assert not viewer._is_panning
        np.testing.assert_array_equal(limits(viewer), before)
    finally:
        if interrupt == "lock":
            viewer.canvas.widgetlock.release(owner)
    emit(viewer, "button_release_event", end)


def test_click_without_drag_and_missing_coordinates_do_not_move_view(viewer):
    pixel = viewer.ax.transData.transform((3, 3))
    before = limits(viewer)
    viewer.toolbar.zoom()
    emit(viewer, "button_press_event", pixel)
    emit(viewer, "button_release_event", pixel)
    np.testing.assert_array_equal(limits(viewer), before)
    viewer.on_mouse_move_on_plot(SimpleNamespace(inaxes=viewer.ax, xdata=None, ydata=None))
    assert not viewer.cursor_coord_label.isVisible()


def test_navigation_does_not_replot_optic(viewer, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Navigation must not recalculate the optical plot")

    monkeypatch.setattr(viewer, "plot_optic", unexpected)
    start, end = viewer.ax.transData.transform([(3, 3), (7, 7)])
    viewer.toolbar.zoom()
    emit(viewer, "button_press_event", start)
    emit(viewer, "motion_notify_event", end)
    emit(viewer, "button_release_event", end)
    viewer.toolbar.back()
    viewer.toolbar.forward()


def test_native_qt_rectangle_drag(viewer, qapp):
    if qapp.platformName() in ("offscreen", "minimal"):
        pytest.skip("Requires the native Qt window platform")
    viewer.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    viewer.resize(800, 600)
    viewer.show()
    qapp.processEvents()
    viewer.canvas.draw()
    before = limits(viewer)
    start, end = viewer.ax.transData.transform([(3, 3), (7, 7)])
    ratio = viewer.canvas.device_pixel_ratio

    def qt_point(pixel):
        return QPoint(
            round(pixel[0] / ratio),
            round(viewer.canvas.height() - pixel[1] / ratio),
        )

    viewer.toolbar.zoom()
    QTest.mousePress(viewer.canvas, Qt.MouseButton.LeftButton, pos=qt_point(start))
    QTest.mouseMove(viewer.canvas, qt_point(end))
    np.testing.assert_array_equal(limits(viewer), before)
    assert not viewer._is_panning
    QTest.mouseRelease(viewer.canvas, Qt.MouseButton.LeftButton, pos=qt_point(end))
    assert not np.array_equal(limits(viewer), before)
