"""Configuration selection through the GUI using original synthetic fixtures."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.widgets.oslo_import_dialog import OsloImportDialog
from tests.test_fileio.test_oslo_glc_fixtures import FIXTURES


@pytest.fixture
def configuration_lens(tmp_path):
    path = tmp_path / "fictional.len"
    path.write_text(
        'LEN NEW "fictional configurations" 1 3; EBR 1; WV .5; TH 1e20; '
        "NXT; GLA FAKE_CO; RD 20; TH 2; NXT; AIR; RD -20; TH 10; "
        "NXT; AIR; END 3; CFG NEW; TH 1 3 4; WV1 3 .7; END; "
        "CFAC 3 NO; CFWT 3 .4",
        encoding="utf-8",
    )
    return path


def test_metadata_preview_lists_implicit_and_inactive_configurations(
    qapp, configuration_lens
):
    with patch("optiland_gui.services.file_service.load_oslo_file") as loader:
        dialog = OsloImportDialog(str(configuration_lens))
    loader.assert_not_called()
    choices = dialog.configuration_combo
    assert [choices.itemData(i) for i in range(choices.count())] == [1, 2, 3]
    assert choices.currentData() == 1
    assert "inactive" in choices.itemText(2).lower()
    assert "0.4" in choices.itemText(2)
    dialog.reject()


def test_inactive_snapshot_reaches_real_service_and_preserves_previous_import(
    qapp, configuration_lens
):
    connector = OptilandConnector()
    catalogs = [str(FIXTURES / "valid" / "version-7-02.glc")]
    assert (
        connector.import_oslo(
            str(configuration_lens), strict=True, glass_catalogs=catalogs
        )
        is None
    )
    first = connector.get_optic()

    def import_action(strict, paths, configuration):
        return connector.import_oslo(
            str(configuration_lens),
            strict=strict,
            glass_catalogs=paths,
            configuration=configuration,
        )

    dialog = OsloImportDialog(str(configuration_lens), import_action=import_action)
    dialog.strict_checkbox.setChecked(True)
    dialog.glass_catalogs = catalogs
    dialog.configuration_combo.setCurrentIndex(2)
    dialog.attempt_import()
    assert dialog.result() == QDialog.DialogCode.Accepted
    third = connector.get_optic()
    assert third is not first
    assert first.primary_wavelength == 0.5
    assert first.surfaces[1].thickness == 2
    assert third.primary_wavelength == 0.7
    assert third.surfaces[1].thickness == 4
    assert third.surfaces[1].material_post.n(0.7).item() == pytest.approx(
        1.45 + 0.004 / 0.7 + 0.0002 / 0.7**3.5
    )


def test_catalog_cancellation_and_retry_keep_configuration(qapp, configuration_lens):
    action = MagicMock(side_effect=["Missing catalog definition", None])
    dialog = OsloImportDialog(str(configuration_lens), import_action=action)
    dialog.show()
    dialog.configuration_combo.setCurrentIndex(2)
    dialog.strict_checkbox.setChecked(True)
    dialog.glass_catalogs = ["discard.glc"]
    with patch(
        "optiland_gui.widgets.oslo_import_dialog.QFileDialog.getExistingDirectory",
        return_value="",
    ):
        dialog.choose_catalogs()
    dialog.attempt_import()
    assert dialog.isVisible()
    assert dialog.configuration_combo.currentData() == 3
    assert dialog.strict_checkbox.isChecked()
    assert dialog.glass_catalogs == []
    dialog.attempt_import()
    assert action.call_args.args == (True, [], 3)


@pytest.mark.parametrize(
    "content", [None, 'LEN NEW "bad" 1 1; NXT; END 1; CFG NEW; TH 9 2 3; END']
)
def test_bad_metadata_disables_import_and_can_be_reloaded(
    qapp, configuration_lens, content
):
    good = configuration_lens.read_text(encoding="utf-8")
    if content is None:
        configuration_lens.unlink()
    else:
        configuration_lens.write_text(content, encoding="utf-8")
    action = MagicMock()
    dialog = OsloImportDialog(str(configuration_lens), import_action=action)
    assert dialog.error_label.text()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.attempt_import()
    action.assert_not_called()
    configuration_lens.write_text(good, encoding="utf-8")
    dialog.reload_configurations()
    assert dialog.configuration_combo.currentData() == 1
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert not dialog.error_label.text()
    dialog.configuration_combo.setCurrentIndex(2)
    dialog.reload_configurations()
    assert dialog.configuration_combo.currentData() == 3
    dialog.reject()


def test_preview_does_not_relax_or_enforce_import_strictness(qapp, configuration_lens):
    with configuration_lens.open("a", encoding="utf-8") as stream:
        stream.write("; UNMAPPED_COMMAND 1")
    dialog = OsloImportDialog(str(configuration_lens))
    assert dialog.configuration_combo.count() == 3
    assert not dialog.strict_checkbox.isChecked()
    dialog.strict_checkbox.setChecked(True)
    assert dialog.configuration_combo.count() == 3
    dialog.reject()


def test_reload_resets_removed_configuration_but_keeps_other_options(
    qapp, configuration_lens
):
    dialog = OsloImportDialog(str(configuration_lens))
    dialog.configuration_combo.setCurrentIndex(2)
    dialog.strict_checkbox.setChecked(True)
    dialog.glass_catalogs = ["keep.glc"]
    text = configuration_lens.read_text(encoding="utf-8")
    configuration_lens.write_text(text.split("CFG NEW")[0], encoding="utf-8")
    dialog.reload_configurations()
    assert dialog.configuration_combo.currentData() == 1
    assert dialog.configuration_combo.count() == 1
    assert dialog.strict_checkbox.isChecked()
    assert dialog.glass_catalogs == ["keep.glc"]
    dialog.reject()


@pytest.mark.parametrize("configuration,extra", [(9, ""), (3, "; GTO 1; PY 0")])
def test_invalid_or_unsupported_configuration_preserves_document(
    qapp, configuration_lens, configuration, extra
):
    if extra:
        text = configuration_lens.read_text(encoding="utf-8")
        configuration_lens.write_text(
            text.replace("NXT; AIR; END 3", "NXT; AIR" + extra + "; END 3"),
            encoding="utf-8",
        )
    connector = OptilandConnector()
    connector._file_service._current_filepath = "saved.json"
    connector.set_modified(False)
    history = connector._undo_redo_manager
    history.add_state({"saved": 1})
    history.add_state({"saved": 2})
    history.undo({"saved": 3})
    before = connector.get_optic()
    stacks = deepcopy((history._undo_stack, history._redo_stack))
    error = connector.import_oslo(
        str(configuration_lens),
        strict=True,
        glass_catalogs=[str(FIXTURES / "valid" / "version-7-02.glc")],
        configuration=configuration,
    )
    assert "configuration" in error.lower()
    assert connector.get_optic() is before
    assert connector._file_service.get_current_filepath() == "saved.json"
    assert not connector.is_modified()
    assert (history._undo_stack, history._redo_stack) == stacks
