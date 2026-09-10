"""Analysis page identity, cancellation, stale results and presentation isolation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from optiland_gui.analysis_panel import AnalysisPanel
from tests.gui.analysis_job_fakes import connector_for, result_data


@pytest.fixture()
def panel(minimal_optic, qapp):
    connector = connector_for(minimal_optic)
    panel = AnalysisPanel(connector)
    yield panel
    panel.close()
    panel.deleteLater()
    qapp.processEvents()


def run_page(panel, name="Ray Fan"):
    page = panel._execute_analysis(
        panel._analysis_class_map[name], name, {"num_points": 16}, {}
    )
    assert page is not None
    panel.switch_plot_page(len(panel.analysis_results_pages) - 1)
    return page


def test_pending_page_immediate_and_late_result_does_not_steal_focus(panel):
    first = run_page(panel)
    second = run_page(panel)
    assert panel.current_plot_page_index == 1
    panel.connector.calculation_jobs.complete(first["page_id"], result_data())
    assert panel.current_plot_page_index == 1
    assert first["prepared"]
    assert second["prepared"] is None


def test_retains_result_during_rerun_cancel_and_error(panel):
    page = run_page(panel)
    jobs = panel.connector.calculation_jobs
    jobs.complete(page["page_id"], result_data())
    prepared = page["prepared"]
    panel._execute_analysis(None, page["name"], {"num_points": 32}, {}, page=page)
    assert page["prepared"] is prepared
    panel.stop_analysis_slot()
    assert page["state"] == "cancelled"
    assert page["prepared"] is prepared
    panel._execute_analysis(None, page["name"], {"num_points": 64}, {}, page=page)
    jobs.complete(page["page_id"], status="failed", error="Bad optical system")
    assert page["prepared"] is prepared
    panel.connector.toast_manager.notify.assert_called()


def test_outdated_result_labeled_and_theme_never_recalculates(panel):
    page = run_page(panel)
    panel.connector.document_state.change()
    panel.connector.calculation_jobs.complete(page["page_id"], result_data())
    assert "Out of date" in panel.dataInfoLabel.text()
    with patch.object(panel.runner, "run", side_effect=AssertionError("recalculation")):
        panel.update_theme("light")
        panel.update_theme("dark")
        panel.switch_plot_page(0)
    assert "Out of date" in panel.dataInfoLabel.text()


def test_clone_shares_completed_data_and_has_independent_identity(panel):
    page = run_page(panel)
    panel.connector.calculation_jobs.complete(page["page_id"], result_data())
    panel._clone_analysis_page(0)
    cloned = panel.analysis_results_pages[1]
    assert cloned["page_id"] != page["page_id"]
    assert cloned["prepared"] is page["prepared"]
    cloned["constructor_args_used"]["num_points"] = 32
    assert page["constructor_args_used"]["num_points"] == 16


def test_remove_active_page_revokes_target_before_completion(panel):
    page = run_page(panel)
    panel._remove_analysis_page(0)
    assert page["page_id"] in panel.connector.calculation_jobs.cancelled
    assert panel.analysis_results_pages == []
    assert not panel.btnRunAll.isEnabled()


def test_result_retention_is_bounded_without_dropping_previous_data(panel):
    page = run_page(panel)
    jobs = panel.connector.calculation_jobs
    jobs.complete(page["page_id"], result_data())
    retained = page["prepared"]
    panel._execute_analysis(None, page["name"], {"num_points": 32}, {}, page=page)
    oversized = result_data()
    oversized["size_bytes"] = panel._result_budget + 1
    jobs.complete(page["page_id"], oversized)
    assert page["state"] == "failed"
    assert page["prepared"] is retained


def test_empty_system_uses_toast(panel):
    from optiland.optic import Optic

    assert not panel._validate_system_for_analysis(Optic())
    panel.connector.toast_manager.notify.assert_called()


def test_optional_fft_settings_remain_auto_on_reopen(panel):
    page = panel._execute_analysis(None, "FFT PSF", {"num_rays": 32}, {})
    panel.switch_plot_page(0)
    grid = panel.current_settings_widgets["grid_size"]
    assert grid.specialValueText() == "Auto"
    assert panel._get_value_from_spinbox(grid) is None
    panel._apply_settings_and_rerun_analysis_slot()
    assert page["constructor_args_used"]["grid_size"] is None


def test_dirty_settings_survive_completion_and_theme_change(panel):
    page = run_page(panel)
    widget = panel.current_settings_widgets["num_points"]
    widget.setValue(48)
    assert page["settings_dirty"]
    panel.connector.calculation_jobs.complete(page["page_id"], result_data())
    assert widget.value() == 48
    assert "Settings changed" in panel.dataInfoLabel.text()
    panel.update_theme("light")
    assert widget.value() == 48


def test_intentional_stop_does_not_show_worker_termination_error(panel):
    page = run_page(panel)
    panel.connector.calculation_jobs.complete(
        page["page_id"],
        status="cancelled",
        error="Calculation worker exited unexpectedly.",
    )
    assert page["error"] == ""
    panel.connector.toast_manager.notify.assert_not_called()


def test_backend_change_labels_retained_result_outdated(panel):
    from optiland_gui.services.job_records import BackendConfig

    page = run_page(panel)
    panel.connector.calculation_jobs.complete(page["page_id"], result_data())
    with patch.object(BackendConfig, "capture", return_value=BackendConfig("torch")):
        panel._update_page_status(page)
        assert "Out of date" in panel.dataInfoLabel.text()
