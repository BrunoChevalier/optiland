"""OSLO catalog choice, cancellation and exact import-option forwarding."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from optiland_gui.widgets.oslo_import_dialog import (
    CatalogSelectionDialog,
    OsloImportDialog,
)


class FakeSearch(QObject):
    batch_ready = Signal(int, list)
    completed = Signal(int, dict)

    def __init__(self):
        super().__init__()
        self.identifier = 0
        self.cancel_count = 0

    def start(self, root):
        self.identifier += 1
        return self.identifier

    def cancel(self):
        self.cancel_count += 1

    def finish(self, complete=True, reason=""):
        self.completed.emit(
            self.identifier, {"complete": complete, "reason": reason, "skipped": 0}
        )


@pytest.fixture
def options_lens(tmp_path):
    path = tmp_path / "fictional.len"
    path.write_text(
        'LEN NEW "fictional" 1 1; EBR 1; TH 1e20; NXT; END 1', encoding="utf-8"
    )
    return str(path)


def selection(tmp_path):
    search = FakeSearch()
    dialog = CatalogSelectionDialog(str(tmp_path), search=search)
    return dialog, search


def test_checks_bulk_actions_duplicate_basenames_and_late_batches(qapp, tmp_path):
    dialog, search = selection(tmp_path)
    paths = [str(tmp_path / name) for name in ("z.glc", "a/shared.GLC", "b/shared.glc")]
    search.batch_ready.emit(search.identifier, paths[:2])
    assert dialog.checked_paths() == sorted(paths[:2], key=str.casefold)
    first = dialog._items[os.path.normcase(paths[0])]
    first.setCheckState(0, Qt.CheckState.Unchecked)
    search.batch_ready.emit(search.identifier, paths)
    assert dialog.checked_paths() == sorted(paths[1:], key=str.casefold)
    assert dialog.tree.topLevelItemCount() == 3
    assert dialog.count_label.text() == "2 / 3 selected"
    assert {dialog.tree.topLevelItem(i).text(1) for i in range(3)} == {".", "a", "b"}
    dialog.uncheck_all.click()
    assert dialog.checked_paths() == []
    dialog.check_all.click()
    assert dialog.checked_paths() == sorted(paths, key=str.casefold)
    search.finish()
    dialog.accept()
    assert dialog.selected_paths == sorted(paths, key=str.casefold)


@pytest.mark.parametrize("dismiss", ["cancel", "escape", "close"])
def test_selection_dismissal_discards_checks_and_late_results(qapp, tmp_path, dismiss):
    dialog, search = selection(tmp_path)
    dialog.show()
    identifier = search.identifier
    search.batch_ready.emit(identifier, [str(tmp_path / "a.glc")])
    if dismiss == "cancel":
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
    elif dismiss == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog.close()
    search.batch_ready.emit(identifier, [str(tmp_path / "late.glc")])
    search.finish()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.selected_paths == []
    assert dialog.tree.topLevelItemCount() == 1
    assert search.cancel_count >= 2


def test_replacement_starts_fresh_and_ignores_previous_search(qapp, tmp_path):
    dialog, search = selection(tmp_path)
    old = search.identifier
    search.batch_ready.emit(old, [str(tmp_path / "a.glc")])
    dialog.uncheck_all.click()
    root = tmp_path / "new"
    dialog.start_search(str(root))
    search.batch_ready.emit(old, [str(tmp_path / "late.glc")])
    search.batch_ready.emit(search.identifier, [str(root / "new.glc")])
    assert dialog.checked_paths() == [str(root / "new.glc")]
    dialog.reject()


@pytest.mark.parametrize("partial", [False, True])
def test_empty_and_incomplete_search_allow_explicit_subset_acceptance(
    qapp, tmp_path, partial
):
    dialog, search = selection(tmp_path)
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    if partial:
        search.batch_ready.emit(search.identifier, [str(tmp_path / "partial.glc")])
    search.finish(complete=not partial, reason="Time limit reached")
    assert ok.isEnabled()
    assert (
        "Search incomplete" if partial else "No GLC catalogs found"
    ) in dialog.status_label.text()
    dialog.uncheck_all.click()
    ok.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_paths == []


def test_directory_cancellation_clears_previous_selection(qapp, tmp_path):
    dialog, search = selection(tmp_path)
    search.batch_ready.emit(search.identifier, [str(tmp_path / "a.glc")])
    with patch(
        "optiland_gui.widgets.oslo_import_dialog.QFileDialog.getExistingDirectory",
        return_value="",
    ):
        dialog.choose_directory()
    assert (
        dialog.result() == QDialog.DialogCode.Rejected and dialog.selected_paths == []
    )


def test_strictness_survives_catalog_cancel_and_catalogs_survive_strictness(
    qapp, tmp_path, options_lens
):
    options = OsloImportDialog(options_lens)
    assert not options.strict_checkbox.isChecked()
    options.glass_catalogs = ["old.glc"]
    options.strict_checkbox.setChecked(True)
    assert options.glass_catalogs == ["old.glc"]
    with patch(
        "optiland_gui.widgets.oslo_import_dialog.QFileDialog.getExistingDirectory",
        return_value="",
    ) as chooser:
        options.choose_catalogs()
    assert chooser.call_args.args[2] == str(tmp_path)
    assert options.glass_catalogs == []
    assert options.strict_checkbox.isChecked()


@pytest.mark.parametrize(
    "accepted,paths",
    [(True, ["a.glc", "sub/b.glc"]), (True, []), (False, ["discard.glc"])],
)
def test_options_use_only_accepted_catalog_checks(
    qapp, tmp_path, accepted, paths, options_lens
):
    options = OsloImportDialog(options_lens)
    options.strict_checkbox.setChecked(True)
    options.glass_catalogs = ["previous.glc"]
    with (
        patch(
            "optiland_gui.widgets.oslo_import_dialog.QFileDialog.getExistingDirectory",
            return_value=str(tmp_path),
        ),
        patch(
            "optiland_gui.widgets.oslo_import_dialog.CatalogSelectionDialog"
        ) as selection_type,
    ):
        selection_type.return_value.exec.return_value = (
            QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
        )
        selection_type.return_value.selected_paths = paths
        options.choose_catalogs()
    assert options.glass_catalogs == (paths if accepted else [])
    assert options.strict_checkbox.isChecked()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("catalogs", [[], ["first.glc", "sub/second.glc"]])
def test_import_forwards_exact_options_and_keeps_failures_reviewable(
    qapp, strict, catalogs, options_lens
):
    action = MagicMock(side_effect=["Missing GLASS_A", None])
    dialog = OsloImportDialog(options_lens, import_action=action)
    dialog.show()
    dialog.strict_checkbox.setChecked(strict)
    dialog.glass_catalogs = list(catalogs)
    dialog.attempt_import()
    assert dialog.isVisible()
    assert "GLASS_A" in dialog.error_label.text()
    assert dialog.strict_checkbox.isChecked() == strict
    assert dialog.glass_catalogs == catalogs
    dialog.attempt_import()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert action.call_args.args == (strict, catalogs, 1)


def test_overall_cancel_never_imports(qapp, options_lens):
    action = MagicMock()
    options = OsloImportDialog(options_lens, import_action=action)
    options.reject()
    action.assert_not_called()


def test_worker_results_cannot_select_outside_root_or_cdb(qapp, tmp_path):
    dialog, search = selection(tmp_path)
    search.batch_ready.emit(
        search.identifier,
        [str(tmp_path.parent / "outside.glc"), str(tmp_path / "bad.cdb")],
    )
    assert dialog.checked_paths() == []
    dialog.reject()


def test_directory_replacement_ignores_old_completion_and_reports_skips(qapp, tmp_path):
    dialog, search = selection(tmp_path)
    old = search.identifier
    new_root = tmp_path / "replacement"
    with patch(
        "optiland_gui.widgets.oslo_import_dialog.QFileDialog.getExistingDirectory",
        return_value=str(new_root),
    ):
        dialog.choose_directory()
    search.completed.emit(old, {"complete": True})
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    search.completed.emit(
        search.identifier,
        {
            "complete": False,
            "reason": "Some entries could not be read",
            "skipped": 2,
        },
    )
    assert "2 entries could not be read" in dialog.status_label.text()
    assert ok.isEnabled()
    dialog.reject()


def test_owned_search_is_disposed_when_selection_closes(qapp, tmp_path):
    with patch("optiland_gui.widgets.oslo_import_dialog.CatalogSearch") as search_type:
        dialog = CatalogSelectionDialog(str(tmp_path))
        dialog.reject()
    search_type.return_value.cancel.assert_called()
    search_type.return_value.dispose.assert_called_once()


def test_options_can_be_accepted_without_import_callback(qapp, options_lens):
    dialog = OsloImportDialog(options_lens)
    dialog.attempt_import()
    assert dialog.result() == QDialog.DialogCode.Accepted
