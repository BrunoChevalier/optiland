"""One asynchronous chooser for an already captured material artifact."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QDialog, QFileDialog


class MaterialExportDialog(QObject):
    """Retain detached output while choosing a path; borrow the shared writer."""

    message = Signal(str)
    state_changed = Signal()

    def __init__(self, operations, parent):
        super().__init__(parent)
        self.operations = operations
        self._dialog = None
        self._artifact = None
        self._closed = False

    @property
    def choosing(self):
        return self._dialog is not None

    def choose(self, artifact, extension, name):
        if self._closed:
            return
        if extension not in ("csv", "png", "json", "py"):
            raise ValueError("Unsupported material export format")
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._artifact = artifact
        dialog = QFileDialog(self.parent(), "Export " + name)
        self._dialog = dialog
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setDefaultSuffix(extension)
        dialog.setNameFilter(f"{extension.upper()} files (*.{extension})")
        dialog.selectFile(f"{name}.{extension}")
        dialog.finished.connect(self._chosen)
        dialog.open()
        self.state_changed.emit()

    @Slot(int)
    def _chosen(self, result):
        dialog, self._dialog = self._dialog, None
        artifact, self._artifact = self._artifact, None
        if dialog is None:
            return
        paths = dialog.selectedFiles()
        dialog.deleteLater()
        self.state_changed.emit()
        if self._closed or result != QDialog.DialogCode.Accepted or not paths:
            return
        request = self.operations.request_artifact(paths[0], artifact)
        self.message.emit(
            "Export queued; publication status appears in the application log."
            if request is not None
            else "Export was not started. Check the application log."
        )

    @Slot()
    def close(self):
        self._closed = True
        self._artifact = None
        if self._dialog is not None:
            self._dialog.reject()
