"""Read-only, identity-preserving material results for native Qt tables."""

from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import ReferenceType, ref

from PySide6.QtCore import (
    QAbstractProxyModel,
    QAbstractTableModel,
    QLocale,
    QModelIndex,
    Qt,
    Slot,
)

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

    from optiland.materials.properties import (
        MaterialKey,
        MaterialMatch,
        MaterialSummary,
    )


class MaterialsTableModel(QAbstractTableModel):
    """Present immutable material summaries without lookup or optical evaluation.

    Use :class:`MaterialsSortProxyModel` for numeric sorting with unavailable
    values last. Keys belong to source rows; map proxy indexes to this model before
    calling :meth:`key_at`. Missing numerical values return ``None`` for sorting
    and explicit text for display, never a manufactured zero.
    """

    KeyRole = int(Qt.ItemDataRole.UserRole)
    SortRole = KeyRole + 1
    HEADERS = (
        "Material",
        "Catalog",
        "Source",
        "n_d",
        "V_d",
        "Δn_d",
        "ΔV_d",
        "Match distance",
        "n support (µm)",
        "k support (µm)",
    )

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._records: tuple[MaterialSummary, ...] = ()
        self._matches: dict[MaterialKey, MaterialMatch] = {}
        self._rows: dict[MaterialKey, int] = {}
        self._sort_values: tuple[tuple[str | float | None, ...], ...] = ()
        self._sort_keys: tuple[tuple[str, str, str], ...] = ()
        self._locale = QLocale()

    def set_records(
        self,
        records: tuple[MaterialSummary, ...],
        matches: tuple[MaterialMatch, ...] = (),
    ) -> None:
        """Atomically replace rows; callers restore selection using exact keys."""
        records = tuple(records)
        keys = [record.key for record in records]
        if len(keys) != len(set(keys)):
            raise ValueError("Material table rows require unique keys")
        match_map = {match.key: match for match in matches}
        if len(match_map) != len(matches):
            raise ValueError("Material table matches require unique keys")
        sort_values = []
        for record in records:
            match = match_map.get(record.key)
            sort_values.append(
                (
                    record.display_name.casefold(),
                    record.key.catalog.casefold(),
                    record.key.source_id.casefold(),
                    record.nd.value,
                    record.vd.value,
                    match.delta_nd if match is not None else None,
                    match.delta_vd if match is not None else None,
                    match.distance if match is not None else None,
                    record.n_range_um[0] if record.n_range_um is not None else None,
                    record.k_range_um[0] if record.k_range_um is not None else None,
                )
            )
        self.beginResetModel()
        self._records = records
        self._matches = match_map
        self._rows = {key: row for row, key in enumerate(keys)}
        self._sort_values = tuple(sort_values)
        self._sort_keys = tuple(
            (key.source_id, key.catalog, key.record_id) for key in keys
        )
        self.endResetModel()

    def key_at(self, row: int) -> MaterialKey | None:
        """Return an exact source-row key, or ``None`` for an invalid row."""
        return self._records[row].key if 0 <= row < len(self._records) else None

    def row_for_key(self, key: MaterialKey) -> int:
        """Return the source row, or ``-1`` if filtered out or unavailable."""
        return self._rows.get(key, -1)

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return (
                    self.HEADERS[section] if 0 <= section < len(self.HEADERS) else None
                )
            return str(section + 1) if 0 <= section < len(self._records) else None
        return None

    def data(
        self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object | None:
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record, column = self._records[index.row()], index.column()
        if not 0 <= column < len(self.HEADERS):
            return None
        if role == self.KeyRole:
            return record.key
        if role == self.SortRole:
            return self._sort_values[index.row()][column]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(record)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(
                Qt.AlignmentFlag.AlignVCenter
                | (
                    Qt.AlignmentFlag.AlignRight
                    if column >= 3
                    else Qt.AlignmentFlag.AlignLeft
                )
            )
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        match = self._matches.get(record.key)
        if column < 3:
            value = (record.display_name, record.key.catalog, record.key.source_id)[
                column
            ]
            return value
        if column in (3, 4):
            prop = record.nd if column == 3 else record.vd
            return (
                self._number(prop.value, 7 if column == 3 else 5)
                if prop.value is not None
                else prop.status.capitalize()
            )
        if column < 8:
            value = (
                (match.delta_nd, match.delta_vd, match.distance)[column - 5]
                if match is not None
                else None
            )
            return "Not requested" if value is None else self._number(value, 6)
        limits = record.n_range_um if column == 8 else record.k_range_um
        return (
            f"{self._number(limits[0], 6)}–{self._number(limits[1], 6)}"
            if limits is not None
            else "Unknown"
        )

    def _number(self, value: float, precision: int) -> str:
        return self._locale.toString(float(value), "g", precision)

    def _tooltip(self, record: MaterialSummary) -> str:
        conditions = record.conditions
        temperature = (
            f"{self._number(conditions.temperature_c, 6)} °C"
            if conditions.temperature_c is not None
            else "unknown temperature"
        )
        lines = [
            record.display_name,
            f"Catalog: {record.key.catalog}",
            f"Source: {record.key.source_id}",
            f"Record: {record.key.record_id}",
            "Computed at native source conditions; no convention normalization.",
            f"Index: {conditions.index_convention}; "
            f"wavelength: {conditions.wavelength_convention}; {temperature}",
            f"Table bounds: {record.bounds or 'not specified'}",
        ]
        for label, prop in (("n_d", record.nd), ("V_d", record.vd)):
            if prop.reason:
                lines.append(f"{label}: {prop.reason}")
        if record.owned_data is None:
            lines.append("No portable definition available for Apply.")
        # Qt's auto-rich-text tooltip detection must never interpret source HTML.
        return "\n".join(lines).replace("<", "‹").replace(">", "›")


class MaterialsSortProxyModel(QAbstractProxyModel):
    """Sort immutable material rows once, without per-comparison Qt callbacks.

    Missing values stay last in either direction. Equal values use ascending
    exact material identity. A row permutation preserves native Qt selection
    through layout changes; the source model owns atomic replacement resets.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # The panel/caller owns the source QObject. A strong Python reference
        # here can destroy that source during this proxy's C++ destruction,
        # delivering a source-destroyed callback to a partially destroyed proxy.
        self._source_ref: ReferenceType[MaterialsTableModel] | None = None
        self._proxy_rows: tuple[int, ...] = ()
        self._source_rows: tuple[int, ...] = ()
        self._column = -1
        self._order = Qt.SortOrder.AscendingOrder

    @property
    def _material_model(self) -> MaterialsTableModel | None:
        return self._source_ref() if self._source_ref is not None else None

    def setSourceModel(self, source_model: MaterialsTableModel | None) -> None:
        """Bind a reset-only material table, or detach it with ``None``."""
        if source_model is not None and not isinstance(
            source_model, MaterialsTableModel
        ):
            raise TypeError("Material sorting requires a MaterialsTableModel")
        previous = self._material_model
        if previous is source_model:
            return
        self.beginResetModel()
        if previous is not None:
            previous.modelAboutToBeReset.disconnect(self._source_about_to_reset)
            previous.modelReset.disconnect(self._source_reset)
            previous.destroyed.disconnect(self._source_destroyed)
        self._source_ref = ref(source_model) if source_model is not None else None
        super().setSourceModel(source_model)
        if source_model is not None:
            source_model.modelAboutToBeReset.connect(self._source_about_to_reset)
            source_model.modelReset.connect(self._source_reset)
            source_model.destroyed.connect(self._source_destroyed)
        self._reorder()
        self.endResetModel()

    @Slot()
    def _source_about_to_reset(self) -> None:
        self.beginResetModel()

    @Slot()
    def _source_reset(self) -> None:
        self._reorder()
        self.endResetModel()

    @Slot()
    def _source_destroyed(self) -> None:
        self.beginResetModel()
        self._source_ref = None
        super().setSourceModel(None)
        self._reorder()
        self.endResetModel()

    def _reorder(self) -> None:
        model = self._material_model
        rows = list(range(len(model._records))) if model is not None else []
        if model is not None and self._column >= 0:
            rows.sort(key=model._sort_keys.__getitem__)
            values = model._sort_values
            column = self._column
            valid = [row for row in rows if values[row][column] is not None]
            missing = [row for row in rows if values[row][column] is None]
            valid.sort(
                key=lambda row: values[row][column],
                reverse=self._order == Qt.SortOrder.DescendingOrder,
            )
            rows = valid + missing
        inverse = [0] * len(rows)
        for proxy_row, source_row in enumerate(rows):
            inverse[source_row] = proxy_row
        self._proxy_rows, self._source_rows = tuple(rows), tuple(inverse)

    def sort(
        self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
    ) -> None:
        """Order a column, retaining persistent indexes and keyboard selection."""
        if not -1 <= column < len(MaterialsTableModel.HEADERS):
            return
        if (column, order) == (self._column, self._order):
            return
        self.layoutAboutToBeChanged.emit()
        persistent = self.persistentIndexList()
        sources = [self.mapToSource(index) for index in persistent]
        self._column, self._order = column, order
        self._reorder()
        self.changePersistentIndexList(
            persistent, [self.mapFromSource(index) for index in sources]
        )
        self.layoutChanged.emit()

    def mapToSource(self, index: QModelIndex) -> QModelIndex:
        model = self._material_model
        if (
            model is None
            or not index.isValid()
            or index.model() is not self
            or not 0 <= index.row() < len(self._proxy_rows)
        ):
            return QModelIndex()
        return model.index(self._proxy_rows[index.row()], index.column())

    def mapFromSource(self, index: QModelIndex) -> QModelIndex:
        if (
            not index.isValid()
            or index.model() is not self._material_model
            or not 0 <= index.row() < len(self._source_rows)
        ):
            return QModelIndex()
        return self.index(self._source_rows[index.row()], index.column())

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self._proxy_rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return (
            0
            if (parent is not None and parent.isValid()) or self._material_model is None
            else len(MaterialsTableModel.HEADERS)
        )

    def index(
        self, row: int, column: int, parent: QModelIndex | None = None
    ) -> QModelIndex:
        if not self.hasIndex(row, column, QModelIndex() if parent is None else parent):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, index: QModelIndex) -> QModelIndex:
        return QModelIndex()

    def data(
        self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object | None:
        source = self.mapToSource(index)
        return source.data(role) if source.isValid() else None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if self._material_model is None:
            return None
        if orientation == Qt.Orientation.Vertical:
            if not 0 <= section < len(self._proxy_rows):
                return None
            section = self._proxy_rows[section]
        return self._material_model.headerData(section, orientation, role)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        source = self.mapToSource(index)
        return source.flags() if source.isValid() else Qt.ItemFlag.NoItemFlags
