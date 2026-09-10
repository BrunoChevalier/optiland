"""Combined-only OSLO jobs retain explicit import choices and atomic ownership."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from PySide6.QtWidgets import QDialogButtonBox

from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.widgets.oslo_import_dialog import OsloImportDialog
from tests.test_fileio.test_oslo_catalogs import catalog_lens
from tests.test_fileio.test_oslo_glc import catalog, glc_record
from tests.test_fileio.test_oslo_glc_fixtures import FIXTURES

from .test_calculation_jobs import wait_for


def shutdown(qapp, connector):
    connector.file_operations.begin_close()
    wait_for(qapp, lambda: not connector.file_operations.busy)
    connector.calculation_jobs.shutdown()
    wait_for(qapp, lambda: connector.calculation_jobs._process is None)


def test_strict_async_retry_preserves_options_and_publishes_once(qapp, tmp_path):
    connector = OptilandConnector()
    operations = connector.file_operations
    original = connector.get_optic()
    lens = catalog_lens(tmp_path, "GLA N-BK7")
    glc = catalog(tmp_path, [glc_record(name="N-BK7")])
    hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (lens, glc)
    }
    changes = []
    connector.document_state.committed.connect(changes.append)
    dialog = OsloImportDialog(str(lens), file_operations=operations)
    dialog.strict_checkbox.setChecked(True)
    dialog.show()
    try:
        dialog.attempt_import()
        assert dialog._pending_id is not None
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        wait_for(qapp, lambda: not operations.busy)
        assert connector.get_optic() is original
        assert dialog.isVisible()
        assert "retry" in dialog.error_label.text().lower()
        assert dialog.strict_checkbox.isChecked()
        assert dialog.glass_catalogs == []
        assert not changes

        dialog.glass_catalogs = [str(glc)]
        dialog.attempt_import()
        request = next(iter(operations._requests))
        # The mutable caller options are detached before the process receives them.
        dialog.glass_catalogs.clear()
        assert request
        wait_for(qapp, lambda: not operations.busy)
        assert connector.get_optic() is not original
        assert connector.is_modified()
        assert operations.service.get_current_filepath() is None
        assert len(changes) == 1 and changes[0].structural
        assert not dialog.isVisible()
        assert hashes == {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in hashes
        }
    finally:
        dialog.reject()
        shutdown(qapp, connector)
        dialog.deleteLater()


def test_options_close_cancels_only_its_pending_import(qapp, tmp_path):
    connector = OptilandConnector()
    operations = connector.file_operations
    original = connector.get_optic()
    lens = catalog_lens(tmp_path, "AIR")
    operations.jobs._command = [
        sys.executable,
        "-u",
        str(Path(__file__).with_name("file_worker_fixture.py")),
        "load_file",
        "before",
        "10",
    ]
    operations.jobs._cancel_grace_ms = 40
    dialog = OsloImportDialog(str(lens), file_operations=operations)
    dialog.show()
    try:
        dialog.attempt_import()
        wait_for(qapp, lambda: operations.jobs.active_request is not None)
        dialog.reject()
        wait_for(qapp, lambda: not operations.busy)
        assert connector.get_optic() is original
        assert not dialog.isVisible()
    finally:
        shutdown(qapp, connector)
        dialog.deleteLater()


def test_oslo_options_configuration_is_captured_exactly(qapp, tmp_path):
    connector = OptilandConnector()
    operations = connector.file_operations
    lens = catalog_lens(tmp_path, "AIR")
    catalogs = ["explicit.glc"]
    request = operations.request_load(
        lens,
        "oslo",
        load_options={"strict": True, "glass_catalogs": catalogs, "configuration": 3},
    )
    catalogs.clear()
    assert request.parameters["load_options"] == {
        "strict": True,
        "glass_catalogs": ["explicit.glc"],
        "configuration": 3,
    }
    operations.cancel_load(request.context.identifier)
    shutdown(qapp, connector)


def test_owned_import_applies_selected_inactive_configuration(qapp, tmp_path):
    lens = tmp_path / "configurations.len"
    lens.write_text(
        'LEN NEW "fictional configurations" 1 3; EBR 1; WV .5; TH 1e20; '
        "NXT; GLA FAKE_CO; RD 20; TH 2; NXT; AIR; RD -20; TH 10; "
        "NXT; AIR; END 3; CFG NEW; TH 1 3 4; WV1 3 .7; END; "
        "CFAC 3 NO; CFWT 3 .4",
        encoding="utf-8",
    )
    connector = OptilandConnector()
    try:
        connector.file_operations.request_load(
            lens,
            "oslo",
            load_options={
                "strict": True,
                "glass_catalogs": [str(FIXTURES / "valid" / "version-7-02.glc")],
                "configuration": 3,
            },
        )
        wait_for(qapp, lambda: not connector.file_operations.busy)
        assert connector.get_optic().primary_wavelength == 0.7
        assert connector.get_optic().surfaces[1].thickness == 4
    finally:
        shutdown(qapp, connector)
