"""Native explicit Apply and local-source actions preserve exact authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget

from optiland.materials.properties import MaterialKey, SourceStamp
from optiland.materials.sources import DirectorySource, GlcSource, InstalledSource
from optiland_gui.materials_panel import MaterialsPanel
from optiland_gui.services.material_browser import MaterialBrowserSources
from optiland_gui.widgets.material_apply_footer import MaterialApplyFooter
from optiland_gui.widgets.material_source_controls import MaterialSourceControls
from tests.gui.test_material_browser import make_connector
from tests.gui.test_materials_panel import install, material_record


class ApplyBoundary(QObject):
    state_changed = Signal()
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.target = None
        self.busy = False
        self.allowed = False
        self.message = "Choose a medium from the editor."
        self.submitted = []
        self.refreshes = 0
        self.cancels = 0

    def can_apply(self, key, stamp):
        return SimpleNamespace(
            allowed=self.allowed and key is not None and stamp is not None,
            message=self.message,
        )

    def refresh_target(self):
        self.refreshes += 1

    def cancel(self):
        self.cancels += 1

    def submit(self, key, stamp):
        self.submitted.append((key, stamp))
        self.busy = True
        self.allowed = False
        self.message = "Preparing exact material Apply…"
        self.state_changed.emit()


def dispose(widget):
    widget.hide()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_apply_is_explicit_and_uses_inspected_generation(qapp):
    service = ApplyBoundary()
    footer = MaterialApplyFooter(service)
    footer.resize(680, 140)
    footer.show()
    qapp.processEvents()
    try:
        key = MaterialKey("local:rii:exact", "Shared", "same-name-reference-2")
        stamp = SourceStamp(key.source_id, "content-generation-1")
        footer.set_selection(key, stamp)
        assert not footer.apply_button.isEnabled()
        assert "No target" in footer.target_label.text()
        service.target = SimpleNamespace(label="Medium after surface 2")
        service.allowed = True
        service.message = "Ready for the captured medium."
        service.state_changed.emit()
        assert footer.apply_button.isEnabled()
        assert not service.submitted  # Inspection and target changes never apply.
        QTest.mouseClick(footer.refresh_button, Qt.MouseButton.LeftButton)
        assert service.refreshes == 1 and not service.submitted
        QTest.mouseClick(footer.apply_button, Qt.MouseButton.LeftButton)
        assert service.submitted == [(key, stamp)]
        assert not footer.apply_button.isEnabled()
        assert not footer.refresh_button.isEnabled()
        assert footer.cancel_button.isVisible()
        QTest.mouseClick(footer.cancel_button, Qt.MouseButton.LeftButton)
        assert service.cancels == 1

        service.busy = False
        service.allowed = True
        service.finished.emit(
            SimpleNamespace(
                message="Applied one exact owned material.", warnings=("<source> note",)
            )
        )
        assert (
            footer.outcome_label.text()
            == "Applied one exact owned material.\n<source> note"
        )
        assert footer.outcome_label.textFormat() == Qt.TextFormat.PlainText
        footer.set_selection(key, SourceStamp(key.source_id, "generation-2"))
        assert "Applied" in footer.outcome_label.text()
        footer.set_selection(MaterialKey(key.source_id, key.catalog, "other"), stamp)
        assert not footer.outcome_label.text()
        footer.close_service()
        service.state_changed.emit()
        footer._submit()
        assert not footer.apply_button.isEnabled()
        assert not footer.refresh_button.isEnabled()
        assert len(service.submitted) == 1
    finally:
        dispose(footer)


def test_local_paths_are_qualified_reload_same_source_and_remove_only_cache(
    qapp, tmp_path
):
    connector = make_connector()
    browser = MaterialBrowserSources(connector)
    controls = MaterialSourceControls(browser)
    first = tmp_path / "one" / "same.glc"
    second = tmp_path / "two" / "same.glc"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first catalog", encoding="utf8")
    second.write_text("second catalog", encoding="utf8")
    try:
        a = controls.load_path("glc", str(first))
        b = controls.load_path("glc", str(second))
        assert a != b
        assert isinstance(connector.materials.loads[-1], GlcSource)
        assert connector.materials.loads[-1].path == str(second)
        assert str(second) in browser.labels[b]
        assert controls.load_path("glc", str(first)) == a
        assert len(browser.requests) == 2
        controls.remove_selected()
        assert a not in browser.requests and b in browser.requests
        assert first.read_text(encoding="utf8") == "first catalog"
        assert second.read_text(encoding="utf8") == "second catalog"
        assert not controls.remove_button.isEnabled()
        directory = controls.load_path("rii", str(first.parent))
        assert isinstance(browser.requests[directory], DirectorySource)
        browser.add_source(InstalledSource(), "Installed")
        controls.set_selected_source("rii:installed")
        controls.remove_selected()
        assert "rii:installed" in browser.requests
        with pytest.raises(ValueError, match="kind"):
            controls.load_path("unknown", str(first))
        with pytest.raises(ValueError, match="path"):
            controls.load_path("rii", "")
        controls.close_service()
        assert controls.load_path("rii", str(tmp_path)) is None
    finally:
        browser.close()
        dispose(controls)


def test_native_source_dialog_accept_cancel_and_shutdown(qapp, tmp_path, monkeypatch):
    # Exercise actual QFileDialog signals without invoking the host's OS picker.
    original = QFileDialog.open

    def open_widget(dialog):
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        original(dialog)

    monkeypatch.setattr(QFileDialog, "open", open_widget)
    connector = make_connector()
    browser = MaterialBrowserSources(connector)
    parent = QWidget()
    controls = MaterialSourceControls(browser, parent)
    parent.show()
    try:
        controls.choose_source("rii")
        dialog = controls._dialog
        assert dialog.fileMode() == QFileDialog.FileMode.Directory
        controls.choose_source("glc")
        assert controls._dialog is dialog  # Only one live chooser.
        dialog.selectFile(str(tmp_path))
        dialog.accept()
        qapp.processEvents()
        assert controls._dialog is None
        assert len(connector.materials.loads) == 1
        assert isinstance(connector.materials.loads[0], DirectorySource)
        controls.choose_source("glc")
        assert controls._dialog.fileMode() == QFileDialog.FileMode.ExistingFile
        controls._dialog.reject()
        assert len(connector.materials.loads) == 1
        controls.choose_source("rii")
        controls.close_service()
        controls._chosen(QDialog.DialogCode.Accepted)
        controls.choose_source("glc")
        assert controls._dialog is None
        assert len(connector.materials.loads) == 1
        with pytest.raises(ValueError, match="kind"):
            controls.choose_source("unknown")
    finally:
        browser.close()
        dispose(parent)


def test_source_tree_removal_handles_first_load_failure(qapp, tmp_path):
    connector = make_connector()
    panel = MaterialsPanel(connector, autoload=False)
    panel.resize(1000, 800)
    panel.show()
    qapp.processEvents()
    panel._query_timer.stop()

    def fail(request):
        connector.materials.errors[request.source_id] = "Catalog cannot be read"
        connector.materials.source_failed.emit(
            request.source_id, "Catalog cannot be read"
        )

    connector.materials.load_source = fail
    try:
        source_id = panel.source_controls.load_path("rii", str(tmp_path))
        assert "cannot be read" in panel.status.text()
        assert panel.catalogs.topLevelItemCount() == 1
        assert panel.source_controls.remove_button.isEnabled()
        assert panel._selected_source_id() == source_id
        panel.source_controls.remove_selected()
        assert panel.catalogs.topLevelItemCount() == 0
        assert source_id not in panel._diagnostic_sources
        assert not panel.source_controls.remove_button.isEnabled()
        assert tmp_path.is_dir()
    finally:
        panel.sources.close()
        dispose(panel)


def test_removed_source_drops_catalog_choices_without_changing_other_selection(
    qapp, tmp_path
):
    connector = make_connector()
    a = material_record("local:a", "record-a")
    b = material_record("local:b", "record-b")
    install(connector.materials, a.key.source_id, (a,))
    install(connector.materials, b.key.source_id, (b,))
    panel = MaterialsPanel(connector, autoload=False)
    try:
        panel.sources.requests[a.key.source_id] = DirectorySource(
            a.key.source_id, str(tmp_path)
        )
        panel._catalog_choices[(b.key.source_id, b.key.catalog)] = False
        panel._rebuild_catalogs()
        panel._select_source(b.key.source_id)
        panel._select_source("no longer loaded")
        assert panel._selected_source_id() == b.key.source_id
        panel._select_source(a.key.source_id)
        panel.source_controls.remove_selected()
        assert panel._catalog_choices == {(b.key.source_id, b.key.catalog): False}
        assert panel.catalogs.topLevelItemCount() == 1
        assert (
            panel.catalogs.topLevelItem(0).child(0).checkState(0)
            == Qt.CheckState.Unchecked
        )
    finally:
        panel.sources.close()
        dispose(panel)
