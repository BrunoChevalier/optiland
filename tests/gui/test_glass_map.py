"""Native material-map gestures preserve identity, view and independent states."""

from __future__ import annotations

import math
from types import SimpleNamespace

import matplotlib
import numpy as np
import pytest
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QLineEdit

from optiland.materials.properties import MaterialKey
from optiland_gui.widgets.glass_map import GlassMap, MapPoint


def point(name="A", x=60, y=1.5, source="installed"):
    return MapPoint(MaterialKey(source, "catalog", name), x, y, name)


@pytest.fixture
def chart(qapp):
    widget = GlassMap()
    widget.resize(800, 550)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def mouse(chart, point, kind="button_press_event", button=1, step=0):
    x, y = chart.ax.transData.transform((point.x, point.y))
    return MouseEvent(kind, chart.canvas, x, y, button=button, step=step)


def test_click_requests_exact_identity_without_applying_or_clearing(chart):
    a, b = point(), point("B", 40, 1.7)
    chart.set_points((a, b))
    chart.canvas.draw()
    requested = []
    chart.selection_requested.connect(requested.append)
    chart.set_selected(a.key)
    chart._on_click(mouse(chart, b))
    assert requested == [b.key]
    assert chart._selected == a.key
    chart._on_click(mouse(chart, point("empty", 50, 1.6)))
    assert chart._selected == a.key and requested == [b.key]
    chart._on_click(mouse(chart, b, button=3))
    assert requested == [b.key]


def test_overlap_menu_lists_all_exact_qualified_identities(chart):
    first = point("SAME", source="b")
    second = point("SAME", source="a")
    chart.set_points((first, second))
    chart.canvas.draw()
    requested = []
    chart.selection_requested.connect(requested.append)
    chart._on_click(mouse(chart, first))
    actions = chart._chooser.actions()
    assert [a.data() for a in actions] == [second.key, first.key]
    assert all("catalog /" in a.text() and "SAME" in a.text() for a in actions)
    assert not requested
    actions[1].trigger()
    assert requested == [first.key]
    chart.set_points(())
    actions[0].trigger()
    assert requested == [first.key]


def test_context_diamonds_remain_separate_but_identity_is_not_duplicated(chart):
    a, context = point(), point("Current", 45, 1.7, "design:one")
    chart.set_points((a,))
    chart.set_context((a, context))
    chart.canvas.draw()
    assert len(chart._dots.get_offsets()) == 1
    assert len(chart._diamonds.get_offsets()) == 2
    assert len(chart._hits(*chart.ax.transData.transform((a.x, a.y)))) == 1
    chart.set_selected(context.key)
    assert tuple(chart._selection.get_xdata()) == (context.x,)
    assert chart.locate(context.key)


def test_unchanged_context_skips_reindex_and_redraw_after_initial_view(
    chart, monkeypatch
):
    assert not chart._has_view
    chart.ax.set_xlim(5, 0)
    chart.set_context(())
    assert chart.ax.get_xlim() == (100, 10)
    assert not chart._has_view
    a, current = point(), point("Current", 45, 1.7, "design:one")
    chart.set_points((a,))
    chart.set_context((current,))
    chart.canvas.draw()
    chart.set_selected(current.key)
    original_index, original_redraw = chart._index_points, chart._redraw
    calls = []

    def index_points():
        calls.append("index")
        original_index()

    def redraw():
        calls.append("draw")
        original_redraw()

    monkeypatch.setattr(chart, "_index_points", index_points)
    monkeypatch.setattr(chart, "_redraw", redraw)
    chart.set_context(iter((current,)))
    assert not calls
    assert tuple(chart._selection.get_xdata()) == (45,)
    changed = point("Current", 50, 1.6, "design:one")
    chart.set_context((changed,))
    assert calls == ["index", "draw"]
    assert chart._by_key[current.key] == changed
    assert tuple(chart._selection.get_xdata()) == (50,)
    np.testing.assert_array_equal(chart._diamonds.get_offsets(), [[50, 1.6]])


def test_bounds_enter_applies_and_invalid_input_stays_open_until_close(chart, qapp):
    a = point()
    chart.set_points((a,))
    chart.set_selected(a.key)
    action = next(a for a in chart.toolbar.actions() if a.text().startswith("Bounds"))
    QTest.mouseClick(chart.toolbar.widgetForAction(action), Qt.MouseButton.LeftButton)
    dialog = chart._bounds_dialog
    fields = [
        dialog.findChild(QLineEdit, "material-map-" + name)
        for name in ("x-minimum", "x-maximum", "y-minimum", "y-maximum")
    ]
    for field, value in zip(fields, (20.0, 80.0, 1.4, 1.9), strict=True):
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(field, field.locale().toString(value, "g", 8))
    QTest.keyClick(fields[-1], Qt.Key.Key_Return)
    assert dialog.isVisible()
    assert chart.ax.get_xlim() == (80, 20)
    assert chart.ax.get_ylim() == (1.4, 1.9)
    assert chart._selected == a.key
    fields[0].setFocus()
    QTest.keyClick(fields[0], Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(fields[0], "invalid")
    QTest.keyClick(fields[0], Qt.Key.Key_Return)
    assert dialog.isVisible()
    assert chart.ax.get_xlim() == (80, 20)
    assert (
        "Enter a number" in dialog.findChild(QLabel, "material-map-bounds-error").text()
    )
    QTest.keyClick(fields[0], Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert chart._bounds_dialog is None
    QTest.mouseClick(chart.toolbar.widgetForAction(action), Qt.MouseButton.LeftButton)
    dialog = chart._bounds_dialog
    buttons = dialog.findChild(QDialogButtonBox)
    QTest.mouseClick(
        buttons.button(QDialogButtonBox.StandardButton.Close), Qt.MouseButton.LeftButton
    )
    assert not dialog.isVisible()
    assert chart._selected == a.key


def test_filtering_preserves_view_and_hidden_selection_until_explicit_reset(chart):
    a, b = point(), point("B", 40, 1.7)
    chart.set_points((a, b))
    chart.ax.set_xlim(65, 55)
    chart.ax.set_ylim(1.45, 1.55)
    chart.canvas.draw()
    chart.set_selected(a.key)
    chart.set_points((b,))
    assert chart.ax.get_xlim() == (65, 55)
    assert chart.ax.get_ylim() == (1.45, 1.55)
    assert chart._selected == a.key and len(chart._selection.get_xdata()) == 0
    assert not chart.locate(a.key)
    chart.set_points((a, b))
    assert tuple(chart._selection.get_xdata()) == (a.x,)
    assert chart.locate(b.key)
    assert sum(chart.ax.get_xlim()) / 2 == b.x
    assert abs(np.diff(chart.ax.get_xlim())[0]) == 10
    chart.reset_view()
    assert chart.ax.get_xlim()[0] > a.x and chart.ax.get_xlim()[1] < b.x


def test_alternate_axes_reset_view_and_can_ascend(chart):
    chart.set_points((point(),))
    chart.ax.set_xlim(65, 55)
    chart.set_points(
        (point(x=0.5, y=0.6),),
        xlabel="Relative partial dispersion",
        ylabel="Index",
        descending=False,
    )
    assert chart.ax.get_xlim()[0] < 0.5 < chart.ax.get_xlim()[1]
    assert chart.ax.get_xlabel() == "Relative partial dispersion"


def test_hover_is_transient_and_does_not_retarget_selected_record(chart):
    a, b = point(), point("B", 40, 1.7)
    chart.set_points((a, b))
    chart.canvas.draw()
    chart.set_selected(a.key)
    chart._on_motion(mouse(chart, b, "motion_notify_event", button=None))
    assert chart._hovered == b.key and chart._selected == a.key
    assert (
        chart._annotation.get_visible()
        and "catalog / installed" in chart._annotation.get_text()
    )
    chart._on_leave(None)
    assert chart._hovered is None and not chart._annotation.get_visible()
    assert chart._selected == a.key


def test_pan_zoom_modes_suppress_selection_and_pointer_zoom(chart):
    a = point()
    chart.set_points((a,))
    chart.canvas.draw()
    requests = []
    chart.selection_requested.connect(requests.append)
    chart.toolbar.pan()
    before = chart.ax.get_xlim()
    chart._on_click(mouse(chart, a))
    chart._on_scroll(mouse(chart, a, "scroll_event", step=1))
    assert chart.ax.get_xlim() == before and not requests
    chart.toolbar.pan()
    chart.toolbar.zoom()
    chart._on_click(mouse(chart, a))
    assert not requests
    chart.toolbar.zoom()


def test_wheel_zooms_about_pointer_and_navigation_back_restores_view(chart):
    chart.set_points((point("A", 40, 1.4), point("B", 80, 1.8)))
    chart.canvas.draw()
    target = point("cursor", 50, 1.7)
    before = chart.ax.get_xlim(), chart.ax.get_ylim()
    event = mouse(chart, target, "scroll_event", step=2)
    anchor = chart.ax.transData.transform((event.xdata, event.ydata))
    chart._on_scroll(event)
    np.testing.assert_allclose(
        chart.ax.transData.transform((event.xdata, event.ydata)), anchor
    )
    assert abs(np.diff(chart.ax.get_xlim())[0]) < abs(np.diff(before[0])[0])
    chart.toolbar.back()
    np.testing.assert_allclose(chart.ax.get_xlim(), before[0])
    np.testing.assert_allclose(chart.ax.get_ylim(), before[1])


def test_zoom_guards_nonfinite_and_deep_spans(chart):
    a = point()
    chart.set_points((a,))
    chart.canvas.draw()
    for _ in range(200):
        chart._on_scroll(
            SimpleNamespace(inaxes=chart.ax, xdata=a.x, ydata=a.y, step=10)
        )
    before = chart.ax.get_xlim(), chart.ax.get_ylim()
    assert all(math.isfinite(value) for limits in before for value in limits)
    assert before[0][0] != before[0][1] and before[1][0] != before[1][1]
    for step in (float("nan"), 0):
        chart._on_scroll(
            SimpleNamespace(inaxes=chart.ax, xdata=a.x, ydata=a.y, step=step)
        )
    chart._on_scroll(
        SimpleNamespace(inaxes=chart.ax, xdata=float("inf"), ydata=a.y, step=1)
    )
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == before
    assert not chart._hits(float("nan"), 10)
    assert not chart._hits(-20, -20)


def test_hidpi_qt_click_and_nearby_pick_use_display_pixels(chart, qapp):
    a = point()
    chart.set_points((a,))
    chart.canvas._set_device_pixel_ratio(2)
    chart.canvas.draw()
    x, y = chart.ax.transData.transform((a.x, a.y))
    assert chart._hits(x + 14, y) == (a,)
    assert chart._hits(x + 18, y) == ()
    requests = []
    chart.selection_requested.connect(requests.append)
    ratio = chart.canvas.device_pixel_ratio
    qtpos = QPoint(round(x / ratio), round(chart.canvas.height() - y / ratio))
    QTest.mouseClick(chart.canvas, Qt.MouseButton.LeftButton, pos=qtpos)
    qapp.processEvents()
    assert requests == [a.key]


def test_local_theme_changes_and_retained_artist_identity(chart):
    matplotlib.get_backend()  # Resolve Matplotlib's lazy backend sentinel first.
    before = matplotlib.rcParams.copy()
    artists = chart._dots, chart._diamonds, chart._selection, chart._halo
    chart.set_points((point(),))
    chart.canvas.draw()
    chart.update_theme("dark")
    chart.canvas.draw()
    dark = chart.ax.get_facecolor()
    chart.update_theme("light")
    chart.set_points((point("B", 40, 1.7),))
    assert (chart._dots, chart._diamonds, chart._selection, chart._halo) == artists
    assert chart.ax.get_facecolor() != dark
    assert dict(matplotlib.rcParams) == dict(before)
    assert (
        chart._selection.get_fillstyle() == "full"
        and chart._selection.get_markerfacecolor() == "none"
    )
    assert chart._halo.get_markersize() > chart._selection.get_markersize()


def test_target_has_independent_rectangular_tolerances_and_can_be_cleared(chart):
    chart.set_target(1.6, 60, 0.01, 5)
    box = chart._target_box
    assert box.get_visible()
    np.testing.assert_allclose(
        (box.get_x(), box.get_y(), box.get_width(), box.get_height()),
        (55, 1.59, 10, 0.02),
    )
    chart.set_target(None, None)
    assert not box.get_visible() and len(chart._target.get_xdata()) == 0
    chart.set_target(1.6, 60)
    assert not box.get_visible() and len(chart._target.get_xdata()) == 1
    for args in ((1.5, float("nan")), (1.5, 60, -1, 3), (1e308, 60)):
        with pytest.raises(ValueError):
            chart.set_target(*args)


def test_invalid_map_inputs_are_atomic_and_empty_view_is_finite(chart):
    a = point()
    chart.set_points((a,))
    with pytest.raises(ValueError, match="unique"):
        chart.set_points((a, a))
    with pytest.raises(ValueError, match="MapPoint"):
        chart.set_context(("not a point",))
    with pytest.raises(ValueError, match="finite"):
        point(x=float("nan"))
    with pytest.raises(ValueError, match="key"):
        MapPoint("not a key", 1, 1, "label")
    assert chart._points == (a,)
    chart.set_points((), preserve_view=False)
    assert all(math.isfinite(v) for v in (*chart.ax.get_xlim(), *chart.ax.get_ylim()))
    chart.set_context((a,))
    assert chart.ax.get_xlim()[1] < a.x < chart.ax.get_xlim()[0]


def test_hit_cache_rebuilds_after_navigation_and_does_not_pick_offscreen_points(chart):
    a, b = point(), point("B", 40, 1.7)
    chart.set_points((a, b))
    chart.canvas.draw()
    old = chart._screen.copy()
    chart.ax.set_xlim(61, 59)
    chart.ax.set_ylim(1.49, 1.51)
    position = chart.ax.transData.transform((a.x, a.y))
    assert chart._hits(*position) == (a,)
    assert not np.allclose(chart._screen, old)
    assert chart._hits(*chart.ax.transData.transform((b.x, b.y))) == ()


def test_repeated_hover_and_empty_hover_recover_without_selection(chart):
    a = point("Untrusted $\\invalid{ label")
    chart.set_points((a,))
    chart.set_selected(a.key)  # Selection before the first data draw is safe.
    chart.canvas.draw()
    event = mouse(chart, a, "motion_notify_event", button=None)
    chart._on_motion(event)
    chart._on_motion(event)
    assert chart._annotation.get_text().startswith(a.label)
    assert chart._annotation.get_parse_math() is False
    chart.canvas.draw()
    chart._on_motion(SimpleNamespace(inaxes=None))
    assert chart._hovered is None
    assert not chart._annotation.get_visible()
    assert chart._selected == a.key


def test_reopening_overlap_menu_replaces_choices_and_deep_locate_is_guarded(chart):
    a, b = point("A"), point("B")
    chart.set_points((a, b))
    chart.canvas.draw()
    chart._on_click(mouse(chart, a))
    first = chart._chooser
    chart._on_click(mouse(chart, a))
    assert chart._chooser is not first
    assert len(chart._chooser.actions()) == 2
    chart.ax.set_xlim(1, 1 + 1e-13)
    assert not chart.locate(a.key)


def test_dense_overlap_chooser_is_lazy_complete_and_keyboard_navigable(chart):
    points = tuple(point(f"Record {i:04}") for i in range(125))
    chart.set_points(points)
    chart.canvas.draw()
    chart._on_click(mouse(chart, points[0]))
    menu = chart._chooser
    assert len(menu.actions()) == 3
    assert all(action.menu() is not None for action in menu.actions())
    assert all(not action.menu().actions() for action in menu.actions())
    choices = []
    for action in menu.actions():
        child = action.menu()
        child.aboutToShow.emit()
        child.aboutToShow.emit()  # Reopening retains one action per identity.
        choices.extend(child.actions())
    assert [action.data() for action in choices] == [point.key for point in points]
    requests = []
    chart.selection_requested.connect(requests.append)
    choices[-1].trigger()
    assert requests == [points[-1].key]


def test_detached_selected_point_can_be_located_without_polluting_results(chart):
    visible, hidden = point("visible", 45, 1.7), point("selected", 90, 1.4)
    chart.set_points((visible,))
    chart.set_selected(hidden.key)
    before = chart.ax.get_xlim(), chart.ax.get_ylim()
    chart.set_selected_point(hidden)
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == before
    assert len(chart._points) == 1 and not chart._context
    assert len(chart._hit_points) == 1
    assert tuple(chart._selection.get_xdata()) == (hidden.x,)
    assert chart.locate(hidden.key)
    assert sum(chart.ax.get_xlim()) / 2 == hidden.x
    assert sum(chart.ax.get_ylim()) / 2 == hidden.y
    np.testing.assert_allclose(
        abs(np.diff(chart.ax.get_xlim())), abs(np.diff(before[0]))
    )
    chart.canvas.draw()
    assert chart._hits(*chart.ax.transData.transform((hidden.x, hidden.y))) == ()
    chart.reset_view()
    assert sum(chart.ax.get_xlim()) / 2 == visible.x
    with pytest.raises(ValueError, match="match"):
        chart.set_selected_point(visible)
    chart.set_selected(visible.key)
    assert chart._selected_point is None
    chart.set_selected_point(None)
    assert tuple(chart._selection.get_xdata()) == (visible.x,)


def test_two_view_actions_have_two_distinct_back_and_forward_steps(chart):
    a, b = point(), point("B", 40, 1.7)
    chart.set_points((a, b))
    chart.canvas.draw()
    baseline = chart.ax.get_xlim(), chart.ax.get_ylim()
    chart._on_scroll(mouse(chart, a, "scroll_event", step=1))
    zoomed = chart.ax.get_xlim(), chart.ax.get_ylim()
    chart.locate(b.key)
    located = chart.ax.get_xlim(), chart.ax.get_ylim()
    assert baseline != zoomed != located
    chart.toolbar.back()
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == zoomed
    chart.toolbar.back()
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == baseline
    chart.toolbar.forward()
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == zoomed
    chart.toolbar.forward()
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == located


def test_bounds_editor_uses_locale_and_rejects_bad_values_without_mutating(chart, qapp):
    a = point()
    chart.set_points((a,))
    chart.set_selected(a.key)
    chart.setLocale(QLocale("nl_BE"))
    chart._show_bounds()
    dialog = chart._bounds_dialog
    chart._show_bounds()
    assert chart._bounds_dialog is dialog
    fields = [
        dialog.findChild(QLineEdit, "material-map-" + name)
        for name in ("x-minimum", "x-maximum", "y-minimum", "y-maximum")
    ]
    button = dialog.findChild(QDialogButtonBox).button(
        QDialogButtonBox.StandardButton.Apply
    )
    before = chart.ax.get_xlim(), chart.ax.get_ylim()
    fields[0].setText("not a number")
    button.click()
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == before
    error = dialog.findChild(QLabel, "material-map-bounds-error")
    assert "regional decimal" in error.text()
    for field, text in zip(fields, ("70", "40", "1,4", "1,8"), strict=True):
        field.setText(text)
    button.click()
    assert "minimum less than maximum" in error.text()
    fields[0].setText("40")
    fields[1].setText("70")
    button.click()
    assert chart.ax.get_xlim() == (70, 40) and chart.ax.get_ylim() == (1.4, 1.8)
    assert chart._selected == a.key and "Selection is unchanged" in error.text()
    dialog.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert chart._bounds_dialog is None
    for bounds in (
        (70, 40, 1, 2),
        (40, 70, 2, 1),
        (40, float("inf"), 1, 2),
        (40, 70, 1, 1 + 1e-14),
    ):
        with pytest.raises(ValueError):
            chart.set_view_bounds(*bounds)


def test_keyboard_navigation_keeps_selection_and_escape_cancels_tools(chart):
    a = point()
    chart.set_points((a,))
    chart.set_selected(a.key)
    chart.canvas.draw()
    initial = chart.ax.get_xlim(), chart.ax.get_ylim()
    QTest.keyClick(chart.canvas, Qt.Key.Key_Plus)
    assert abs(np.diff(chart.ax.get_xlim())[0]) < abs(np.diff(initial[0])[0])
    QTest.keyClick(chart.canvas, Qt.Key.Key_Minus)
    np.testing.assert_allclose(chart.ax.get_xlim(), initial[0])
    before = chart.ax.get_xlim(), chart.ax.get_ylim()
    QTest.keyClick(chart.canvas, Qt.Key.Key_Left)
    assert sum(chart.ax.get_xlim()) > sum(before[0])  # Descending Abbe axis.
    QTest.keyClick(chart.canvas, Qt.Key.Key_Right)
    np.testing.assert_allclose(chart.ax.get_xlim(), before[0])
    QTest.keyClick(chart.canvas, Qt.Key.Key_Up)
    assert sum(chart.ax.get_ylim()) > sum(before[1])
    QTest.keyClick(chart.canvas, Qt.Key.Key_Down)
    np.testing.assert_allclose(chart.ax.get_ylim(), before[1])
    QTest.keyClick(chart.canvas, Qt.Key.Key_Home)
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == initial
    chart.toolbar.pan()
    QTest.keyClick(chart.canvas, Qt.Key.Key_Escape)
    assert not chart.toolbar.mode
    chart.toolbar.zoom()
    QTest.keyClick(chart.canvas, Qt.Key.Key_Escape)
    assert not chart.toolbar.mode and chart._selected == a.key
    QTest.keyClick(chart.canvas, Qt.Key.Key_A)
    QTest.keyClick(chart.canvas, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
    assert (chart.ax.get_xlim(), chart.ax.get_ylim()) == initial


@pytest.mark.parametrize("width,height", [(620, 270), (400, 250)])
def test_compact_resize_reserves_axis_label_space(chart, qapp, width, height):
    chart.resize(width, height)
    chart.set_points((point(), point("B", 40, 1.7)))
    qapp.processEvents()
    chart.canvas.draw()
    renderer = chart.canvas.get_renderer()
    for label in (chart.ax.xaxis.label, chart.ax.yaxis.label):
        box = label.get_window_extent(renderer)
        assert box.x0 >= 0 and box.y0 >= 0
        assert box.x1 <= chart.figure.bbox.width and box.y1 <= chart.figure.bbox.height


def test_dark_toolbar_icons_follow_local_palette_without_global_change(chart, qapp):
    original = qapp.palette()
    levels = []
    for theme in ("light", "dark"):
        chart.update_theme(theme)
        qapp.processEvents()
        image = chart.toolbar.actions()[0].icon().pixmap(24, 24).toImage()
        colors = [
            image.pixelColor(x, y).lightness()
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 200
        ]
        levels.append(sum(colors) / len(colors))
    assert levels[0] < 80 and levels[1] > 180
    assert qapp.palette() == original


def test_long_hover_label_leaves_no_pixels_outside_retained_blit_region(chart, qapp):
    a = point("LONG NAME " * 50)
    chart.resize(400, 300)
    chart.set_points((a,))
    qapp.processEvents()
    chart.canvas.draw()
    before = np.asarray(chart.canvas.buffer_rgba()).copy()
    chart._on_motion(mouse(chart, a, "motion_notify_event", button=None))
    assert not np.array_equal(np.asarray(chart.canvas.buffer_rgba()), before)
    chart._on_leave(None)
    np.testing.assert_array_equal(np.asarray(chart.canvas.buffer_rgba()), before)


def test_keyboard_guarded_limits_and_escape_after_overlap_preserve_selection(chart):
    a, b = point(), point("B")
    chart.set_points((a, b))
    chart.set_selected(a.key)
    chart.canvas.draw()
    chart._on_click(mouse(chart, a))
    QTest.keyClick(chart.canvas, Qt.Key.Key_Escape)
    assert not chart._chooser.isVisible()
    assert chart._selected == a.key
    chart.ax.set_xlim(1 + 1.1e-12, 1)
    before = chart.ax.get_xlim()
    QTest.keyClick(chart.canvas, Qt.Key.Key_Plus)
    assert chart.ax.get_xlim() == before
    chart.ax.set_xlim(0.99e100, 0.5e100)
    before = chart.ax.get_xlim()
    QTest.keyClick(chart.canvas, Qt.Key.Key_Left)
    assert chart.ax.get_xlim() == before
