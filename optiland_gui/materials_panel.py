"""Native material discovery workspace with exact linked selection."""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QSettings,
    QSignalBlocker,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableView,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from optiland.materials.properties import (
    MaterialKey,
    MaterialQuery,
    MaterialQueryResult,
    MaterialSummary,
    PropertyValue,
)
from optiland_gui.models.materials_model import (
    MaterialsSortProxyModel,
    MaterialsTableModel,
)
from optiland_gui.services.material_browser import MaterialBrowserSources
from optiland_gui.widgets.glass_map import GlassMap, MapPoint
from optiland_gui.widgets.material_apply_footer import MaterialApplyFooter
from optiland_gui.widgets.material_source_controls import MaterialSourceControls

if TYPE_CHECKING:
    from PySide6.QtGui import QHideEvent, QResizeEvent, QShowEvent

    from optiland_gui.optiland_connector import OptilandConnector


class MaterialsPanel(QWidget):
    """Browse immutable material records without changing the optical document.

    Args:
        connector: Owner of the shared material and document services.
        parent: Optional native parent widget.
        settings: Host settings used for splitter preferences.
        autoload: Discover sources on first display; tests may supply loaded data.
    """

    selection_changed = Signal(object)

    def __init__(
        self,
        connector: OptilandConnector,
        parent: QWidget | None = None,
        *,
        settings: QSettings | None = None,
        autoload: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MaterialsPanel")
        self.setAccessibleName("Materials workspace")
        self.connector = connector
        self.service = connector.materials
        self.sources = MaterialBrowserSources(connector, self)
        self.settings = settings
        self._autoload = autoload
        self._dock_visible: bool | None = None
        self._closed = False
        self._selected_key: MaterialKey | None = None
        self._selected_record: MaterialSummary | None = None
        self._selecting = False
        self._catalog_choices: dict[tuple[str, str], bool] = {}
        self._catalog_default_checked = True
        self._diagnostic_sources: set[str] = set()
        self._source_states: dict[str, str] = {}
        self._plot_points: dict[str, dict[MaterialKey, MapPoint]] = {}
        self._records: tuple[MaterialSummary, ...] = ()
        self._last_query: MaterialQuery | None = None
        self._results_current = False
        self._filters_wanted: bool | None = None
        self._query_timer = QTimer(self)
        self._query_timer.setSingleShot(True)
        self._query_timer.setInterval(100)
        self._query_timer.timeout.connect(self.find_matches)
        self._build_ui()
        self._connect_signals()
        self._rebuild_catalogs()
        self._show_inspector()
        if settings is not None:
            for key, splitter in (
                ("Materials/browseSplitter", self.browse_splitter),
                ("Materials/mapSplitter", self.map_splitter),
            ):
                value = settings.value(key)
                if value is not None:
                    splitter.restoreState(value)

    @property
    def selected_key(self) -> MaterialKey | None:
        """Return the persistent exact identity, including a hidden selection."""
        return self._selected_key

    @property
    def selected_material(self) -> MaterialSummary | None:
        """Resolve the selected identity in current accepted source data."""
        return self.service.material(self._selected_key) if self._selected_key else None

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)
        self.browse = QWidget(self.tabs)
        self.tabs.addTab(self.browse, "&Browse")
        browse_layout = QVBoxLayout(self.browse)
        browse_layout.setContentsMargins(0, 4, 0, 0)
        tools = QHBoxLayout()
        self.filters_toggle = QToolButton(self)
        self.filters_toggle.setText("Filters")
        self.filters_toggle.setCheckable(True)
        self.filters_toggle.setAccessibleName("Show material filters")
        tools.addWidget(self.filters_toggle)
        self.name_search = QLineEdit(self)
        self.name_search.setPlaceholderText("Find material or record…")
        self.name_search.setAccessibleName("Find material or record")
        self.name_search.setClearButtonEnabled(True)
        tools.addWidget(self.name_search, 1)
        self.locate_button = QPushButton("Locate", self)
        self.locate_button.setToolTip("Move the map to the selected material")
        tools.addWidget(self.locate_button)
        self.details_toggle = QToolButton(self)
        self.details_toggle.setText("Details")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAccessibleName("Show selected material details")
        tools.addWidget(self.details_toggle)
        self.help_button = QPushButton("Help", self)
        tools.addWidget(self.help_button)
        browse_layout.addLayout(tools)
        self.selection_summary = QLabel(self)
        self.selection_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_summary.setWordWrap(True)
        browse_layout.addWidget(self.selection_summary)
        self.browse_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        browse_layout.addWidget(self.browse_splitter, 1)
        self.filters = self._make_filters()
        self.browse_splitter.addWidget(self.filters)
        self.center = QWidget(self)
        center_layout = QVBoxLayout(self.center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.map_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.map = GlassMap(self)
        self.map_splitter.addWidget(self.map)
        self.model = MaterialsTableModel(self)
        self.proxy = MaterialsSortProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView(self)
        self.table.setAccessibleName("Matching material results")
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((130, 95, 110, 95, 90, 90, 90, 80, 100, 150)):
            self.table.setColumnWidth(column, width)
        self.map_splitter.addWidget(self.table)
        self.map_splitter.setSizes([380, 240])
        center_layout.addWidget(self.map_splitter)
        self.browse_splitter.addWidget(self.center)
        self.inspector = QPlainTextEdit(self)
        self.inspector.setReadOnly(True)
        self.inspector.setTabChangesFocus(True)
        self.inspector.setAccessibleName("Selected material properties and provenance")
        self.inspector.setMinimumWidth(180)
        self.browse_splitter.addWidget(self.inspector)
        self.browse_splitter.setSizes([250, 645, 280])
        self.browse_splitter.setStretchFactor(1, 1)
        self.status = QLabel(self)
        self.status.setAccessibleName("Material browsing status")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        browse_layout.addWidget(self.status)
        self.conditions_notice = QLabel(
            "Native reference conditions: computed values are not normalized between "
            "sources. Inspect temperature and index/wavelength conventions "
            "before comparing.",
            self,
        )
        self.conditions_notice.setWordWrap(True)
        browse_layout.addWidget(self.conditions_notice)
        self.footer = MaterialApplyFooter(self.connector.material_apply, self)
        layout.addWidget(self.footer)

    def _make_filters(self) -> QScrollArea:
        content = QWidget(self)
        form = QVBoxLayout(content)
        form.setContentsMargins(2, 2, 6, 2)
        form.addWidget(QLabel("Catalogs", self))
        self.catalogs = QTreeWidget(self)
        self.catalogs.setHeaderHidden(True)
        self.catalogs.setAccessibleName("Selected source-qualified catalogs")
        self.catalogs.setMinimumHeight(150)
        form.addWidget(self.catalogs, 1)
        selection_buttons = QHBoxLayout()
        self.all_catalogs = QPushButton("All", self)
        self.no_catalogs = QPushButton("None", self)
        selection_buttons.addWidget(self.all_catalogs)
        selection_buttons.addWidget(self.no_catalogs)
        form.addLayout(selection_buttons)
        self.source_actions = QHBoxLayout()
        self.reload_button = QPushButton("Reload sources", self)
        self.stop_button = QPushButton("Stop loading", self)
        self.source_actions.addWidget(self.reload_button)
        self.source_actions.addWidget(self.stop_button)
        form.addLayout(self.source_actions)
        self.source_controls = MaterialSourceControls(self.sources, self)
        form.addWidget(self.source_controls)
        self.context_check = QCheckBox("Show current-design context", self)
        self.context_check.setChecked(True)
        form.addWidget(self.context_check)
        self.patent = QGroupBox("Patent match", self)
        self.patent.setCheckable(True)
        self.patent.setChecked(False)
        patent_form = QFormLayout(self.patent)
        patent_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.target_nd = self._number(0.000001, 20, 1.5168, 8)
        self.tolerance_nd = self._number(1e-8, 10, 0.01, 8)
        self.target_vd = self._number(-1e6, 1e6, 64.17, 6)
        self.tolerance_vd = self._number(1e-6, 1e6, 1.0, 6)
        for label, control in (
            ("Target &n_d", self.target_nd),
            ("n_d tolerance ±", self.tolerance_nd),
            ("Target &V_d", self.target_vd),
            ("V_d tolerance ±", self.tolerance_vd),
        ):
            control.setAccessibleName(label.replace("&", ""))
            patent_form.addRow(label, control)
        form.addWidget(self.patent)
        self.support = QGroupBox("Wavelength support", self)
        self.support.setCheckable(True)
        self.support.setChecked(False)
        support_form = QFormLayout(self.support)
        support_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.wave_min = self._number(1e-6, 1e6, 0.4, 6)
        self.wave_max = self._number(1e-6, 1e6, 0.7, 6)
        self.wave_min.setAccessibleName("Minimum wavelength in micrometers")
        self.wave_max.setAccessibleName("Maximum wavelength in micrometers")
        support_form.addRow("From (µm)", self.wave_min)
        support_form.addRow("To (µm)", self.wave_max)
        self.unknown_support = QComboBox(self)
        self.unknown_support.addItem("Exclude unknown support", "exclude")
        self.unknown_support.addItem("Include unknown support", "include")
        self.unknown_support.setAccessibleName("Unknown wavelength support policy")
        support_form.addRow(self.unknown_support)
        form.addWidget(self.support)
        self.find_button = QPushButton("Find matches", self)
        self.reveal_button = QPushButton("Reveal selected", self)
        self.reveal_button.setToolTip(
            "Clear name, patent and wavelength constraints and select "
            "the material's catalog"
        )
        form.addWidget(self.find_button)
        form.addWidget(self.reveal_button)
        form.addStretch()
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setMinimumWidth(250)
        scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        return scroll

    def _number(
        self, minimum: float, maximum: float, value: float, decimals: int
    ) -> QDoubleSpinBox:
        box = QDoubleSpinBox(self)
        box.setDecimals(decimals)
        box.setRange(minimum, maximum)
        box.setValue(value)
        box.setKeyboardTracking(False)
        return box

    def _connect_signals(self) -> None:
        self.name_search.textChanged.connect(self.queue_query)
        self.catalogs.itemChanged.connect(self._catalog_changed)
        self.catalogs.currentItemChanged.connect(self._selected_source_changed)
        self.source_controls.message.connect(self.status.setText)
        self.source_controls.changed.connect(self._rebuild_catalogs)
        self.source_controls.selection_requested.connect(self._select_source)
        self.all_catalogs.clicked.connect(lambda: self._set_all_catalogs(True))
        self.no_catalogs.clicked.connect(lambda: self._set_all_catalogs(False))
        self.find_button.clicked.connect(self.find_matches)
        self.reveal_button.clicked.connect(self.reveal_selected)
        self.locate_button.clicked.connect(lambda: self.map.locate(self._selected_key))
        self.reload_button.clicked.connect(self.sources.reload)
        self.stop_button.clicked.connect(self.sources.stop)
        self.context_check.toggled.connect(self._refresh_context)
        self.filters_toggle.toggled.connect(self._toggle_filters)
        self.details_toggle.toggled.connect(self._adapt_layout)
        self.help_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://www.optiland.org/docs/gui_materials.html")
            )
        )
        self.table.selectionModel().currentRowChanged.connect(self._table_selection)
        self.map.selection_requested.connect(self.select_key)
        for control in (
            self.target_nd,
            self.target_vd,
            self.tolerance_nd,
            self.tolerance_vd,
            self.wave_min,
            self.wave_max,
        ):
            control.editingFinished.connect(self.queue_query)
        self.patent.toggled.connect(self._patent_toggled)
        self.support.toggled.connect(self.queue_query)
        self.unknown_support.currentIndexChanged.connect(self.queue_query)
        self.service.source_changed.connect(self._source_changed)
        self.service.source_removed.connect(self._source_changed)
        self.service.source_failed.connect(self._source_failed)
        self.service.query_finished.connect(self._query_finished)
        self.service.query_invalidated.connect(self._query_invalidated)
        self.service.query_failed.connect(self._query_failed)
        self.service.state_changed.connect(self._service_state)
        self.sources.error.connect(self.status.setText)
        self.connector.calculation_jobs.stopped.connect(self._jobs_stopped)
        for key, callback in (
            ("Ctrl+F", self._focus_search),
            ("F6", lambda: self._cycle_focus(1)),
            ("Shift+F6", lambda: self._cycle_focus(-1)),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        copy = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        copy.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy.activated.connect(self.copy_selected)

    def _focus_search(self) -> None:
        self.name_search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.name_search.selectAll()

    def _patent_toggled(self, checked: bool) -> None:
        if checked:
            self.table.sortByColumn(7, Qt.SortOrder.AscendingOrder)
        self.queue_query()

    def copy_selected(self) -> None:
        """Copy the selected visible row with its full source-qualified identity."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = self.proxy.mapToSource(rows[0]).row()
        key = self.model.key_at(row)
        headers = ("Source ID", "Record ID", *self.model.HEADERS)
        values = (
            key.source_id,
            key.record_id,
            *(
                str(self.model.data(self.model.index(row, column)))
                for column in range(self.model.columnCount())
            ),
        )
        QApplication.clipboard().setText("\t".join(headers) + "\n" + "\t".join(values))

    def _rebuild_catalogs(self) -> None:
        selected_source = self._selected_source_id()
        blocker = QSignalBlocker(self.catalogs)
        self.catalogs.clear()
        self._diagnostic_sources = {
            source_id
            for source_id in self._diagnostic_sources
            if self.service.source_error(source_id)
        }
        source_ids = dict.fromkeys((*self.service.source_ids, *self.sources.requests))
        visible_catalogs = set()
        for source_id in sorted(self._diagnostic_sources):
            source_ids[source_id] = None
        for source_id in source_ids:
            if source_id.startswith("design:"):
                continue
            result = self.service.source_result(source_id)
            names = sorted(
                {record.key.catalog for record in result.snapshot.materials}
                if result
                else set(),
                key=str.casefold,
            )
            parent = QTreeWidgetItem([self.sources.labels.get(source_id, source_id)])
            parent.setData(0, Qt.ItemDataRole.UserRole, source_id)
            error = self.service.source_error(source_id)
            if error:
                parent.setText(
                    0,
                    parent.text(0)
                    + (" (reload failed)" if result else " (load failed)"),
                )
                parent.setIcon(
                    0,
                    self.style().standardIcon(
                        QStyle.StandardPixmap.SP_MessageBoxWarning
                    ),
                )
                message = error + (
                    "\nPrevious accepted data is retained."
                    if result
                    else "\nNo data loaded."
                )
                parent.setToolTip(
                    0, "<qt>" + html.escape(message).replace("\n", "<br>") + "</qt>"
                )
            elif result is None:
                state = self._source_states.get(source_id, "not loaded")
                parent.setText(0, parent.text(0) + f" ({state})")
            parent.setFlags(
                parent.flags()
                | Qt.ItemFlag.ItemIsAutoTristate
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            self.catalogs.addTopLevelItem(parent)
            if source_id == selected_source:
                self.catalogs.setCurrentItem(parent)
            for name in names:
                key = (source_id, name)
                visible_catalogs.add(key)
                selected = self._catalog_choices.setdefault(
                    key, self._catalog_default_checked
                )
                item = QTreeWidgetItem(parent, [name or "Unnamed catalog"])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0, Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
                )
            parent.setExpanded(True)
        self._catalog_choices = {
            key: value
            for key, value in self._catalog_choices.items()
            if key in visible_catalogs
        }
        del blocker
        self._selected_source_changed()

    def _selected_source_id(self) -> str | None:
        item = self.catalogs.currentItem()
        if item is None:
            return None
        parent = item.parent() or item
        return parent.data(0, Qt.ItemDataRole.UserRole)

    def _selected_source_changed(self, *args: object) -> None:
        self.source_controls.set_selected_source(self._selected_source_id())

    def _select_source(self, source_id: str) -> None:
        for index in range(self.catalogs.topLevelItemCount()):
            item = self.catalogs.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == source_id:
                self.catalogs.setCurrentItem(item)
                self.catalogs.scrollToItem(item)
                return

    def _catalog_changed(self, item: QTreeWidgetItem, column: int) -> None:
        for top in range(self.catalogs.topLevelItemCount()):
            parent = self.catalogs.topLevelItem(top)
            for child in range(parent.childCount()):
                item = parent.child(child)
                self._catalog_choices[item.data(0, Qt.ItemDataRole.UserRole)] = (
                    item.checkState(0) == Qt.CheckState.Checked
                )
        self.queue_query()

    def _set_all_catalogs(self, selected: bool) -> None:
        self._catalog_default_checked = selected
        for key in self._catalog_choices:
            self._catalog_choices[key] = selected
        self._rebuild_catalogs()
        self.queue_query()

    def make_query(self) -> MaterialQuery:
        """Validate complete controls before replacing the last valid query."""
        controls = []
        if self.patent.isChecked():
            controls.extend(
                (self.target_nd, self.tolerance_nd, self.target_vd, self.tolerance_vd)
            )
        if self.support.isChecked():
            controls.extend((self.wave_min, self.wave_max))
        if any(not control.hasAcceptableInput() for control in controls):
            raise ValueError("Finish the numeric entry before finding matches.")
        choices = tuple(
            key
            for key, checked in self._catalog_choices.items()
            if checked and key[0] in self.service.source_ids
        )
        return MaterialQuery(
            source_ids=tuple(sorted({key[0] for key in choices})),
            catalog_keys=choices,
            name=self.name_search.text(),
            target_nd=self.target_nd.value() if self.patent.isChecked() else None,
            nd_tolerance=self.tolerance_nd.value() if self.patent.isChecked() else None,
            target_vd=self.target_vd.value() if self.patent.isChecked() else None,
            vd_tolerance=self.tolerance_vd.value() if self.patent.isChecked() else None,
            wavelength_range_um=(self.wave_min.value(), self.wave_max.value())
            if self.support.isChecked()
            else None,
            unknown_policy=self.unknown_support.currentData(),
        )

    def queue_query(self, *args: object) -> None:
        """Debounce complete query edits while preserving the current viewport."""
        if not self._closed and self._dock_visible is not False:
            self._query_timer.start()

    def _jobs_stopped(self) -> None:
        self._closed = True
        self._plot_points.clear()
        self.footer.close_service()
        self.source_controls.close_service()
        self._query_timer.stop()
        self.setEnabled(False)

    def find_matches(self) -> None:
        """Submit valid immutable criteria to the shared background material service."""
        self._query_timer.stop()
        if self._closed or self._dock_visible is False:
            return
        try:
            query = self.make_query()
        except ValueError as exc:
            self.status.setText(str(exc) + " Previous results are retained.")
            return
        self._last_query = query
        self.status.setText("Finding materials…")
        self.map.set_target(
            query.target_nd, query.target_vd, query.nd_tolerance, query.vd_tolerance
        )
        self.service.query(query)

    def _query_finished(self, result: MaterialQueryResult) -> None:
        if self.service.last_query_result is not result:
            return
        # One exact lookup per source avoids scanning the full source for every
        # matched row (quadratic work when a catalog-wide query completes).
        by_key = {
            record.key: record
            for stamp in result.stamps
            if (source := self.service.source_result(stamp.source_id)) is not None
            for record in source.snapshot.materials
        }
        records = tuple(
            by_key[match.key] for match in result.matches if match.key in by_key
        )
        self._records = records
        self._results_current = True
        self._selecting = True
        self.model.set_records(records, result.matches)
        self._selecting = False
        self.map.set_points(
            tuple(
                self._plot_point(record)
                for record in records
                if record.nd.value is not None and record.vd.value is not None
            )
        )
        self._refresh_context()
        self._sync_selection()
        off_map = sum(
            record.nd.value is None or record.vd.value is None for record in records
        )
        if not self._last_query.catalog_keys:
            message = "Select one or more catalogs to browse."
        else:
            message = f"{len(records)} matches · {off_map} without map coordinates"
            if result.excluded_counts:
                message += " · Excluded: " + ", ".join(
                    f"{name}: {count}" for name, count in result.excluded_counts
                )
        self.status.setText(message)
        self._show_inspector()

    def _plot_point(self, record: MaterialSummary) -> MapPoint:
        # Source publication/removal clears only that source's presentation
        # cache. Repeated filtering reuses immutable coordinates and labels.
        points = self._plot_points.get(record.key.source_id)
        if points is None:
            points = self._plot_points[record.key.source_id] = {}
        point = points.get(record.key)
        if point is None:
            point = points[record.key] = MapPoint(
                record.key, record.vd.value, record.nd.value, self._qualified(record)
            )
        return point

    def _query_failed(self, message: str) -> None:
        self.status.setText(f"Query failed: {message}. Previous results are retained.")

    def _query_invalidated(self) -> None:
        self._results_current = False
        self.status.setText(
            "Results need refresh. Previous rows remain available for inspection."
        )

    def _source_changed(self, source_id: str) -> None:
        self._plot_points.pop(source_id, None)
        if source_id.startswith("design:"):
            self._refresh_context()
        else:
            self._rebuild_catalogs()
            try:
                query = self.make_query()
            except ValueError:
                query = None
            if query is not None and (
                query != self._last_query or source_id in query.source_ids
            ):
                self.queue_query()
        self._sync_selection()
        self._show_inspector()

    def _source_failed(self, source_id: str, message: str) -> None:
        self._diagnostic_sources.add(source_id)
        label = self.sources.labels.get(source_id, source_id)
        retained = (
            " Previous accepted data is retained."
            if self.service.source_result(source_id)
            else ""
        )
        self.status.setText(f"{label}: {message}.{retained}")
        self._rebuild_catalogs()
        self._show_inspector()

    def _service_state(self, target: str, state: str) -> None:
        if target != "query":
            self._source_states[target] = state
        if state == "loading" and target == "query":
            self.status.setText(
                "Finding materials… Previous rows remain for inspection."
            )
        elif state == "loading":
            self._rebuild_catalogs()
            label = self.sources.labels.get(target, "current-design materials")
            self.status.setText(f"Loading {label}…")
        elif state == "cancelled":
            self._rebuild_catalogs()
            self.status.setText(
                "Material work cancelled. Accepted sources are retained."
            )

    def _refresh_context(self, *args: object) -> None:
        points = []
        if self.context_check.isChecked():
            for source_id in self.service.source_ids:
                if not source_id.startswith("design:"):
                    continue
                for record in self.service.source_result(source_id).snapshot.materials:
                    if record.nd.value is not None and record.vd.value is not None:
                        label = self.sources.design_labels.get(
                            record.key.record_id, "Current design"
                        )
                        points.append(
                            MapPoint(
                                record.key,
                                record.vd.value,
                                record.nd.value,
                                f"{label} · {record.display_name}",
                            )
                        )
        self.map.set_context(tuple(points))

    def _table_selection(self, current: QModelIndex, previous: QModelIndex) -> None:
        if not self._selecting and current.isValid():
            self.select_key(self.model.key_at(self.proxy.mapToSource(current).row()))

    def select_key(self, key: MaterialKey) -> None:
        """Inspect one exact record; selection never applies it to the document."""
        record = self.service.material(key)
        if record is None:
            return
        self._selected_key, self._selected_record = key, record
        self._sync_selection()
        self._show_inspector()
        self.selection_changed.emit(key)

    def _sync_selection(self) -> None:
        self._selecting = True
        row = self.model.row_for_key(self._selected_key)
        if row is not None and row >= 0:
            index = self.proxy.mapFromSource(self.model.index(row, 0))
            self.table.selectionModel().setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.table.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
        else:
            self.table.selectionModel().clearSelection()
            self.table.selectionModel().setCurrentIndex(
                QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate
            )
        self._selecting = False
        self.map.set_selected(self._selected_key)
        record = self.selected_material
        self.map.set_selected_point(
            MapPoint(
                record.key, record.vd.value, record.nd.value, self._qualified(record)
            )
            if record is not None
            and record.nd.value is not None
            and record.vd.value is not None
            else None
        )

    def _qualified(self, record: MaterialSummary) -> str:
        label = self.sources.labels.get(record.key.source_id, record.key.source_id)
        return (
            f"{record.display_name} · {record.key.catalog} · {label} · "
            f"{record.key.record_id[:12]}"
        )

    def _number_text(self, value: float | None) -> str:
        return "Unknown" if value is None else self.locale().toString(value, "g", 12)

    def _property_text(self, value: PropertyValue) -> str:
        return (
            self._number_text(value.value)
            if value.value is not None
            else f"{value.status.title()}: {value.reason or 'Unavailable'}"
        )

    def _range_text(self, value: tuple[float, float] | None) -> str:
        return (
            "Unknown"
            if value is None
            else f"{self._number_text(value[0])}–{self._number_text(value[1])} µm"
        )

    def _show_inspector(self) -> None:
        record = self.selected_material
        source = self.service.source_result(record.key.source_id) if record else None
        stamp = source.snapshot.stamps[0] if source is not None else None
        self.footer.set_selection(self._selected_key, stamp)
        available = record is not None
        if record is not None:
            self._selected_record = record
        else:
            record = self._selected_record
        self.locate_button.setEnabled(
            available and record.nd.value is not None and record.vd.value is not None
        )
        self.reveal_button.setEnabled(
            available and not record.key.source_id.startswith("design:")
            if record
            else False
        )
        if record is None:
            self.selection_summary.setText(
                "Select a material to inspect its exact source record."
            )
            self.inspector.setPlainText(
                "Choose a row or a map point.\n\n"
                "Inspection never changes the optical design."
            )
            return
        visible = any(item.key == record.key for item in self._records)
        suffix = ""
        if not available:
            suffix = " — Source unavailable; showing the last inspected record"
        elif record.key.source_id.startswith("design:"):
            suffix = " — Current-design context; not counted as a match"
        elif not visible:
            suffix = " — Selected glass is outside these filters"
        self.selection_summary.setText(self._qualified(record) + suffix)
        conditions = record.conditions
        lines = [
            record.display_name,
            suffix.strip(" —"),
            f"Catalog: {record.key.catalog}",
            "Source: "
            + self.sources.labels.get(record.key.source_id, record.key.source_id),
            f"Record: {record.key.record_id}",
            "",
            "Computed reference-line values (dimensionless)",
            f"n_d: {self._property_text(record.nd)}",
            f"V_d: {self._property_text(record.vd)}",
            "d = 0.5875618 µm; F = 0.4861327 µm; C = 0.6562725 µm",
            "",
            f"Declared n_d: {self._property_text(record.declared_nd)}",
            f"Declared V_d: {self._property_text(record.declared_vd)}",
            "",
            "Native reference conditions (no conversion)",
            f"Index convention: {conditions.index_convention}",
            f"Wavelength convention: {conditions.wavelength_convention}",
            f"Temperature: {self._number_text(conditions.temperature_c)} °C",
            f"Pressure: {self._number_text(conditions.pressure_atm)} atm",
            *conditions.notes,
            "",
            f"Index support: {self._range_text(record.n_range_um)}",
            f"Extinction support: {self._range_text(record.k_range_um)}",
            f"Out-of-range policy: {record.bounds or 'Not specified'}",
            "Extinction data: "
            + {True: "Present", False: "Absent", None: "Unknown"}[
                record.has_extinction_data
            ],
            "Measured transmission: "
            + {True: "Present", False: "Absent", None: "Unknown"}[
                record.has_transmission_data
            ],
            "",
            "Portable optical definition: "
            + ("Available" if record.owned_data is not None else "Unavailable"),
            "Bounds control evaluation; clamping does not establish measured support.",
        ]
        for value in (record.declared_nd, record.declared_vd):
            if value.source:
                lines.append(f"Declared value source: {value.source}")
        lines.extend(f"Condition source: {source}" for source in conditions.sources)
        source = self.service.source_result(record.key.source_id)
        if source is not None:
            error = self.service.source_error(record.key.source_id)
            if error:
                lines.extend(("", "Source reload failed; using previous data", error))
            kind = self.sources.source_kind(record.key.source_id)
            lines.append(f"Source kind: {kind}")
            lines.append(f"Source fingerprint: {source.snapshot.stamps[0].generation}")
            lines.extend(
                diagnostic.message
                for diagnostic in source.diagnostics
                if diagnostic.record_id in (None, record.key.record_id)
            )
        if record.owned_data is not None and len(record.owned_data) > 256 * 1024:
            lines.extend(
                (
                    "",
                    "Metadata preview unavailable for definitions larger than 256 KiB. "
                    "The complete optical data is retained.",
                )
            )
        elif record.owned_data is not None:
            metadata = json.loads(record.owned_data).get("metadata", {})
            if metadata:
                text = json.dumps(metadata, ensure_ascii=False, indent=2)
                lines.extend(("", "Source metadata and references", text[:20000]))
                if len(text) > 20000:
                    lines.append("Metadata preview truncated at 20,000 characters.")
        self.inspector.setPlainText("\n".join(lines))

    def reveal_selected(self) -> None:
        """Explicitly clear blocking constraints and enable the selected catalog."""
        record = self.selected_material
        if record is None or record.key.source_id.startswith("design:"):
            return
        self.name_search.clear()
        self.patent.setChecked(False)
        self.support.setChecked(False)
        self._catalog_choices[(record.key.source_id, record.key.catalog)] = True
        self._rebuild_catalogs()
        self.find_matches()

    def _toggle_filters(self, checked: bool) -> None:
        self._filters_wanted = checked
        self._adapt_layout()

    def _adapt_layout(self, *args: object) -> None:
        compact = self.width() < 740
        show_filters = (
            self.width() >= 1050
            if self._filters_wanted is None
            else self._filters_wanted
        )
        self._set_region_visible(self.filters, show_filters, self.filters_toggle)
        blocker = QSignalBlocker(self.filters_toggle)
        self.filters_toggle.setChecked(show_filters)
        del blocker
        self.details_toggle.setVisible(compact)
        detail = self.details_toggle.isChecked()
        self._set_region_visible(
            self.inspector, not compact or detail, self.details_toggle
        )
        self._set_region_visible(
            self.center, not compact or not detail, self.details_toggle
        )

    def _set_region_visible(
        self, widget: QWidget, visible: bool, toggle: QWidget
    ) -> None:
        focused = QApplication.focusWidget()
        if (
            not visible
            and focused is not None
            and (focused is widget or widget.isAncestorOf(focused))
        ):
            toggle.setFocus()
        widget.setVisible(visible)

    def _cycle_focus(self, direction: int) -> None:
        regions = [
            self.name_search,
            self.catalogs,
            self.map.canvas,
            self.table,
            self.inspector,
        ]
        regions = [
            widget for widget in regions if widget.isVisible() and widget.isEnabled()
        ]
        current = QApplication.focusWidget()
        index = (
            next(
                (
                    i
                    for i, widget in enumerate(regions)
                    if widget is current or widget.isAncestorOf(current)
                ),
                -1,
            )
            if current
            else -1
        )
        if regions:
            if index < 0 and direction < 0:
                index = 0
            regions[(index + direction) % len(regions)].setFocus(
                Qt.FocusReason.ShortcutFocusReason
            )

    def update_theme(self, theme: str) -> None:
        """Restyle existing plot artists through the host theme contract."""
        self.map.update_theme(theme)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._adapt_layout()
        QTimer.singleShot(0, self._on_visible)

    def _on_visible(self) -> None:
        if self.isVisible() and not self._closed and self._dock_visible is not False:
            if self._autoload:
                self.sources.set_active(True)
            self.queue_query()

    def set_dock_visible(self, visible: bool) -> None:
        """Follow dock-tab visibility, distinct from child QWidget visibility."""
        self._dock_visible = visible
        if visible:
            self._on_visible()
        else:
            self.sources.set_active(False)
            self._query_timer.stop()
            self.service.cancel_query()

    def hideEvent(self, event: QHideEvent) -> None:
        self.sources.set_active(False)
        self._query_timer.stop()
        self.service.cancel_query()
        if self.settings is not None:
            self.settings.setValue(
                "Materials/browseSplitter", self.browse_splitter.saveState()
            )
            self.settings.setValue(
                "Materials/mapSplitter", self.map_splitter.saveState()
            )
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._adapt_layout()
