"""An exception without a message must remain a failed, retryable import."""

from __future__ import annotations

from unittest.mock import patch

from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.widgets.oslo_import_dialog import OsloImportDialog
from tests.test_fileio.test_oslo_catalogs import catalog_lens


def test_empty_exception_keeps_import_options_open(qapp, tmp_path):
    path = str(catalog_lens(tmp_path, "GLA 1.6"))
    connector = OptilandConnector()
    previous = connector.get_optic()
    dialog = OsloImportDialog(
        path,
        import_action=lambda strict, catalogs, configuration: connector.import_oslo(
            path, strict=strict, glass_catalogs=catalogs, configuration=configuration
        ),
    )
    dialog.show()
    try:
        with patch.object(
            connector, "_initialize_optic_structure", side_effect=RuntimeError()
        ):
            dialog.attempt_import()
        assert connector.get_optic() is previous
        assert dialog.isVisible()
        assert "RuntimeError" in dialog.error_label.text()
    finally:
        dialog.close()
