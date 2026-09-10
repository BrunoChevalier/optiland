"""OSLO import options and explicit, checkable GLC catalog selection."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from optiland.fileio.oslo.reader.parser import OsloDataParser
from optiland_gui.services.catalog_search import CatalogSearch

if TYPE_CHECKING:
    from collections.abc import Callable


def _path_order(path: str) -> tuple[str, str]:
    return path.casefold(), path


class _CatalogItem(QTreeWidgetItem):
    def __lt__(self, other: QTreeWidgetItem) -> bool:
        return _path_order(self.data(0, Qt.ItemDataRole.UserRole)) < _path_order(
            other.data(0, Qt.ItemDataRole.UserRole)
        )


class CatalogSelectionDialog(QDialog):
    """Review streamed results; all dismissal paths explicitly return no catalogs."""

    def __init__(
        self,
        directory: str,
        parent: QWidget | None = None,
        *,
        search: CatalogSearch | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose OSLO glass catalogs")
        self.resize(700, 480)
        self.directory = directory
        self.selected_paths: list[str] = []
        self._items: dict[str, QTreeWidgetItem] = {}
        self._search_id: int | None = None
        self._owns_search = search is None
        # Keep a killed process alive long enough for Qt to reap it after this
        # dialog closes; it must not be destroyed with the selection widget.
        self.search = search or CatalogSearch(QApplication.instance())
        self.search.batch_ready.connect(self._on_batch)
        self.search.completed.connect(self._on_complete)

        layout = QVBoxLayout(self)
        self.directory_label = QLabel()
        self.directory_label.setWordWrap(True)
        layout.addWidget(self.directory_label)
        choose = QPushButton("Choose another directory…")
        choose.clicked.connect(self.choose_directory)
        layout.addWidget(choose)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Catalog", "Relative directory"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.tree.itemChanged.connect(self._update_count)
        layout.addWidget(self.tree)
        controls = QHBoxLayout()
        self.check_all = QPushButton("Check all")
        self.uncheck_all = QPushButton("Uncheck all")
        self.check_all.clicked.connect(lambda: self._check_all(True))
        self.uncheck_all.clicked.connect(lambda: self._check_all(False))
        self.count_label = QLabel()
        controls.addWidget(self.check_all)
        controls.addWidget(self.uncheck_all)
        controls.addStretch()
        controls.addWidget(self.count_label)
        layout.addLayout(controls)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        explanation = QLabel(
            "Cancel, Escape or closing this window continues the import without "
            "external catalogs. Strict mode remains as chosen in import options."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Use checked catalogs"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.start_search(directory)

    def start_search(self, directory: str) -> None:
        """Start a fresh selection, discarding all checks from the previous root."""
        self._search_id = None
        self.search.cancel()
        self.directory = os.path.abspath(directory)
        self.directory_label.setText(f"Search directory: {self.directory}")
        self.selected_paths = []
        self.tree.clear()
        self._items.clear()
        self._update_count()
        self.status_label.setText(
            "Searching for GLC files in this directory and its subdirectories…"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._search_id = self.search.start(self.directory)

    def choose_directory(self) -> None:
        """Choose another root, or discard the whole selection on cancellation."""
        self._search_id = None
        self.search.cancel()
        directory = QFileDialog.getExistingDirectory(
            self, "Search for OSLO GLC catalogs", self.directory
        )
        if directory:
            self.start_search(directory)
        else:
            self.reject()

    def _on_batch(self, search_id: int, paths: list[str]) -> None:
        if search_id != self._search_id:
            return
        with QSignalBlocker(self.tree):
            for raw in paths:
                path = Path(raw)
                try:
                    relative = path.relative_to(self.directory)
                except ValueError:
                    continue
                key = os.path.normcase(str(path))
                if key in self._items or path.suffix.casefold() != ".glc":
                    continue
                item = _CatalogItem([path.name, str(relative.parent)])
                item.setData(0, Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(0, str(path))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                self._items[key] = item
                self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(0)
        self._update_count()

    def _on_complete(self, search_id: int, result: dict) -> None:
        if search_id != self._search_id:
            return
        self._search_id = None
        if not result["complete"]:
            skipped = result.get("skipped", 0)
            notice = f"Search incomplete: {result['reason']}."
            if skipped:
                notice += f" {skipped} entries could not be read."
            self.status_label.setText(
                notice + " Review the files found so far before continuing."
            )
        elif not self._items:
            self.status_label.setText(
                "No GLC catalogs found. You can continue without external catalogs."
            )
        else:
            self.status_label.setText(
                "Search complete. Review the checked catalogs to use for this import."
            )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def checked_paths(self) -> list[str]:
        """Return exactly the checked paths in stable, case-insensitive order."""
        return sorted(
            [
                item.data(0, Qt.ItemDataRole.UserRole)
                for item in self._items.values()
                if item.checkState(0) == Qt.CheckState.Checked
            ],
            key=_path_order,
        )

    def _update_count(self) -> None:
        self.count_label.setText(
            f"{len(self.checked_paths())} / {len(self._items)} selected"
        )

    def _check_all(self, checked: bool) -> None:
        with QSignalBlocker(self.tree):
            for item in self._items.values():
                item.setCheckState(
                    0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self._update_count()

    def done(self, result: int) -> None:
        """Ensure Cancel, Escape and window-close all discard partial selections."""
        self.selected_paths = (
            self.checked_paths() if result == QDialog.DialogCode.Accepted else []
        )
        self._search_id = None
        self.search.cancel()
        if self._owns_search:
            self.search.dispose()
        super().done(result)


class OsloImportDialog(QDialog):
    """Keep strictness outside catalog selection and let failed imports be revised."""

    def __init__(
        self,
        filepath: str,
        parent: QWidget | None = None,
        *,
        import_action: Callable[[bool, list[str], int], str | None] | None = None,
        file_operations=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import OSLO lens")
        self.resize(620, 400)
        self.filepath = filepath
        self.glass_catalogs: list[str] = []
        self._import_action = import_action
        self._metadata_error: str | None = None
        self._file_operations = file_operations
        self._pending_id = None
        if file_operations is not None:
            file_operations.load_ready.connect(self._load_ready)
            file_operations.load_failed.connect(self._load_failed)
        layout = QVBoxLayout(self)
        filename = QLabel(Path(filepath).name)
        layout.addWidget(filename)
        configuration_row = QHBoxLayout()
        configuration_label = QLabel("Configuration")
        self.configuration_combo = QComboBox()
        configuration_label.setBuddy(self.configuration_combo)
        reload_button = QPushButton("Reload lens")
        self.reload_button = reload_button
        reload_button.clicked.connect(self.reload_configurations)
        configuration_row.addWidget(configuration_label)
        configuration_row.addWidget(self.configuration_combo, 1)
        configuration_row.addWidget(reload_button)
        layout.addLayout(configuration_row)
        snapshot_note = QLabel(
            "Import one configuration as an independent lens. "
            "Inactive configurations can also be selected."
        )
        snapshot_note.setWordWrap(True)
        layout.addWidget(snapshot_note)
        self.strict_checkbox = QCheckBox("Strict mode")
        self.strict_checkbox.setChecked(False)
        layout.addWidget(self.strict_checkbox)
        description = QLabel(
            "Strict mode rejects unsupported lens data and named glasses without "
            "definitions in the selected catalogs. When off, missing glasses use "
            "the material database or an approximation, with warnings."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.choose_catalogs_button = QPushButton("Choose glass catalogs…")
        self.choose_catalogs_button.clicked.connect(self.choose_catalogs)
        layout.addWidget(self.choose_catalogs_button)
        self.catalog_label = QLabel("No external catalogs selected")
        layout.addWidget(self.catalog_label)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.error_label)
        self.activity = QProgressBar()
        self.activity.setRange(0, 0)
        self.activity.setAccessibleName("Importing OSLO lens")
        self.activity.hide()
        layout.addWidget(self.activity)
        self._activity_timer = QTimer(self)
        self._activity_timer.setSingleShot(True)
        self._activity_timer.timeout.connect(
            lambda: self.activity.setVisible(self._pending_id is not None)
        )
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        self.buttons.accepted.connect(self.attempt_import)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.reload_configurations()

    def reload_configurations(self) -> None:
        """Inspect metadata without material conversion or changing strictness."""
        previous = self.configuration_combo.currentData()
        self.configuration_combo.clear()
        try:
            # Preview the table even when unsupported optical commands exist.
            # The selected strictness is enforced, and all warnings reported,
            # by the authoritative import when the user presses Import.
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                data = OsloDataParser(self.filepath, strict=False).parse()
            for index, configuration in sorted(data.configurations.items()):
                state = "active" if configuration.active else "inactive"
                self.configuration_combo.addItem(
                    f"{index} — {state}, weight {configuration.weight:g}", index
                )
            selected = self.configuration_combo.findData(previous)
            self.configuration_combo.setCurrentIndex(max(selected, 0))
            self._metadata_error = None
            self.error_label.clear()
        except (OSError, ValueError) as exc:
            self._metadata_error = f"Cannot read lens configurations: {exc}"
            self.error_label.setText(
                self._metadata_error + "\nCorrect the lens file and choose Reload lens."
            )
        ready = self._metadata_error is None and self.configuration_combo.count() > 0
        self.configuration_combo.setEnabled(ready)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready)

    def choose_catalogs(self) -> None:
        """Request a root explicitly; directory and selection cancellation mean []."""
        self.glass_catalogs = []
        directory = QFileDialog.getExistingDirectory(
            self, "Search for OSLO GLC catalogs", str(Path(self.filepath).parent)
        )
        if directory:
            dialog = CatalogSelectionDialog(directory, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.glass_catalogs = list(dialog.selected_paths)
            dialog.deleteLater()
        self.catalog_label.setText(
            f"{len(self.glass_catalogs)} external catalogs selected"
            if self.glass_catalogs
            else "No external catalogs selected"
        )

    def attempt_import(self) -> None:
        """Keep the options open on error, preserving strictness and checked paths."""
        if (
            self._metadata_error is not None
            or self.configuration_combo.currentIndex() < 0
        ):
            return
        if self._file_operations is not None:
            if self._pending_id is not None:
                return
            request = self._file_operations.request_load(
                self.filepath,
                "oslo",
                load_options={
                    "strict": self.strict_checkbox.isChecked(),
                    "glass_catalogs": list(self.glass_catalogs),
                    "configuration": self.configuration_combo.currentData(),
                },
            )
            if request is None:
                self.error_label.setText("The import could not start. Please retry.")
                return
            self._pending_id = request.context.identifier
            self._set_pending(True)
            return
        if self._import_action is not None:
            error = self._import_action(
                self.strict_checkbox.isChecked(),
                list(self.glass_catalogs),
                self.configuration_combo.currentData(),
            )
            if error is not None:
                self.error_label.setText(
                    error
                    + "\nRevise the configuration, catalogs or strict mode, then retry."
                )
                return
        self.accept()

    def _set_pending(self, pending):
        for control in (
            self.strict_checkbox,
            self.configuration_combo,
            self.choose_catalogs_button,
            self.reload_button,
            self.buttons.button(QDialogButtonBox.StandardButton.Ok),
        ):
            control.setEnabled(not pending)
        if pending:
            self.error_label.setText(
                "Importing lens… You can continue using the main window."
            )
            self._activity_timer.start(500)
        else:
            self._activity_timer.stop()
            self.activity.hide()

    def _load_ready(self, identifier):
        if identifier == self._pending_id:
            self._pending_id = None
            self.accept()

    def _load_failed(self, identifier, error):
        if identifier == self._pending_id:
            self._pending_id = None
            self._set_pending(False)
            self.error_label.setText(
                error
                + "\nRevise the configuration, catalogs or strict mode, then retry."
            )

    def done(self, result):
        """Cancel only this dialog's open intent when it is dismissed."""
        identifier, self._pending_id = self._pending_id, None
        if identifier is not None and self._file_operations is not None:
            self._file_operations.cancel_load(identifier)
        self._activity_timer.stop()
        super().done(result)
