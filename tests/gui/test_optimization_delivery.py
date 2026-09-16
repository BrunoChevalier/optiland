"""Delivery regressions for optimization lifecycle and candidate previews."""

from __future__ import annotations

import threading

import pytest

from optiland_gui.optimization_panel import OptimizationPanel
from optiland_gui.optimization_preview import OptimizationPreview
from optiland_gui.services.job_records import OpticSnapshot
from optiland_gui.services.layout_tasks import prepare_2d
from tests.gui import test_optimization_jobs
from tests.gui.optimization_fixture import DelayedOptimizer
from tests.gui.test_calculation_jobs import wait_for

owned_service = test_optimization_jobs.owned_service


def test_no_document_reports_error_to_signal_and_callback(qapp, owned_service):
    connector, service = owned_service
    connector._optic = None
    errors, signals = [], []
    service.failed.connect(signals.append)
    service.run(DelayedOptimizer, {}, on_error=errors.append)
    assert errors == signals == ["Open an optical system before running optimization."]
    assert not service.is_running
    panel = OptimizationPanel(connector)
    panel._on_run()
    assert "Open an optical system" in panel.txtLog.toPlainText()
    assert panel.btnRun.isEnabled() and not panel.btnStop.isEnabled()
    panel.close()


def test_repeated_preview_keeps_navigation_without_changing_document(
    qapp, minimal_optic
):
    snapshot = OpticSnapshot.capture(minimal_optic)
    scene = prepare_2d(
        snapshot,
        {"num_rays": 3, "distribution": "line_y"},
        lambda *args: None,
        threading.Event(),
    )
    preview = OptimizationPreview(None)
    context = {
        "document_id": "candidate",
        "surface_identities": tuple(minimal_optic.surfaces),
    }
    try:
        preview.install(scene, context)
        preview.ax.set_xlim(-12, 17)
        preview.ax.set_ylim(-7, 9)
        preview.install(scene, context)
        assert preview.ax.get_xlim() == (-12, 17)
        assert preview.ax.get_ylim() == (-7, 9)
        assert OpticSnapshot.capture(minimal_optic).data == snapshot.data
    finally:
        preview.close()


@pytest.mark.parametrize("converged", [True, False])
def test_completion_owns_request_until_document_or_candidate_notifications_finish(
    qapp, owned_service, converged
):
    connector, service = owned_service
    completed, errors, attempted, changes = [], [], [], []
    signal = connector.opticLoaded if converged else service.candidateAvailable

    def try_restart(*args):
        if args and not args[0]:
            return
        attempted.append(service.is_running)
        service.run(
            DelayedOptimizer,
            {},
            on_finished=lambda _: errors.append("unexpected second run"),
        )

    signal.connect(try_restart)
    connector.document_state.committed.connect(changes.append)
    service.run(
        DelayedOptimizer, {}, on_finished=completed.append, on_error=errors.append
    )
    if not converged:
        connector.notify_change("metadata")
    wait_for(qapp, lambda: completed or errors, timeout=25)
    assert not errors
    assert attempted == [True]
    assert len(completed) == 1
    assert not service.is_running
    if converged:
        assert "was applied" in completed[0]
        assert len(changes) == 1
        assert connector._document_optic is connector.get_optic()
    else:
        assert service.take_candidate() is not None


def test_completion_listener_can_start_next_run(qapp, owned_service):
    connector, service = owned_service
    completed, errors = [], []

    def restart(summary):
        completed.append(summary)
        if len(completed) == 1:
            service.run(
                DelayedOptimizer,
                {},
                on_finished=completed.append,
                on_error=errors.append,
            )

    service.completed.connect(restart)
    service.run(DelayedOptimizer, {}, on_error=errors.append)
    wait_for(qapp, lambda: len(completed) == 3 or errors, timeout=25)
    assert not errors
    assert not service.is_running
    assert len(connector._undo_redo_manager._undo_stack) == 2
