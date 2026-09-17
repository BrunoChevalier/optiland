"""Real worker coverage of local-source selection, explicit Apply and persistence."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from optiland.materials.portability import capture_material
from optiland_gui.materials_panel import MaterialsPanel
from optiland_gui.optiland_connector import OptilandConnector
from tests.gui.test_calculation_jobs import wait_for


def test_local_catalog_apply_undo_redo_save_and_reopen_without_source(qapp, tmp_path):
    samples = Path(__file__).parents[2] / "docs" / "samples" / "glassmap"
    catalog = tmp_path / "local.glc"
    catalog.write_bytes((samples / "tutorial.glc").read_bytes())
    connector = OptilandConnector()
    panel = MaterialsPanel(connector, autoload=False)
    panel.resize(1250, 900)
    panel.show()
    outcomes = []
    connector.material_apply.finished.connect(outcomes.append)
    try:
        connector.file_operations.request_load(str(samples / "singlet.json"))
        wait_for(qapp, lambda: not connector.file_operations.busy, timeout=30)
        original = connector.get_optic()
        assert len(original.surfaces) == 4
        old_payload = capture_material(original.surfaces[1].material_post)
        old_path = connector.get_current_filepath()
        old_revision = connector._undo_redo_manager.revision
        assert not connector.is_modified()

        source_id = panel.source_controls.load_path("glc", str(catalog))
        wait_for(
            qapp,
            lambda: connector.materials.source_result(source_id) is not None,
            timeout=30,
        )
        source = connector.materials.source_result(source_id)
        record = next(
            r for r in source.snapshot.materials if r.display_name == "DEMO-CROWN"
        )
        panel.select_key(record.key)
        assert not panel.footer.apply_button.isEnabled()
        # Editor/managed-dock capture is separately exercised by editor tests.
        connector.material_apply.set_target(original.surfaces[1])
        assert panel.footer.apply_button.isEnabled()
        assert "surface 1" in panel.footer.target_label.text()
        assert connector.get_optic() is original
        assert connector._undo_redo_manager.revision == old_revision
        QTest.mouseClick(panel.footer.apply_button, Qt.MouseButton.LeftButton)
        wait_for(qapp, lambda: bool(outcomes), timeout=30)
        assert outcomes[-1].status == "applied", outcomes[-1].message
        changed = connector.get_optic()
        assert changed is not original
        assert capture_material(changed.surfaces[1].material_post) == record.owned_data
        assert capture_material(original.surfaces[1].material_post) == old_payload
        assert connector.get_current_filepath() == old_path
        assert connector.is_modified()
        assert connector._undo_redo_manager.revision == old_revision + 1
        assert connector.material_apply.target.surface is changed.surfaces[1]

        connector.history.submit("undo")
        wait_for(qapp, lambda: not connector.history.busy, timeout=30)
        assert (
            capture_material(connector.get_optic().surfaces[1].material_post)
            == old_payload
        )
        connector.history.submit("redo")
        wait_for(qapp, lambda: not connector.history.busy, timeout=30)
        assert (
            capture_material(connector.get_optic().surfaces[1].material_post)
            == record.owned_data
        )

        saved = tmp_path / "owned-material.json"
        connector.file_operations.request_output(str(saved))
        wait_for(qapp, lambda: not connector.file_operations.busy, timeout=30)
        assert saved.is_file()
        assert not connector.is_modified()
        panel.source_controls.set_selected_source(source_id)
        panel.source_controls.remove_selected()
        catalog.unlink()
        connector.file_operations.request_load(str(saved))
        wait_for(qapp, lambda: not connector.file_operations.busy, timeout=30)
        assert (
            capture_material(connector.get_optic().surfaces[1].material_post)
            == record.owned_data
        )
        assert connector.get_current_filepath() == str(saved)
        assert not connector.is_modified()
    finally:
        panel.hide()
        connector.calculation_jobs.shutdown()
        wait_for(qapp, lambda: connector.calculation_jobs._process is None, timeout=15)
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
