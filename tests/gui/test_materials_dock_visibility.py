"""Native dock tabs, rather than child visibility, own Materials background work."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
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
    from optiland_gui.config import THEME_DARK_PATH, THEME_LIGHT_PATH
    from optiland_gui.main_window import MainWindow
    from optiland_gui.widgets.command_palette import CommandRegistry

    class Panel(QWidget):
        commandExecuted = Signal()
        _analysis_class_map = {}

        def __init__(self, *args, **kwargs):
            super().__init__()
            self.viewer2D = SimpleNamespace(set_interaction_state=lambda _: None)
            self.viewer3D = None

        def update_theme(self, theme):
            self.theme = theme

        def set_theme(self, theme):
            self.theme = theme

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
    manager.connect_signals()
    panel = manager.materials_panel
    panel._query_timer.setInterval(0)
    try:
        # Exercise startup construction plus real inherited QSS, without an
        # explicit theme switch that would mask a light-only initial canvas.
        window.setStyleSheet(Path(THEME_DARK_PATH).read_text(encoding="utf-8"))
        assert panel._dock_visible is False
        manager.setup_default_layout()
        window.show()
        drain_events(qapp)
        assert panel.isVisible() and panel.visibleRegion().isEmpty()
        assert not loads and not queries

        window.panel_manager = manager
        window.command_registry = CommandRegistry()
        window.action_manager = SimpleNamespace(get_action=lambda key: None)
        MainWindow._register_palette_commands(window)
        command = next(
            c for c in window.command_registry.all_commands() if c.name == "Materials"
        )
        command.callback()
        drain_events(qapp)
        assert panel._dock_visible is True and panel.sources._active
        assert sum(isinstance(source, InstalledSource) for source in loads) == 1
        assert queries

        for plot in (panel.map, panel.compare):
            assert QColor.fromRgbF(*plot.ax.get_facecolor()).lightness() < 60
            toolbar = plot.toolbar
            toolbar.set_message("(x, y) = (20.13, 0.6401)")
            image = toolbar.grab().toImage()
            assert image.pixelColor(image.width() // 2, 2).lightness() < 60
            assert (
                toolbar.locLabel.palette()
                .color(QPalette.ColorRole.WindowText)
                .lightness()
                > 180
            )
            icon = toolbar.actions()[0].icon().pixmap(24, 24).toImage()
            ink = [
                icon.pixelColor(x, y).lightness()
                for y in range(icon.height())
                for x in range(icon.width())
                if icon.pixelColor(x, y).alpha() > 200
            ]
            assert sum(ink) / len(ink) > 180

        panel.tabs.setCurrentWidget(panel.expert)
        QTest.mouseClick(panel.expert.objective_button, Qt.MouseButton.LeftButton)
        drain_events(qapp)
        assert not manager.optimization_panel.visibleRegion().isEmpty()
        manager.on_sidebar_menu_selected("materials")
        drain_events(qapp)

        manager.analysis_dock.raise_()
        drain_events(qapp)
        assert panel._dock_visible is False and not panel.sources._active
        assert not panel._query_timer.isActive()

        surface = highlighting_connector.get_optic().surfaces[1]
        highlighting_connector.requestBrowseMaterials.emit(surface)
        drain_events(qapp)
        assert not panel.visibleRegion().isEmpty()
        assert highlighting_connector.material_apply.target.surface is surface
        window.setStyleSheet(Path(THEME_LIGHT_PATH).read_text(encoding="utf-8"))
        manager.update_theme("light")
        for plot in (panel.map, panel.compare):
            assert plot.ax.get_facecolor() == (1.0, 1.0, 1.0, 1.0)
    finally:
        window.hide()
        panel.sources.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_stopped_materials_preserves_layout_before_settings_owner_is_destroyed(
    qapp, tmp_path
):
    from shiboken6 import delete

    settings = QSettings(str(tmp_path / "closed.ini"), QSettings.Format.IniFormat)
    connector = make_connector()
    panel = MaterialsPanel(connector, settings=settings, autoload=False)
    panel.show()
    qapp.processEvents()
    connector.calculation_jobs.stopped.emit()
    assert settings.contains("Materials/browseSplitter")
    assert settings.contains("Materials/mapSplitter")
    delete(settings)
    # A late native Hide/visibility event must not call through the dead borrowed
    # settings wrapper or restart a stopped owner during parent destruction.
    panel.hide()
    panel.set_dock_visible(False)
    panel.set_dock_visible(True)
    panel._jobs_stopped()
    assert not panel._activation_timer.isActive()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_accepted_main_window_close_hides_floating_native_docks(qapp):
    from PySide6.QtGui import QCloseEvent

    from optiland_gui.main_window import MainWindow

    window = QMainWindow()
    window.connector = SimpleNamespace(file_operations=None)
    window._file_operations_settled = True
    window._calculation_shutdown_requested = True
    window._calculation_shutdown_complete = True
    dock = QDockWidget("Floating Materials", window)
    dock.setWidget(QWidget())
    window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    dock.setFloating(True)
    other = QDockWidget("Docked", window)
    other.setWidget(QWidget())
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, other)
    stopped = []
    window.panel_manager = SimpleNamespace(
        all_docks=[dock, other],
        python_terminal=SimpleNamespace(shutdown_kernel=lambda: stopped.append(True)),
    )
    window.show()
    dock.show()
    qapp.processEvents()
    assert dock.isVisible()
    event = QCloseEvent()
    MainWindow.closeEvent(window, event)
    assert event.isAccepted() and not dock.isVisible()
    assert stopped == [True]
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
