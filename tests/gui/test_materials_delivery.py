"""Materials is a managed native workspace available through normal navigation."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenuBar, QWidget

from optiland_gui.main_window import MainWindow
from optiland_gui.panel_manager import PanelManager
from optiland_gui.widgets.command_palette import CommandRegistry
from optiland_gui.widgets.sidebar import SidebarWidget


def test_sidebar_materials_is_a_real_destination(qapp):
    sidebar = SidebarWidget()
    selected, wip = [], []
    sidebar.menuSelected.connect(selected.append)
    sidebar.showWipMessage.connect(wip.append)
    next(b["widget"] for b in sidebar._buttons_list if b["name"] == "materials").click()
    assert selected == ["materials"]
    assert wip == []


def test_materials_managed_dock_view_palette_and_theme(
    qapp, highlighting_connector, tmp_path, monkeypatch
):
    # Keep unrelated terminal kernel and optical viewers out of this native dock test.
    import optiland_gui.panel_manager as module

    class Panel(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.viewer2D = SimpleNamespace(set_interaction_state=lambda _: None)
            self.viewer3D = None

        def update_theme(self, theme):
            pass

        def set_theme(self, theme):
            pass

    for name in (
        "ViewerPanel",
        "LensEditor",
        "SystemPropertiesPanel",
        "AnalysisPanel",
        "OptimizationPanel",
        "PythonTerminalWidget",
    ):
        monkeypatch.setattr(module, name, Panel)
    window = QMainWindow()
    window.iface = object()
    window.settings = QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
    )
    manager = PanelManager(window, highlighting_connector)
    manager.create_all_panels(window)
    manager.materials_panel._autoload = False
    manager.setup_default_layout()
    window.show()
    assert manager.materials_dock in manager.get_all_docks()
    assert manager.materials_dock.objectName() == "MaterialsDock"
    manager.materials_dock.hide()
    manager.on_sidebar_menu_selected("materials")
    assert manager.materials_dock.isVisible()
    themes = []
    monkeypatch.setattr(manager.materials_panel, "update_theme", themes.append)
    manager.update_theme("light")
    assert themes == ["light"]
    actions = SimpleNamespace(
        get_action=lambda _: QAction(window), get_actions=lambda *args: []
    )
    host = SimpleNamespace(
        action_manager=actions,
        panel_manager=manager,
        _populate_gallery_menu=lambda _: None,
        command_registry=CommandRegistry(),
    )
    menu = QMenuBar(window)
    MainWindow._populate_main_menu_bar(host, menu)
    view = next(a.menu() for a in menu.actions() if a.text() == "&View")
    assert manager.materials_dock.toggleViewAction() in view.actions()
    manager.analysis_panel._analysis_class_map = {}
    MainWindow._register_palette_commands(host)
    command = next(c for c in host.command_registry._commands if c.name == "Materials")
    manager.materials_dock.hide()
    command.callback()
    assert manager.materials_dock.isVisible()
    window.hide()
    window.deleteLater()
