"""Retained Matplotlib material map with exact identity and native interaction."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from optiland.materials.properties import MaterialKey
from optiland_gui.widgets.plot_navigation import PlotNavigationToolbar, valid_plot_view

if TYPE_CHECKING:
    from collections.abc import Iterable

    from matplotlib.axes import Axes
    from matplotlib.backend_bases import DrawEvent, MouseEvent, ResizeEvent
    from PySide6.QtCore import QObject


@dataclass(frozen=True)
class MapPoint:
    """One finite plot coordinate attached to an exact material identity."""

    key: MaterialKey
    x: float
    y: float
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, MaterialKey) or not isinstance(self.label, str):
            raise ValueError("Map points require a material key and text label")
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("Map coordinates must be finite")


class _MapToolbar(PlotNavigationToolbar):
    # Output is published by the document's shared artifact writer. Arbitrary
    # figure edits would disconnect displayed axes from their data provenance.
    toolitems = tuple(
        item
        for item in PlotNavigationToolbar.toolitems
        if item[3] not in {"save_figure", "edit_parameters", "configure_subplots"}
    )


class GlassMap(QWidget):
    """Linked glass map: gestures request selection and never mutate the design.

    All coordinates use Matplotlib display pixels for picking, including HiDPI.
    Current-design points use diamonds, selection an open ring, and hover an
    outer halo. These shapes distinguish states independently of hue. The view
    remains stable through filtering; Reset and Locate are explicit operations.
    """

    selection_requested = Signal(object)
    export_requested = Signal()
    _PICK_RADIUS = 8.0  # Logical screen pixels, scaled to the canvas display.
    _MAX_VIEW = 1e100  # Avoid transforms with overflow-prone extreme coordinates.

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Tick precision changes during navigation. Measure the labels on each
        # draw instead of reserving a fixed margin that can clip a wider title.
        self.figure = Figure(figsize=(6, 4), dpi=100, layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setAccessibleName("Material property map")
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.setMinimumSize(220, 160)
        self.canvas.installEventFilter(self)
        self.ax = self.figure.add_subplot()
        self.toolbar = _MapToolbar(self.canvas, self)
        self.canvas.setToolTip(
            "Left-click selects a material. "
            + self.canvas.toolTip()
            + " Use the table for keyboard selection."
        )
        self.toolbar.setAccessibleName("Material map navigation")
        self.toolbar.addSeparator()
        self.toolbar.addAction("Bounds…", self._show_bounds)
        self.toolbar.addAction("Export PNG…", self.export_requested.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self._points: tuple[MapPoint, ...] = ()
        self._context: tuple[MapPoint, ...] = ()
        self._by_key: dict[MaterialKey, MapPoint] = {}
        self._selected: MaterialKey | None = None
        self._selected_point: MapPoint | None = None
        self._hovered: MaterialKey | None = None
        self._descending = True
        self._target_spec = (None, None, None, None)
        self._has_view = False
        self._hit_points: tuple[MapPoint, ...] = ()
        self._screen = np.empty((0, 2))
        self._visible = np.empty(0, dtype=bool)
        self._hit_dirty = True
        self._background = None
        self._blit_bbox = None
        self._chooser: QMenu | None = None
        self._bounds_dialog: QDialog | None = None
        self._dots = self.ax.scatter([], [], s=24, linewidths=0.5, zorder=2)
        self._diamonds = self.ax.scatter(
            [], [], s=56, marker="D", linewidths=1.3, zorder=3
        )
        (self._selection,) = self.ax.plot(
            [], [], "o", ms=12, mfc="none", mew=2.2, zorder=6, animated=True
        )
        (self._halo,) = self.ax.plot(
            [], [], "o", ms=16, mfc="none", mew=1.2, zorder=5, animated=True
        )
        self._annotation = self.ax.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            fontsize=9,
            bbox={"boxstyle": "round,pad=0.35", "alpha": 0.96},
            zorder=7,
            animated=True,
            visible=False,
            parse_math=False,
        )
        self._annotation.set_clip_on(True)
        self._annotation.set_in_layout(False)
        self._annotation.set_clip_path(self.ax.patch)
        self._annotation.get_bbox_patch().set_clip_path(self.ax.patch)
        self._target_box = Rectangle(
            (0, 0),
            0,
            0,
            fill=False,
            linewidth=1.4,
            linestyle="--",
            zorder=4,
            visible=False,
        )
        self.ax.add_patch(self._target_box)
        (self._target,) = self.ax.plot([], [], "+", ms=12, mew=1.6, zorder=4)
        self.ax.set_xlabel("V_d (d/F/C, native conditions)", wrap=True)
        self.ax.set_ylabel("n_d (native conditions)", wrap=True)
        self.ax.set_xlim(100, 10)
        self.ax.set_ylim(1.4, 2.1)
        self._connections = [
            self.canvas.mpl_connect("draw_event", self._on_draw),
            self.canvas.mpl_connect("button_press_event", self._on_click),
            self.canvas.mpl_connect("motion_notify_event", self._on_motion),
            self.canvas.mpl_connect("figure_leave_event", self._on_leave),
            self.canvas.mpl_connect("resize_event", self._on_resize),
        ]
        self.ax.callbacks.connect("xlim_changed", self._invalidate_view)
        self.ax.callbacks.connect("ylim_changed", self._invalidate_view)
        self.toolbar.push_current()
        self.update_theme("light")

    def view_bounds(self) -> tuple[float, float, float, float]:
        """Return actual plotted bounds, including descending axes."""
        return tuple(
            float(value) for value in (*self.ax.get_xlim(), *self.ax.get_ylim())
        )

    def capture_rgba(self) -> tuple[int, int, bytes]:
        """Detach the displayed image on Qt, including retained selection cues."""
        self.canvas.draw()
        # Animated overlays are normally blitted after drawing; reuse that path
        # so the export agrees with the currently visible map.
        self._draw_overlays()
        pixels = self.canvas.buffer_rgba()
        return pixels.shape[1], pixels.shape[0], bytes(pixels)

    @staticmethod
    def _validated(points: Iterable[MapPoint]) -> tuple[MapPoint, ...]:
        result = tuple(points)
        if any(not isinstance(point, MapPoint) for point in result):
            raise ValueError("Material maps require MapPoint records")
        if len({point.key for point in result}) != len(result):
            raise ValueError("Material map keys must be unique within each layer")
        return result

    def set_points(
        self,
        points: Iterable[MapPoint],
        *,
        xlabel: str = "V_d (d/F/C, native conditions)",
        ylabel: str = "n_d (native conditions)",
        descending: bool = True,
        preserve_view: bool = True,
    ) -> None:
        """Replace visible records without changing selection or a retained view."""
        points = self._validated(points)
        if self._chooser is not None:
            self._chooser.close()
        changed_axes = xlabel != self.ax.get_xlabel() or ylabel != self.ax.get_ylabel()
        self._points = points
        self._descending = bool(descending)
        self._dots.set_offsets(self._coordinates(points))
        self.ax.set_xlabel(xlabel, wrap=True)
        self.ax.set_ylabel(ylabel, wrap=True)
        self._index_points()
        self._clear_hover()
        self._sync_selection()
        if not preserve_view or not self._has_view or changed_axes:
            self.reset_view()
        else:
            low, high = sorted(self.ax.get_xlim())
            self.ax.set_xlim((high, low) if self._descending else (low, high))
            self._redraw()

    def set_context(self, points: Iterable[MapPoint]) -> None:
        """Replace current-design diamond markers without retargeting selection."""
        points = self._validated(points)
        if self._has_view and points == self._context:
            return
        self._context = points
        self._diamonds.set_offsets(self._coordinates(self._context))
        self._index_points()
        self._clear_hover()
        self._sync_selection()
        if not self._has_view:
            self.reset_view()
        else:
            self._redraw()

    @staticmethod
    def _coordinates(points: tuple[MapPoint, ...]) -> np.ndarray:
        return np.column_stack(
            (
                np.fromiter(
                    (point.x for point in points), dtype=float, count=len(points)
                ),
                np.fromiter(
                    (point.y for point in points), dtype=float, count=len(points)
                ),
            )
        )

    def _index_points(self) -> None:
        # Context may contain an already visible key; offer that identity once.
        self._by_key = {point.key: point for point in (*self._points, *self._context)}
        self._hit_points = tuple(
            sorted(
                self._by_key.values(),
                key=lambda point: (point.label.casefold(), point.key),
            )
        )
        self._hit_dirty = True

    def set_selected(self, key: MaterialKey | None) -> None:
        """Display the selected identity; filtered-out selections stay retained."""
        self._selected = key
        if self._selected_point is not None and self._selected_point.key != key:
            self._selected_point = None
        self._sync_selection()
        self._draw_overlays()

    def set_selected_point(self, point: MapPoint | None) -> None:
        """Retain a hidden selected coordinate for its ring and explicit Locate.

        This detached point is excluded from result/context counts, picking,
        overlap choices and Reset bounds. Set the selected key first. A stale
        coordinate with a different key is rejected without changing state.
        """
        if point is not None and (
            not isinstance(point, MapPoint) or point.key != self._selected
        ):
            raise ValueError("Detached point must match the selected material key")
        self._selected_point = point
        self._sync_selection()
        self._draw_overlays()

    def _sync_selection(self) -> None:
        point = self._by_key.get(self._selected) or self._selected_point
        self._selection.set_data([point.x] if point else [], [point.y] if point else [])

    def _invalidate_view(self, _axes: Axes | None = None) -> None:
        self._hit_dirty = True
        self._background = None

    def _redraw(self) -> None:
        self._invalidate_view()
        self.canvas.draw_idle()

    @classmethod
    def _limits(
        cls, values: list[float], fallback: tuple[float, float]
    ) -> tuple[float, float]:
        safe = [value for value in values if abs(value) < cls._MAX_VIEW / 2]
        if not safe:
            return fallback
        low, high = min(safe), max(safe)
        pad = max((high - low) * 0.07, max(abs(low), abs(high), 1.0) * 0.015)
        return low - pad, high + pad

    def reset_view(self) -> None:
        """Fit current results/context and reset the navigation history baseline."""
        points = tuple(self._by_key.values())
        x = self._limits([p.x for p in points], (10, 100))
        y = self._limits([p.y for p in points], (1.4, 2.1))
        self.ax.set_xlim(x[::-1] if self._descending else x)
        self.ax.set_ylim(y)
        self._has_view = bool(points)
        self.toolbar.update()
        self.toolbar.push_current()
        self._redraw()

    def locate(self, key: MaterialKey) -> bool:
        """Center a present point at the current zoom; return false if unavailable."""
        point = self._by_key.get(key)
        if point is None and key == self._selected:
            point = self._selected_point
        if point is None or max(abs(point.x), abs(point.y)) >= self._MAX_VIEW / 2:
            return False
        x, y = self.ax.get_xlim(), self.ax.get_ylim()
        width, height = abs(x[1] - x[0]), abs(y[1] - y[0])
        x = (point.x - width / 2, point.x + width / 2)
        y = (point.y - height / 2, point.y + height / 2)
        if not valid_plot_view(x, y):
            return False
        self.ax.set_xlim(x[::-1] if self._descending else x)
        self.ax.set_ylim(y)
        self.toolbar.push_current()
        self._redraw()
        return True

    def set_target(
        self,
        nd: float | None,
        vd: float | None,
        ntol: float | None = None,
        vtol: float | None = None,
    ) -> None:
        """Show a d/F/C target cross and independent rectangular tolerances.

        A missing coordinate clears the two-dimensional overlay. Bounds must be
        finite and positive when supplied. This overlay is for the default nd/Vd
        view; the parent clears it for alternative coordinate systems.
        """
        for value in (nd, vd, ntol, vtol):
            if value is not None and not math.isfinite(value):
                raise ValueError("Target coordinates and tolerances must be finite")
        if any(value is not None and value <= 0 for value in (ntol, vtol)):
            raise ValueError("Target tolerances must be positive")
        if any(
            value is not None and abs(value) >= self._MAX_VIEW / 4
            for value in (nd, vd, ntol, vtol)
        ):
            raise ValueError("Target exceeds safe plot coordinate limits")
        visible = nd is not None and vd is not None
        spec = (nd, vd, ntol, vtol) if visible else (None, None, None, None)
        if spec == self._target_spec:
            return
        self._target_spec = spec
        self._target.set_data([vd] if visible else [], [nd] if visible else [])
        rectangle = visible and ntol is not None and vtol is not None
        self._target_box.set_visible(rectangle)
        if rectangle:
            self._target_box.set_bounds(vd - vtol, nd - ntol, 2 * vtol, 2 * ntol)
        self._redraw()

    def _on_draw(self, _event: DrawEvent) -> None:
        self._hit_dirty = True
        self._refresh_hits()
        # Include antialiasing at the clip boundary: fractional axes pixels can
        # otherwise survive just beyond the integer region copied by Agg.
        self._blit_bbox = self.ax.bbox.padded(2 * self.canvas.device_pixel_ratio)
        self._background = self.canvas.copy_from_bbox(self._blit_bbox)
        self._draw_overlays()

    def _on_resize(self, _event: ResizeEvent) -> None:
        """Invalidate display coordinates; layout measures labels on redraw."""
        self._redraw()

    def _refresh_hits(self) -> None:
        if self._hit_dirty:
            with np.errstate(over="ignore", invalid="ignore"):
                self._screen = self.ax.transData.transform(
                    self._coordinates(self._hit_points)
                )
            box = self.ax.bbox
            self._visible = (
                np.isfinite(self._screen).all(axis=1)
                & (self._screen[:, 0] >= box.x0)
                & (self._screen[:, 0] <= box.x1)
                & (self._screen[:, 1] >= box.y0)
                & (self._screen[:, 1] <= box.y1)
            )
            self._hit_dirty = False

    def _hits(self, x: float, y: float) -> tuple[MapPoint, ...]:
        if (
            not math.isfinite(x)
            or not math.isfinite(y)
            or not self.ax.bbox.contains(x, y)
        ):
            return ()
        self._refresh_hits()
        radius = self._PICK_RADIUS * self.canvas.device_pixel_ratio
        with np.errstate(over="ignore", invalid="ignore"):
            delta = self._screen - (x, y)
            mask = self._visible & (np.hypot(delta[:, 0], delta[:, 1]) <= radius)
        return tuple(self._hit_points[i] for i in np.flatnonzero(mask))

    def _request_selection(self, key: MaterialKey) -> None:
        if key in self._by_key:
            self.selection_requested.emit(key)

    def _on_click(self, event: MouseEvent) -> None:
        if (
            self.toolbar.mode
            or self.toolbar.is_dragging
            or event.button != 1
            or event.inaxes is not self.ax
            or not self.canvas.widgetlock.available(self.toolbar)
        ):
            return
        hits = self._hits(event.x, event.y)
        if len(hits) == 1:
            self._request_selection(hits[0].key)
        elif hits:
            self._show_choices(hits, event.x, event.y)

    def _show_choices(self, hits: tuple[MapPoint, ...], x: float, y: float) -> None:
        if self._chooser is not None:
            self._chooser.close()
            self._chooser.deleteLater()
        menu = self._chooser = QMenu(self)
        menu.setTitle("Choose overlapping material")
        self._populate_choices(menu, hits)
        ratio = self.canvas.device_pixel_ratio
        pos = QPoint(round(x / ratio), round(self.canvas.height() - y / ratio))
        menu.popup(self.canvas.mapToGlobal(pos))

    def _populate_choices(self, menu: QMenu, hits: tuple[MapPoint, ...]) -> None:
        """Keep dense coincidences complete without eagerly creating 20k actions."""
        if menu.actions():
            return
        if len(hits) > 50:
            size = max(50, math.ceil(len(hits) / 50))
            for start in range(0, len(hits), size):
                group = hits[start : start + size]
                # Explicit parenting keeps the callback's Python wrapper alive
                # when Qt returns the submenu through its associated QAction.
                child = QMenu(f"Materials {start + 1}–{start + len(group)}", menu)
                menu.addMenu(child)
                child.aboutToShow.connect(
                    lambda group=group, child=child: self._populate_choices(
                        child, group
                    )
                )
            return
        for point in hits:
            label = (
                f"{point.label} — {point.key.catalog} / "
                f"{point.key.source_id} / {point.key.record_id}"
            )
            action = menu.addAction(label.replace("&", "&&"))
            action.setData(point.key)
            action.triggered.connect(
                lambda _checked=False, key=point.key: self._request_selection(key)
            )

    def _on_motion(self, event: MouseEvent) -> None:
        hits = (
            ()
            if self.toolbar.mode
            or self.toolbar.is_dragging
            or event.inaxes is not self.ax
            else self._hits(event.x, event.y)
        )
        point = hits[0] if hits else None
        key = point.key if point else None
        if key == self._hovered:
            return
        self._hovered = key
        self._halo.set_data([point.x] if point else [], [point.y] if point else [])
        self._annotation.set_visible(point is not None)
        if point:
            self._annotation.xy = point.x, point.y
            suffix = (
                f" (+{len(hits) - 1} overlapping; click to choose)"
                if len(hits) > 1
                else ""
            )
            self._annotation.set_text(
                f"{point.label}\n{point.key.catalog} / {point.key.source_id}{suffix}"
            )
            right = event.x > self.ax.bbox.x0 + self.ax.bbox.width * 0.6
            top = event.y > self.ax.bbox.y0 + self.ax.bbox.height * 0.75
            self._annotation.set_position((-12 if right else 12, -12 if top else 12))
            self._annotation.set_ha("right" if right else "left")
            self._annotation.set_va("top" if top else "bottom")
        self._draw_overlays()

    def _clear_hover(self) -> None:
        self._hovered = None
        self._halo.set_data([], [])
        self._annotation.set_visible(False)

    def _on_leave(self, _event: object | None) -> None:
        self._clear_hover()
        self._draw_overlays()

    def _draw_overlays(self) -> None:
        if self._background is None:
            self.canvas.draw_idle()
            return
        self.canvas.restore_region(self._background)
        for artist in (self._halo, self._selection, self._annotation):
            self.ax.draw_artist(artist)
        self.canvas.blit(self._blit_bbox)

    def set_view_bounds(
        self, xmin: float, xmax: float, ymin: float, ymax: float
    ) -> None:
        """Apply ordered finite numeric bounds without changing selected identity."""
        x, y = (xmin, xmax), (ymin, ymax)
        if xmin >= xmax or ymin >= ymax or not valid_plot_view(x, y):
            raise ValueError(
                "Use finite limits with minimum less than maximum and a nonzero span."
            )
        self.ax.set_xlim(x[::-1] if self._descending else x)
        self.ax.set_ylim(y)
        self.toolbar.push_current()
        self._redraw()

    def _show_bounds(self) -> None:
        """Open a native, locale-aware viewport editor with inline validation."""
        if self._bounds_dialog is not None:
            self._bounds_dialog.show()
            self._bounds_dialog.raise_()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Material map bounds")
        dialog.setModal(True)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        layout.addLayout(form)
        values = (*sorted(self.ax.get_xlim()), *sorted(self.ax.get_ylim()))
        labels = ("X minimum", "X maximum", "Y minimum", "Y maximum")
        fields = []
        for name, value in zip(labels, values, strict=True):
            field = QLineEdit(self.locale().toString(float(value), "g", 15), dialog)
            field.setObjectName("material-map-" + name.lower().replace(" ", "-"))
            field.setAccessibleName(name)
            form.addRow(name, field)
            fields.append(field)
        error = QLabel(dialog)
        error.setObjectName("material-map-bounds-error")
        error.setWordWrap(True)
        error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Close,
            parent=dialog,
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setDefault(True)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setAutoDefault(False)
        close_button.setDefault(False)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)

        def apply() -> None:
            parsed = [self.locale().toDouble(field.text().strip()) for field in fields]
            try:
                if not all(ok for _value, ok in parsed):
                    raise ValueError(
                        "Enter a number in each field using your "
                        "regional decimal separator."
                    )
                self.set_view_bounds(*(value for value, _ok in parsed))
            except ValueError as exc:
                error.setText(str(exc))
            else:
                error.setText("View updated. Selection is unchanged.")

        apply_button.clicked.connect(apply)
        dialog.destroyed.connect(self._bounds_closed)
        self._bounds_dialog = dialog
        dialog.open()

    def _bounds_closed(self) -> None:
        self._bounds_dialog = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self.canvas or event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return super().eventFilter(watched, event)
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self._chooser is not None:
                self._chooser.close()
            if self.toolbar.mode == "pan/zoom":
                self.toolbar.pan()
            elif self.toolbar.mode == "zoom rect":
                self.toolbar.zoom()
            self._on_leave(None)
        elif key == Qt.Key.Key_Home:
            self.reset_view()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            x, y = self.ax.get_xlim(), self.ax.get_ylim()
            factor = 1.2 if key == Qt.Key.Key_Minus else 1 / 1.2
            center_x, center_y = (x[0] + x[1]) / 2, (y[0] + y[1]) / 2
            x = tuple(center_x + (edge - center_x) * factor for edge in x)
            y = tuple(center_y + (edge - center_y) * factor for edge in y)
            if valid_plot_view(x, y):
                self.set_view_bounds(*sorted(x), *sorted(y))
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            x, y = self.ax.get_xlim(), self.ax.get_ylim()
            dx = (
                (x[1] - x[0]) * (0.1 if key == Qt.Key.Key_Right else -0.1)
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Right)
                else 0
            )
            dy = (
                (y[1] - y[0]) * (0.1 if key == Qt.Key.Key_Up else -0.1)
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down)
                else 0
            )
            moved_x, moved_y = (x[0] + dx, x[1] + dx), (y[0] + dy, y[1] + dy)
            if valid_plot_view(moved_x, moved_y):
                self.set_view_bounds(*sorted(moved_x), *sorted(moved_y))
        else:
            return super().eventFilter(watched, event)
        event.accept()
        return True

    def update_theme(self, theme: str) -> None:
        """Restyle local artists; global Matplotlib/Qt themes stay untouched."""
        dark = "dark" in theme.casefold()
        background, foreground = (
            ("#202124", "#f0f1f3") if dark else ("#ffffff", "#25272b")
        )
        dots = "#9bbce0" if dark else "#35669a"
        accent = "#ffd17d" if dark else "#8b5400"
        # Matplotlib's Qt icon engine reads the toolbar's palette rather than
        # the application's QSS background. Keep icons readable locally.
        palette = QPalette(self.toolbar.palette())
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Button):
            palette.setColor(role, QColor(background))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            palette.setColor(role, QColor(foreground))
        self.toolbar.setPalette(palette)
        self.toolbar.setAutoFillBackground(True)
        self.toolbar.finish_navigation()
        self.figure.set_facecolor(background)
        self.ax.set_facecolor(background)
        self.ax.tick_params(colors=foreground)
        for spine in self.ax.spines.values():
            spine.set_color(foreground)
        self.ax.xaxis.label.set_color(foreground)
        self.ax.yaxis.label.set_color(foreground)
        self.ax.grid(True, color=foreground, alpha=0.13, linewidth=0.6)
        self.ax.set_axisbelow(True)
        self._dots.set_facecolor(dots)
        self._dots.set_edgecolor(background)
        self._diamonds.set_facecolor(background)
        self._diamonds.set_edgecolor(accent)
        self._selection.set_color(foreground)
        self._halo.set_color(accent)
        self._annotation.set_color(foreground)
        self._annotation.get_bbox_patch().set_facecolor(background)
        self._annotation.get_bbox_patch().set_edgecolor(foreground)
        self._target.set_color(accent)
        self._target_box.set_edgecolor(accent)
        self._redraw()
