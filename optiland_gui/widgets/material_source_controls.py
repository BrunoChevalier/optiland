"""Native, asynchronous file selection for supported local material sources."""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from optiland.materials.sources import DirectorySource, GlcSource

if TYPE_CHECKING:
    from optiland_gui.services.material_browser import MaterialBrowserSources


class MaterialSourceControls(QWidget):
    """Load local locators in the shared worker; removal only forgets cached data."""

    message = Signal(str)
    changed = Signal()
    selection_requested = Signal(str)

    def __init__(self, sources: MaterialBrowserSources, parent: QWidget | None = None):
        super().__init__(parent)
        self.sources = sources
        self._selected_source: str | None = None
        self._dialog: QFileDialog | None = None
        self._kind: str | None = None
        self._closed = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.add_button = QToolButton(self)
        self.add_button.setText("Add source…")
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_button)
        menu.addAction("RII directory…", lambda: self.choose_source("rii"))
        menu.addAction("OSLO GLC file…", lambda: self.choose_source("glc"))
        self.add_button.setMenu(menu)
        self.remove_button = QPushButton("Remove source", self)
        self.remove_button.setToolTip(
            "Forget the selected local source; its files stay unchanged."
        )
        layout.addWidget(self.add_button)
        layout.addWidget(self.remove_button)
        self.remove_button.clicked.connect(self.remove_selected)
        self.set_selected_source(None)

    def set_selected_source(self, source_id: str | None) -> None:
        """Track the catalog tree's selected source, independently of checkboxes."""
        self._selected_source = source_id
        request = self.sources.requests.get(source_id)
        self.remove_button.setEnabled(
            not self._closed and isinstance(request, DirectorySource | GlcSource)
        )

    def choose_source(self, kind: str) -> None:
        """Open one owned chooser without a nested blocking dialog event loop."""
        if kind not in ("rii", "glc"):
            raise ValueError("Unsupported material source kind")
        if self._closed:
            return
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._kind = kind
        dialog = QFileDialog(
            self,
            "Load RII material directory" if kind == "rii" else "Load OSLO GLC catalog",
        )
        self._dialog = dialog
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        if kind == "rii":
            dialog.setFileMode(QFileDialog.FileMode.Directory)
            dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        else:
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setNameFilter("OSLO glass catalogs (*.glc *.GLC)")
        dialog.finished.connect(self._chosen)
        dialog.open()

    def _chosen(self, result: int) -> None:
        dialog, self._dialog = self._dialog, None
        kind, self._kind = self._kind, None
        if dialog is None:
            return
        paths = dialog.selectedFiles()
        dialog.deleteLater()
        if self._closed or result != QDialog.DialogCode.Accepted or not paths:
            return
        self.load_path(kind, paths[0])

    def load_path(self, kind: str, path: str) -> str | None:
        """Submit a local path with stable, path-qualified browser identity.

        Path normalization is lexical: no catalog parsing, filesystem traversal
        or global registry registration happens on the GUI thread.
        """
        if kind not in ("rii", "glc"):
            raise ValueError("Unsupported material source kind")
        if self._closed:
            return None
        if not isinstance(path, str) or not path.strip():
            raise ValueError("A local material source requires a path")
        locator = os.path.abspath(path)
        identity = os.path.normcase(locator)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        source_id = f"local:{kind}:{digest}"
        request = (
            DirectorySource(source_id, locator)
            if kind == "rii"
            else GlcSource(source_id, locator)
        )
        label = f"{'RII' if kind == 'rii' else 'GLC'} · {locator}"
        self.message.emit(f"Loading local source: {locator}")
        self.sources.add_source(request, label)
        self.changed.emit()
        self.set_selected_source(source_id)
        self.selection_requested.emit(source_id)
        return source_id

    def remove_selected(self) -> None:
        source_id = self._selected_source
        if self._closed or not isinstance(
            self.sources.requests.get(source_id), DirectorySource | GlcSource
        ):
            return
        self.sources.remove_source(source_id)
        self.changed.emit()
        self.set_selected_source(None)
        self.message.emit(
            "Local source removed from Materials. Source files were retained."
        )

    def close_service(self) -> None:
        """Prevent a late native file-dialog completion from submitting work."""
        self._closed = True
        if self._dialog is not None:
            self._dialog.reject()
        self.setEnabled(False)
