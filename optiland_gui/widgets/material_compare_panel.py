"""Native, identity-preserving material shortlist and spectral comparison views."""

from __future__ import annotations

import html
import math
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QLocale,
    QModelIndex,
    QSignalBlocker,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from optiland.materials.comparison import SpectralRequest
from optiland.materials.properties import MaterialKey
from optiland_gui.models.materials_model import (
    MaterialsSortProxyModel,
    MaterialsTableModel,
)
from optiland_gui.widgets.plot_navigation import PlotNavigationToolbar

if TYPE_CHECKING:
    from collections.abc import Mapping

    from optiland.materials.comparison import NearbyResult, SpectralResult
    from optiland.materials.properties import MaterialSummary, SourceStamp


def _tooltip(text: str) -> str:
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


def _identity(key: MaterialKey) -> str:
    return f"Source: {key.source_id}\nCatalog: {key.catalog}\nRecord: {key.record_id}"


@dataclass(frozen=True)
class ComparisonPin:
    """Persist exact identity and fallback labels, never an optical payload."""

    key: MaterialKey
    display_name: str
    source_label: str = ""

    def __post_init__(self):
        if not isinstance(self.key, MaterialKey) or not all(
            isinstance(value, str) for value in (self.display_name, self.source_label)
        ):
            raise ValueError("Comparison pins require an exact key and text labels")


class _CompareToolbar(PlotNavigationToolbar):
    # All file publication belongs to the shared artifact owner. Figure editing
    # would also let the displayed quantity diverge from its immutable result.
    toolitems = tuple(
        item
        for item in PlotNavigationToolbar.toolitems
        if item[3] not in {"save_figure", "edit_parameters", "configure_subplots"}
    )


class _SpectralTableModel(QAbstractTableModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self._labels = {}
        self._locale = QLocale()

    def set_result(self, result, labels=None):
        self.beginResetModel()
        self.result = result
        self._labels = dict(labels or {})
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None):
        return (
            0
            if (parent is not None and parent.isValid()) or self.result is None
            else len(self.result.wavelengths_um)
        )

    def columnCount(self, parent: QModelIndex | None = None):
        return (
            0
            if parent is not None and parent.isValid()
            else 1 + (len(self.result.series) if self.result else 0)
        )

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (
            self.result is None
            or not index.isValid()
            or not 0 <= index.row() < self.rowCount()
            or not 0 <= index.column() < self.columnCount()
        ):
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if index.column() == 0:
            wavelength = self.result.wavelengths_um[index.row()]
            if role == Qt.ItemDataRole.DisplayRole:
                return self._locale.toString(wavelength, "g", 10)
            if role == Qt.ItemDataRole.UserRole:
                return wavelength
            return None
        series = self.result.series[index.column() - 1]
        cell = series.values[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                self._locale.toString(cell.value, "g", 10)
                if cell.value is not None
                else cell.status.title()
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return _tooltip(_identity(series.key) + "\n" + (cell.reason or cell.status))
        if role == Qt.ItemDataRole.UserRole:
            return cell.value
        if role == int(Qt.ItemDataRole.UserRole) + 1:
            return series.key
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Vertical:
            return section + 1 if role == Qt.ItemDataRole.DisplayRole else None
        if not 0 <= section < self.columnCount():
            return None
        if section == 0:
            return "Wavelength (µm)" if role == Qt.ItemDataRole.DisplayRole else None
        series = self.result.series[section - 1]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{section}. {self._labels.get(series.key, series.display_name)}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return _tooltip(_identity(series.key))
        return None


class MaterialComparePanel(QWidget):
    """Four ordered pins with one immutable result for plot and numeric table.

    This widget never resolves a name, evaluates optics, applies a material, or
    writes files. Its owner resolves exact source data and validates worker
    generations before calling ``set_result``. The widget additionally rejects
    a result whose keys or settings no longer match the current request.
    """

    pins_changed = Signal(object)
    request_changed = Signal(object)
    selection_requested = Signal(object)
    nearby_requested = Signal(object, float, float)
    export_requested = Signal(str)
    message = Signal(str)
    LINE_STYLES = ("-", "--", "-.", ":")
    MARKERS = ("o", "s", "^", "D")
    LIGHT_COLORS = ("#0072b2", "#a85c00", "#7a42a8", "#2f6b59")
    DARK_COLORS = ("#80bfff", "#ffc878", "#d1a3ff", "#97d4c1")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._pins: tuple[ComparisonPin, ...] = ()
        self._records: dict[MaterialKey, MaterialSummary] = {}
        self._stamps: dict[str, SourceStamp] = {}
        self._reasons: dict[MaterialKey, str] = {}
        self._request = None
        self._result = None
        self._nearby = None
        self._nearby_request = None
        self._theme = "light"
        self._closed = False
        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.setInterval(150)
        self._request_timer.timeout.connect(self.recompute)
        self._init_ui()
        self.update_theme("light")
        self._render_pins()

    @property
    def pins(self):
        return self._pins

    @property
    def pin_keys(self):
        return tuple(pin.key for pin in self._pins)

    @property
    def request(self):
        return self._request

    @property
    def result(self):
        return self._result

    @staticmethod
    def _label(text, parent):
        label = QLabel(text, parent)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _number(name, value, minimum, maximum, parent):
        control = QDoubleSpinBox(parent)
        control.setAccessibleName(name)
        control.setDecimals(8)
        control.setRange(minimum, maximum)
        control.setValue(value)
        control.setKeyboardTracking(False)
        return control

    @staticmethod
    def _interpret_numbers(controls):
        """Commit acceptable native drafts before reading their numeric values."""
        if any(not control.hasAcceptableInput() for control in controls):
            return False
        # With keyboard tracking disabled, text and value intentionally differ
        # until commit. Block valueChanged so committing a valid draft does not
        # recursively revoke the request currently being constructed.
        with ExitStack() as stack:
            for control in controls:
                stack.enter_context(QSignalBlocker(control))
                control.interpretText()
        return True

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.pin_table = QTableWidget(0, 5, self)
        self.pin_table.setAccessibleName("Ordered comparison pins")
        self.pin_table.setHorizontalHeaderLabels(
            ["Pin", "Material", "Catalog", "Source", "Availability"]
        )
        self.pin_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pin_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.pin_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pin_table.verticalHeader().hide()
        self.pin_table.setMaximumHeight(150)
        self.pin_table.setMinimumHeight(85)
        self.pin_table.horizontalHeader().setStretchLastSection(False)
        self.pin_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.pin_table.setColumnWidth(0, 40)
        self.pin_table.setColumnWidth(3, 150)
        self.pin_table.setColumnWidth(4, 140)
        layout.addWidget(self.pin_table)
        actions = QHBoxLayout()
        self.remove_button = QPushButton("Remove pin", self)
        self.up_button = QPushButton("Move up", self)
        self.down_button = QPushButton("Move down", self)
        self.inspect_button = QPushButton("Inspect in Browse", self)
        for button in (
            self.remove_button,
            self.up_button,
            self.down_button,
            self.inspect_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.remove_button.clicked.connect(self.remove_current)
        self.up_button.clicked.connect(lambda: self.move_current(-1))
        self.down_button.clicked.connect(lambda: self.move_current(1))
        self.inspect_button.clicked.connect(self.inspect_current)
        self.pin_table.currentCellChanged.connect(self._pin_selected)

        controls = QWidget(self)
        form = QFormLayout(controls)
        form.setContentsMargins(0, 0, 0, 0)
        self.quantity = QComboBox(controls)
        self.quantity.addItem("Refractive index n(λ)", "n")
        self.quantity.addItem("Internal transmission", "transmission")
        self.quantity.setAccessibleName("Comparison quantity")
        self.interval_mode = QComboBox(controls)
        self.interval_mode.addItem("Common declared support", "common")
        self.interval_mode.addItem("Explicit interval", "explicit")
        self.interval_mode.setAccessibleName("Comparison interval mode")
        row = QHBoxLayout()
        row.addWidget(self.quantity)
        row.addWidget(self.interval_mode)
        form.addRow("Quantity / interval", row)
        self.wave_min = self._number("Comparison from (µm)", 0.4, 1e-8, 1e5, controls)
        self.wave_max = self._number("Comparison to (µm)", 0.8, 1e-8, 1e5, controls)
        self.thickness = self._number(
            "Transmission thickness (mm)", 10, 0, 1e6, controls
        )
        self.samples = QSpinBox(controls)
        self.samples.setRange(2, 2001)
        self.samples.setValue(201)
        self.samples.setAccessibleName("Spectral sample count")
        row = QHBoxLayout()
        for title, control in (
            ("From (µm)", self.wave_min),
            ("To (µm)", self.wave_max),
            ("Thickness (mm)", self.thickness),
            ("Samples", self.samples),
        ):
            row.addWidget(self._label(title, controls))
            row.addWidget(control)
        form.addRow(row)
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setWidget(controls)
        area.setMinimumHeight(90)
        area.setMaximumHeight(105)
        layout.addWidget(area)
        self.recompute_button = QPushButton("Recompute", self)
        self.csv_button = QPushButton("Export samples CSV…", self)
        self.png_button = QPushButton("Export plot PNG…", self)
        actions = QHBoxLayout()
        for button in (self.recompute_button, self.csv_button, self.png_button):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.recompute_button.clicked.connect(self.recompute)
        self.csv_button.clicked.connect(lambda: self.export_requested.emit("csv"))
        self.png_button.clicked.connect(lambda: self.export_requested.emit("png"))
        for control in (self.quantity, self.interval_mode):
            control.currentIndexChanged.connect(self._settings_changed)
        for control in (self.wave_min, self.wave_max, self.thickness, self.samples):
            control.valueChanged.connect(self._settings_changed)
            control.lineEdit().textEdited.connect(self._settings_changed)

        self.status = self._label("Pin up to four materials from Browse.", self)
        self.status.setAccessibleName("Comparison status")
        status_area = QScrollArea(self)
        status_area.setWidgetResizable(True)
        status_area.setWidget(self.status)
        status_area.setMinimumHeight(38)
        status_area.setMaximumHeight(90)
        layout.addWidget(status_area)
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.plot = QWidget(self.splitter)
        plot_layout = QVBoxLayout(self.plot)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.figure = Figure(figsize=(6, 3), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setAccessibleName("Material comparison plot")
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.setMinimumSize(230, 150)
        self.canvas.installEventFilter(self)
        self.ax = self.figure.add_subplot()
        self.toolbar = _CompareToolbar(self.canvas, self)
        self.toolbar.setAccessibleName("Comparison plot navigation")
        plot_layout.addWidget(self.toolbar)
        plot_layout.addWidget(self.canvas, 1)
        self.canvas.mpl_connect("resize_event", self._resize_plot)
        self.splitter.addWidget(self.plot)
        self.data_tabs = QTabWidget(self.splitter)
        self.expand_data = QToolButton(self.data_tabs)
        self.expand_data.setText("Expand data")
        self.expand_data.setAccessibleName("Expand comparison data or restore plot")
        self.expand_data.setCheckable(True)
        self.expand_data.toggled.connect(self._expand_data)
        self.data_tabs.setCornerWidget(self.expand_data)
        self.table = QTableView(self.data_tabs)
        self.table.setAccessibleName("Spectral samples equivalent to plotted curves")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.installEventFilter(self)
        self.model = _SpectralTableModel(self)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setDefaultSectionSize(160)
        self.data_tabs.addTab(self.table, "Spectral samples")
        self.details = QPlainTextEdit(self.data_tabs)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected pin formula and source details")
        self.data_tabs.addTab(self.details, "Details")
        self._init_nearby()
        self.splitter.addWidget(self.data_tabs)
        self.splitter.setSizes([360, 180])
        layout.addWidget(self.splitter, 1)
        self.csv_button.setEnabled(False)
        self.png_button.setEnabled(False)
        self._update_controls()

    def _expand_data(self, expanded):
        if expanded:
            self._plot_sizes = self.splitter.sizes()
            focused = QApplication.focusWidget()
            if focused is not None and self.plot.isAncestorOf(focused):
                self.data_tabs.currentWidget().setFocus(Qt.FocusReason.OtherFocusReason)
        self.plot.setVisible(not expanded)
        if not expanded:
            self.splitter.setSizes(self._plot_sizes)
        self.expand_data.setText("Show plot" if expanded else "Expand data")

    def _init_nearby(self):
        page = QWidget(self.data_tabs)
        layout = QVBoxLayout(page)
        layout.addWidget(
            self._label(
                "Nearby alternatives across loaded sources. Distance is geometric "
                "similarity in n_d/V_d, not optical merit.",
                page,
            )
        )
        controls = QWidget(page)
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        self.nd_scale = self._number("Nearby n_d scale", 0.01, 1e-8, 10, controls)
        self.vd_scale = self._number("Nearby V_d scale", 1, 1e-8, 1e6, controls)
        for label, control in (
            ("n_d scale", self.nd_scale),
            ("V_d scale", self.vd_scale),
        ):
            row.addWidget(self._label(label, controls))
            row.addWidget(control)
        self.nearby_button = QPushButton("Find nearby alternatives", controls)
        row.addWidget(self.nearby_button)
        area = QScrollArea(page)
        area.setWidgetResizable(True)
        area.setWidget(controls)
        area.setMinimumHeight(55)
        area.setMaximumHeight(70)
        layout.addWidget(area)
        self.nearby_status = self._label("Select an available pin to search.", page)
        layout.addWidget(self.nearby_status)
        self.nearby_table = QTableView(page)
        self.nearby_table.setAccessibleName("Nearby qualified alternatives")
        self.nearby_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.nearby_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.nearby_model = MaterialsTableModel(self)
        self.nearby_proxy = MaterialsSortProxyModel(self)
        self.nearby_proxy.setSourceModel(self.nearby_model)
        self.nearby_table.setModel(self.nearby_proxy)
        self.nearby_table.setSortingEnabled(True)
        self.nearby_table.sortByColumn(7, Qt.SortOrder.AscendingOrder)
        layout.addWidget(self.nearby_table)
        self.nearby_inspect = QPushButton("Inspect alternative in Browse", page)
        layout.addWidget(self.nearby_inspect)
        self.nearby_inspect.setEnabled(False)
        self.nearby_table.selectionModel().currentChanged.connect(
            lambda *args: self.nearby_inspect.setEnabled(
                self.nearby_table.currentIndex().isValid()
            )
        )
        self.nearby_button.clicked.connect(self.find_nearby)
        self.nearby_inspect.clicked.connect(self.inspect_nearby)
        for control in (self.nd_scale, self.vd_scale):
            control.valueChanged.connect(self._clear_nearby)
            control.lineEdit().textEdited.connect(self._clear_nearby)
        # A hidden tab also contributes its minimum size to the parent dock.
        # Keep the complete nearby controls reachable by scrolling when the
        # splitter is short, instead of forcing every other tab to be tall.
        area = QScrollArea(self.data_tabs)
        area.setWidgetResizable(True)
        area.setWidget(page)
        self.data_tabs.addTab(area, "Nearby alternatives")

    def pin(self, summary, stamp, *, source_label="") -> bool:
        if self._closed:
            return False
        if stamp.source_id != summary.key.source_id:
            raise ValueError("The pin's source stamp must match its exact key")
        if summary.key in self.pin_keys:
            self.status.setText("This exact material is already pinned.")
            self.message.emit(self.status.text())
            return False
        if len(self._pins) == 4:
            self.status.setText(
                "Four materials are pinned. Remove one before adding another."
            )
            self.message.emit(self.status.text())
            return False
        self._pins += (ComparisonPin(summary.key, summary.display_name, source_label),)
        self._records[summary.key] = summary
        self._stamps[stamp.source_id] = stamp
        self._pins_updated(summary.key)
        return True

    def set_pins(self, pins):
        pins = tuple(pins)
        if len(pins) > 4 or any(not isinstance(pin, ComparisonPin) for pin in pins):
            raise ValueError("At most four ComparisonPin records are supported")
        if len({pin.key for pin in pins}) != len(pins):
            raise ValueError("Comparison pins must have unique exact keys")
        if self._closed:
            return
        self._pins = pins
        self._records.clear()
        self._stamps.clear()
        self._reasons.clear()
        self._pins_updated(pins[0].key if pins else None)

    def refresh_pins(
        self,
        records: Mapping[MaterialKey, MaterialSummary],
        stamps: Mapping[str, SourceStamp],
        reasons: Mapping[MaterialKey, str] | None = None,
        *,
        source_labels: Mapping[str, str] | None = None,
    ):
        if self._closed:
            return
        resolved = {key: records[key] for key in self.pin_keys if key in records}
        if any(record.key != key for key, record in resolved.items()) or any(
            stamp.source_id != source_id for source_id, stamp in stamps.items()
        ):
            raise ValueError("Resolved pins must preserve exact source identities")
        current_stamps = {
            key.source_id: stamps[key.source_id]
            for key in resolved
            if key.source_id in stamps
        }
        resolved = {
            key: value
            for key, value in resolved.items()
            if key.source_id in current_stamps
        }
        changed = (
            resolved.keys() != self._records.keys()
            or current_stamps != self._stamps
            or any(
                record is not self._records.get(key) for key, record in resolved.items()
            )
        )
        self._records, self._stamps = resolved, current_stamps
        self._reasons = dict(reasons or {})
        previous_pins = self._pins
        self._pins = tuple(
            ComparisonPin(
                pin.key,
                resolved[pin.key].display_name
                if pin.key in resolved
                else pin.display_name,
                (source_labels or {}).get(pin.key.source_id, pin.source_label),
            )
            for pin in self._pins
        )
        self._render_pins(self.current_key())
        if self._pins != previous_pins:
            self.pins_changed.emit(self._pins)
        if changed:
            self._settings_changed()

    def current_key(self):
        row = self.pin_table.currentRow()
        return self._pins[row].key if 0 <= row < len(self._pins) else None

    def remove_current(self):
        key = self.current_key()
        if key is not None:
            self._pins = tuple(pin for pin in self._pins if pin.key != key)
            self._records.pop(key, None)
            self._reasons.pop(key, None)
            self._pins_updated(self._pins[0].key if self._pins else None)

    def move_current(self, offset):
        row = self.pin_table.currentRow()
        if not 0 <= row < len(self._pins) or not 0 <= row + offset < len(self._pins):
            return
        pins = list(self._pins)
        pins[row], pins[row + offset] = pins[row + offset], pins[row]
        key = self.current_key()
        self._pins = tuple(pins)
        self._pins_updated(key)

    def _pins_updated(self, selected):
        self._render_pins(selected)
        self.pins_changed.emit(self._pins)
        self._settings_changed()

    def _render_pins(self, selected=None):
        blocker = QSignalBlocker(self.pin_table)
        self.pin_table.setRowCount(len(self._pins))
        for row, pin in enumerate(self._pins):
            available = pin.key in self._records
            reason = (
                "Available"
                if available
                else self._reasons.get(pin.key, "Source unavailable; pin retained")
            )
            values = (
                str(row + 1),
                pin.display_name,
                pin.key.catalog,
                pin.source_label or pin.key.source_id,
                reason,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, pin.key)
                item.setToolTip(_tooltip(_identity(pin.key) + "\n" + reason))
                self.pin_table.setItem(row, column, item)
            if pin.key == selected:
                self.pin_table.setCurrentCell(row, 0)
        del blocker
        self._pin_selected()

    def _pin_selected(self, *args):
        row = self.pin_table.currentRow()
        key = self.current_key()
        self.remove_button.setEnabled(key is not None and not self._closed)
        self.up_button.setEnabled(row > 0 and not self._closed)
        self.down_button.setEnabled(0 <= row < len(self._pins) - 1 and not self._closed)
        self.inspect_button.setEnabled(key in self._records and not self._closed)
        self.nearby_button.setEnabled(key in self._records and not self._closed)
        self._clear_nearby()
        self._show_details()

    def inspect_current(self):
        key = self.current_key()
        if key in self._records and not self._closed:
            self.selection_requested.emit(key)

    def _update_controls(self):
        explicit = self.interval_mode.currentData() == "explicit"
        self.wave_min.setEnabled(explicit)
        self.wave_max.setEnabled(explicit)
        self.thickness.setEnabled(self.quantity.currentData() == "transmission")
        self.recompute_button.setEnabled(bool(self._pins) and not self._closed)

    def _settings_changed(self, *args):
        if self._closed:
            return
        self._update_controls()
        self._request = None
        self._clear_result()
        self.status.setText("Comparison settings changed; recomputing…")
        self.request_changed.emit(None)
        self._request_timer.start()

    def recompute(self):
        self._request_timer.stop()
        if self._closed:
            return
        if not self._pins:
            self._request = None
            self.set_pending("Pin up to four materials from Browse.")
            self.request_changed.emit(None)
            return
        controls = [self.samples]
        explicit = self.interval_mode.currentData() == "explicit"
        if explicit:
            controls += [self.wave_min, self.wave_max]
        if self.quantity.currentData() == "transmission":
            controls.append(self.thickness)
        try:
            if not self._interpret_numbers(controls):
                raise ValueError("Finish the numeric entry before comparing.")
            request = SpectralRequest(
                self.pin_keys,
                quantity=self.quantity.currentData(),
                interval_um=(self.wave_min.value(), self.wave_max.value())
                if explicit
                else None,
                sample_count=self.samples.value(),
                thickness_mm=self.thickness.value(),
            )
        except ValueError as exc:
            self._request = None
            self.set_error(str(exc))
            self.request_changed.emit(None)
            return
        self._request = request
        self.set_pending("Computing comparison…")
        self.request_changed.emit(request)

    def _clear_result(self):
        self._result = None
        self.model.set_result(None)
        self.ax.clear()
        self.toolbar.update()
        self.csv_button.setEnabled(False)
        self.png_button.setEnabled(False)
        self._show_details()
        self.update_theme(self._theme)

    def set_pending(self, message):
        self._clear_result()
        self.status.setText(message)

    def set_error(self, message):
        self.set_pending(message)

    def set_result(self, result: SpectralResult) -> bool:
        if self._closed or self._request is None or result.request != self._request:
            return False
        if tuple(series.key for series in result.series) != self.pin_keys:
            return False
        count = len(result.wavelengths_um)
        if count not in (0, result.request.sample_count) or any(
            len(series.values) != count for series in result.series
        ):
            raise ValueError("Comparison curves and table must share one sample grid")
        self._result = result
        labels = {
            pin.key: pin.display_name
            for pin in self._pins
            if pin.key not in self._records
        }
        self.model.set_result(result, labels)
        self.ax.clear()
        for index, series in enumerate(result.series):
            label = labels.get(series.key, series.display_name)[:45]
            values = [
                cell.value if cell.value is not None else np.nan
                for cell in series.values
            ]
            self.ax.plot(
                result.wavelengths_um,
                values,
                linestyle=self.LINE_STYLES[index],
                marker=self.MARKERS[index],
                markevery=max(1, count // 8),
                markersize=4,
                linewidth=1.6,
                label=f"{index + 1}. {label}",
            )
        if result.series and count:
            self.ax.legend(loc="best", fontsize="small")
        self.ax.set_xlabel("Wavelength (µm; native source conventions)")
        self.ax.set_ylabel(
            "Refractive\nindex n(λ)"
            if result.request.quantity == "n"
            else "Internal\ntransmission\n"
            f"(fraction; {result.request.thickness_mm:g} mm)"
        )
        notes = list(result.notes)
        if result.interval_um is None:
            notes.insert(0, "No common declared support. Choose an explicit interval.")
        else:
            low, high = result.interval_um
            notes.insert(
                0,
                f"{low:g}–{high:g} µm · {count} samples · "
                "Native conditions; no normalization.",
            )
        for index, series in enumerate(result.series):
            notes.extend(f"Pin {index + 1}: {warning}" for warning in series.warnings)
        self.status.setText("\n".join(notes))
        self.toolbar.update()
        self.update_theme(self._theme)
        self.toolbar.push_current()
        self.csv_button.setEnabled(bool(count))
        self.png_button.setEnabled(bool(count))
        self._show_details()
        return True

    def _show_details(self):
        key = self.current_key()
        if key is None:
            self.details.setPlainText(
                "Select a pin to inspect its formula and source details."
            )
            return
        lines = [_identity(key)]
        series = (
            next((s for s in self._result.series if s.key == key), None)
            if self._result
            else None
        )
        if series is None:
            lines.append(
                self._reasons.get(key, "Recompute to obtain current formula details.")
            )
        else:
            condition = series.conditions
            temperature = condition.temperature_c
            pressure = condition.pressure_atm
            lines += [
                f"Index convention: {condition.index_convention}",
                f"Wavelength convention: {condition.wavelength_convention}",
                "Reference temperature: "
                f"{temperature if temperature is not None else 'Unknown'} °C",
                f"Pressure: {pressure if pressure is not None else 'Unknown'} atm",
            ]
            lines.extend(condition.notes)
            if series.details:
                lines.append(series.details.model)
                lines.extend(
                    f"{label}: {value}" for label, value in series.details.fields
                )
                lines.extend(series.details.notes)
            lines.extend(series.warnings)
        # Core bounds the details. This final display limit also guards against
        # an accidental oversized application adapter without decoding payloads.
        text = "\n".join(lines)
        self.details.setPlainText(
            text[:32768] + ("\n[Display truncated]" if len(text) > 32768 else "")
        )

    def _clear_nearby(self, *args):
        self._nearby = None
        self._nearby_request = None
        self.nearby_model.set_records(())
        self.nearby_inspect.setEnabled(False)
        self.nearby_status.setText(
            "Select an available pin to search across loaded sources."
        )

    def clear_nearby(self):
        """Revoke accepted/in-flight alternatives after owner invalidation."""
        self._clear_nearby()

    def find_nearby(self):
        key = self.current_key()
        if self._closed or key not in self._records:
            return
        if not self._interpret_numbers((self.nd_scale, self.vd_scale)):
            self.nearby_status.setText("Finish both positive proximity scales.")
            return
        self._clear_nearby()
        self.nearby_status.setText("Finding nearby alternatives across loaded sources…")
        self._nearby_request = (key, self.nd_scale.value(), self.vd_scale.value())
        self.nearby_requested.emit(key, self.nd_scale.value(), self.vd_scale.value())

    def set_nearby(self, result: NearbyResult, records) -> bool:
        if self._closed or (
            self._nearby_request
            != (result.target_key, result.nd_scale, result.vd_scale)
            or result.target_key != self.current_key()
        ):
            return False
        lookup = {record.key: record for record in records}
        selected = tuple(
            lookup[match.key] for match in result.matches if match.key in lookup
        )
        self.nearby_model.set_records(selected, result.matches)
        self._nearby = result
        self.nearby_status.setText(
            f"{len(selected)} alternatives across loaded sources. "
            "Distance uses the displayed n_d/V_d scales; it is not optical merit.\n"
            f"Excluded records: {result.excluded_counts}"
        )
        return True

    def set_nearby_error(self, message):
        self._clear_nearby()
        self.nearby_status.setText(message)

    def inspect_nearby(self):
        index = self.nearby_table.currentIndex()
        if index.isValid() and self._nearby is not None and not self._closed:
            key = self.nearby_model.key_at(self.nearby_proxy.mapToSource(index).row())
            self.selection_requested.emit(key)

    def view_bounds(self):
        return tuple(
            float(value) for value in (*self.ax.get_xlim(), *self.ax.get_ylim())
        )

    def capture_rgba(self):
        """Detach pixels only; encoding and file publication belong to the owner."""
        if self._result is None:
            raise ValueError("No current comparison is available to export")
        self.canvas.draw()
        width, height = self.canvas.get_width_height(physical=True)
        return width, height, bytes(self.canvas.buffer_rgba())

    def _resize_plot(self, event):
        width, height = self.canvas.get_width_height(physical=True)
        ratio = self.canvas.device_pixel_ratio
        self.ax.yaxis.label.set_fontsize(8 if height / ratio < 220 else 10)
        left_pixels = 95 if self.ax.get_ylabel().count("\n") >= 2 else 78
        self.figure.subplots_adjust(
            left=min(0.35, left_pixels * ratio / max(width, 1)),
            right=0.97,
            bottom=min(0.40, 56 * ratio / max(height, 1)),
            top=0.95,
        )

    def update_theme(self, theme):
        self.toolbar.finish_navigation()
        self._theme = theme
        dark = "dark" in theme.casefold()
        background, foreground = (
            ("#202124", "#f0f1f3") if dark else ("#ffffff", "#25272b")
        )
        palette = QPalette(self.toolbar.palette())
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Button):
            palette.setColor(role, QColor(background))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            palette.setColor(role, QColor(foreground))
        self.toolbar.setPalette(palette)
        self.toolbar.setAutoFillBackground(True)
        self.figure.set_facecolor(background)
        self.ax.set_facecolor(background)
        self.ax.tick_params(colors=foreground)
        self.ax.xaxis.label.set_color(foreground)
        self.ax.yaxis.label.set_color(foreground)
        for spine in self.ax.spines.values():
            spine.set_color(foreground)
        self.ax.grid(True, color=foreground, alpha=0.15, linewidth=0.6)
        colors = self.DARK_COLORS if dark else self.LIGHT_COLORS
        for line, color in zip(self.ax.lines, colors, strict=False):
            line.set_color(color)
        legend = self.ax.get_legend()
        if legend:
            legend.get_frame().set_facecolor(background)
            legend.get_frame().set_edgecolor(foreground)
            for text in legend.get_texts():
                text.set_color(foreground)
            for line, color in zip(legend.get_lines(), colors, strict=False):
                line.set_color(color)
        self._resize_plot(None)
        self.canvas.draw_idle()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if watched is self.table and event.matches(QKeySequence.StandardKey.Copy):
                rows = sorted({index.row() for index in self.table.selectedIndexes()})
                values = []
                for row in rows:
                    values.append(
                        "\t".join(
                            str(self.model.data(self.model.index(row, column)))
                            for column in range(self.model.columnCount())
                        )
                    )
                QApplication.clipboard().setText("\n".join(values))
                return True
            if watched is self.canvas:
                if event.key() == Qt.Key.Key_Home:
                    self.toolbar.home()
                    return True
                if event.key() == Qt.Key.Key_Escape:
                    if str(self.toolbar.mode) == "pan/zoom":
                        self.toolbar.pan()
                    elif str(self.toolbar.mode) == "zoom rect":
                        self.toolbar.zoom()
                    return True
                key = event.key()
                if key in (
                    Qt.Key.Key_Left,
                    Qt.Key.Key_Right,
                    Qt.Key.Key_Up,
                    Qt.Key.Key_Down,
                    Qt.Key.Key_Plus,
                    Qt.Key.Key_Equal,
                    Qt.Key.Key_Minus,
                ):
                    x, y = self.ax.get_xlim(), self.ax.get_ylim()
                    if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
                        factor = 1.2 if key == Qt.Key.Key_Minus else 1 / 1.2
                        x = tuple(sum(x) / 2 + (v - sum(x) / 2) * factor for v in x)
                        y = tuple(sum(y) / 2 + (v - sum(y) / 2) * factor for v in y)
                    else:
                        dx = (x[1] - x[0]) * (
                            0.1
                            if key == Qt.Key.Key_Right
                            else -0.1
                            if key == Qt.Key.Key_Left
                            else 0
                        )
                        dy = (y[1] - y[0]) * (
                            0.1
                            if key == Qt.Key.Key_Up
                            else -0.1
                            if key == Qt.Key.Key_Down
                            else 0
                        )
                        x, y = tuple(v + dx for v in x), tuple(v + dy for v in y)
                    if all(
                        math.isfinite(v) and abs(v) < 1e100 for v in (*x, *y)
                    ) and all(
                        0 < b - a > max(abs(a), abs(b), 1) * 1e-12 for a, b in (x, y)
                    ):
                        if self.toolbar._nav_stack() is None:
                            self.toolbar.push_current()
                        self.ax.set_xlim(x)
                        self.ax.set_ylim(y)
                        self.toolbar.push_current()
                        self.canvas.draw_idle()
                    return True
        return super().eventFilter(watched, event)

    def close_service(self):
        self._closed = True
        self._request_timer.stop()
        self._request = None
        self._clear_result()
        self.setEnabled(False)
