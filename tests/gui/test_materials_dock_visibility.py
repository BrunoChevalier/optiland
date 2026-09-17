"""Native dock tabs, rather than child visibility, own Materials background work."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer
from PySide6.QtWidgets import QDockWidget, QMainWindow, QWidget

from optiland.materials.registry import MaterialRegistry
from optiland.materials.sources import InstalledSource, OwnedSource
from optiland_gui.materials_panel import MaterialsPanel
from optiland_gui.panel_manager import PanelManager
from tests.gui.test_material_browser import make_connector


def drain_events(qapp):
    """Run queued show/capture/query callbacks without timing-dependent sleeps."""
    for _ in range(4):
        qapp.processEvents()


@pytest.fixture
def tabbed_materials(qapp, monkeypatch):
    monkeypatch.setattr(MaterialRegistry, "registered_snapshot", lambda: ())
    connector = make_connector()
    window = QMainWindow()
    window.resize(1000, 700)
    panel = MaterialsPanel(connector)
    panel._query_timer.setInterval(0)
    analysis = QDockWidget("Analysis", window)
    analysis.setWidget(QWidget())
    materials = QDockWidget("Materials", window)
    materials.setWidget(panel)
    panel.set_dock_visible(False)
    materials.visibilityChanged.connect(panel.set_dock_visible)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, analysis)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, materials)
    window.tabifyDockWidget(analysis, materials)
    analysis.raise_()
    window.show()
    drain_events(qapp)
    yield window, panel, connector, analysis, materials
    window.hide()
    panel.sources.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_native_tab_first_activation_pause_return_and_reopen(tabbed_materials, qapp):
    _, panel, connector, analysis, materials = tabbed_materials
    service = connector.materials

    # Qt leaves the child visible behind another tab and emits no child Show
    # when selecting it. Autoload must instead follow dock.visibilityChanged.
    assert materials.isVisible() and panel.isVisible()
    assert panel.visibleRegion().isEmpty()
    assert not panel.sources._active
    assert not service.loads and not service.queries

    materials.raise_()
    drain_events(qapp)
    assert not panel.visibleRegion().isEmpty()
    assert panel.sources._active
    assert sum(isinstance(source, InstalledSource) for source in service.loads) == 1
    assert any(isinstance(source, OwnedSource) for source in service.loads)
    assert service.queries

    # Switching away cancels the pending result and queued design capture.
    panel.sources.refresh_design()
    panel.queue_query()
    cancellations = service.query_cancelled
    analysis.raise_()
    assert service.query_cancelled > cancellations
    loads, queries = len(service.loads), len(service.queries)
    drain_events(qapp)
    assert panel.isVisible() and panel.visibleRegion().isEmpty()
    assert not panel.sources._active
    assert not panel.sources._timer.isActive()
    assert not panel._query_timer.isActive()
    assert (len(service.loads), len(service.queries)) == (loads, queries)

    materials.raise_()
    drain_events(qapp)
    assert panel.sources._active
    assert len(service.loads) > loads and len(service.queries) > queries
    assert sum(isinstance(source, InstalledSource) for source in service.loads) == 1

    materials.close()
    drain_events(qapp)
    assert not panel.isVisible() and not panel.sources._active
    loads, queries = len(service.loads), len(service.queries)
    materials.show()
    materials.raise_()
    drain_events(qapp)
    assert panel.isVisible() and panel.sources._active
    assert len(service.loads) > loads and len(service.queries) > queries
    assert sum(isinstance(source, InstalledSource) for source in service.loads) == 1


def test_inactive_or_stopped_dock_rejects_late_callbacks(tabbed_materials, qapp):
    _, panel, connector, analysis, materials = tabbed_materials
    service = connector.materials
    materials.raise_()
    drain_events(qapp)
    # These callbacks were queued while visible but arrive after another tab wins.
    for callback in (panel._on_visible, panel.queue_query, panel.find_matches):
        QTimer.singleShot(0, callback)
    analysis.raise_()
    loads, queries = len(service.loads), len(service.queries)
    service.source_changed.emit("rii:installed")
    drain_events(qapp)
    assert (len(service.loads), len(service.queries)) == (loads, queries)
    assert not panel._query_timer.isActive()

    connector.calculation_jobs.stopped.emit()
    materials.close()
    materials.show()
    materials.raise_()
    drain_events(qapp)
    panel.find_matches()
    assert panel._closed and panel.sources._closed
    assert not panel.sources._active and not panel.isEnabled()
    assert (len(service.loads), len(service.queries)) == (loads, queries)


def test_panel_manager_wires_native_tab_visibility(
    qapp, highlighting_connector, tmp_path, monkeypatch
):
    import optiland_gui.panel_manager as module

    class Panel(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.viewer2D = SimpleNamespace(set_interaction_state=lambda _: None)
            self.viewer3D = None

    for name in (
        "ViewerPanel",
        "LensEditor",
        "SystemPropertiesPanel",
        "AnalysisPanel",
        "OptimizationPanel",
        "PythonTerminalWidget",
    ):
        monkeypatch.setattr(module, name, Panel)
    monkeypatch.setattr(MaterialRegistry, "registered_snapshot", lambda: ())
    loads, queries = [], []
    monkeypatch.setattr(highlighting_connector.materials, "load_source", loads.append)
    monkeypatch.setattr(highlighting_connector.materials, "query", queries.append)
    window = QMainWindow()
    window.resize(1000, 700)
    window.iface = object()
    window.settings = QSettings(str(tmp_path / "docks.ini"), QSettings.Format.IniFormat)
    manager = PanelManager(window, highlighting_connector)
    manager.create_all_panels(window)
    panel = manager.materials_panel
    panel._query_timer.setInterval(0)
    try:
        assert panel._dock_visible is False
        manager.setup_default_layout()
        window.show()
        drain_events(qapp)
        assert panel.isVisible() and panel.visibleRegion().isEmpty()
        assert not loads and not queries

        manager.on_sidebar_menu_selected("materials")
        drain_events(qapp)
        assert panel._dock_visible is True and panel.sources._active
        assert sum(isinstance(source, InstalledSource) for source in loads) == 1
        assert queries

        manager.analysis_dock.raise_()
        drain_events(qapp)
        assert panel._dock_visible is False and not panel.sources._active
        assert not panel._query_timer.isActive()
    finally:
        window.hide()
        panel.sources.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
