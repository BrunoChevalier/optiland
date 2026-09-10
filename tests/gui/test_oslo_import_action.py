"""Menu dispatch and primary import cancellation without building analysis panels."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QMenuBar, QWidget

from optiland_gui.action_manager import ActionManager
from optiland_gui.main_window import MainWindow


def host():
    window = QWidget()
    window.connector = MagicMock()
    window._confirm_discard_changes = MagicMock(return_value=True)
    window._update_project_name_in_title_bar = MagicMock()
    return window


@pytest.mark.parametrize("discard,lens", [(False, "lens.len"), (True, "")])
def test_lens_cancel_and_unsaved_cancel_never_open_options(qapp, discard, lens):
    window = host()
    window._confirm_discard_changes.return_value = discard
    with (
        patch(
            "optiland_gui.main_window.QFileDialog.getOpenFileName",
            return_value=(lens, ""),
        ) as chooser,
        patch("optiland_gui.main_window.OsloImportDialog") as options,
    ):
        MainWindow.import_oslo_action(window)
    options.assert_not_called()
    window.connector.import_oslo.assert_not_called()
    assert chooser.called == discard


def test_import_action_uses_nonmodal_owned_file_options(qapp):
    window = host()
    with (
        patch(
            "optiland_gui.main_window.QFileDialog.getOpenFileName",
            return_value=("lens.len", ""),
        ) as chooser,
        patch("optiland_gui.main_window.OsloImportDialog") as options,
    ):
        MainWindow.import_oslo_action(window)
        # Closing options alone never imports.
        window.connector.import_oslo.assert_not_called()
        assert (
            options.call_args.kwargs["file_operations"]
            is window.connector.file_operations
        )
    assert "*.len" in chooser.call_args.args[3]
    window.connector.import_oslo.assert_not_called()
    window._update_project_name_in_title_bar.assert_not_called()
    options.return_value.show.assert_called_once()
    options.return_value.exec.assert_not_called()


def test_file_import_menu_contains_wired_oslo_action(qapp):
    window = host()
    for name in (
        "new_system",
        "open_system",
        "save_system",
        "save_system_as",
        "import_zemax",
        "import_codev",
        "import_oslo",
        "export_zemax",
        "export_codev",
    ):
        setattr(window, name + "_action", MagicMock())
    window._populate_gallery_menu = MagicMock()
    manager = window.action_manager = ActionManager(window, window.connector)
    manager._create_file_actions()
    for name in (
        "undo",
        "redo",
        "dock_all",
        "reset_layout",
        "dark_theme",
        "light_theme",
        "about",
    ):
        manager._create_action(name, name)
    menu_bar = QMenuBar(window)
    MainWindow._populate_main_menu_bar(window, menu_bar)
    # Retain the Qt action wrappers while inspecting their associated menus.
    top_actions = menu_bar.actions()
    file_menu = top_actions[0].menu()
    file_actions = file_menu.actions()
    import_menu = next(a.menu() for a in file_actions if a.text() == "&Import")
    action = manager.get_action("import_oslo")
    assert action in import_menu.actions()
    assert "OSLO (.len)" in action.text()
    action.trigger()
    window.import_oslo_action.assert_called_once()
