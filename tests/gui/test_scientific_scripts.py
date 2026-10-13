"""Scientific ownership, bounded output and preserved GUI-extension contracts."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.services.scientific_scripts import ScientificScripts
from optiland_gui.services.scientific_tasks import (
    LINE_CHARACTERS,
    OUTPUT_BYTES,
    OUTPUT_LINES,
    BoundedOutput,
    GuiCommands,
)

from .test_calculation_jobs import wait_for


def test_script_worker_crash_reports_failure_without_an_applicable_candidate(
    qapp, scripts
):
    connector, service, output, statuses, results = scripts
    original = connector.get_optic()
    service.run("import os\nos._exit(3)")
    wait_for(qapp, lambda: results, timeout=30)
    assert results[-1].status == "failed"
    assert "Calculation worker exited unexpectedly" in output[-1]
    assert not service.can_apply()
    assert connector.get_optic() is original


def test_script_candidate_restore_failure_preserves_model_and_history(
    scripts, monkeypatch
):
    from optiland_gui.services.prepared_optic import PreparedOptic

    connector, service, output, statuses, results = scripts
    original = connector.get_optic()
    monkeypatch.setattr(service.jobs, "_dispatch", lambda: None)
    service.run("print('result')")
    service.candidate = PreparedOptic.capture(original)
    undo = list(connector._undo_redo_manager._undo_stack)
    redo = list(connector._undo_redo_manager._redo_stack)

    def reject(*args, **kwargs):
        raise ValueError("candidate restore failed")

    monkeypatch.setattr(PreparedOptic, "restore", reject)
    assert not service.apply_candidate()
    assert connector.get_optic() is original
    assert connector._undo_redo_manager._undo_stack == undo
    assert connector._undo_redo_manager._redo_stack == redo
    assert "candidate restore failed" in statuses[-1][0]


@pytest.fixture
def scripts(qapp, minimal_optic):
    connector = OptilandConnector()
    connector._optic = minimal_optic
    service = ScientificScripts(connector)
    service.jobs._cancel_grace_ms = 40
    output, statuses, results = [], [], []
    service.output_changed.connect(output.append)
    service.status_changed.connect(lambda *args: statuses.append(args))
    service.jobs.finished.connect(results.append)
    yield connector, service, output, statuses, results
    connector._calculation_shutdown_started = True
    for jobs in tuple(connector.calculation_services):
        jobs.shutdown()
    wait_for(
        qapp,
        lambda: all(jobs._process is None for jobs in connector.calculation_services),
    )


@pytest.mark.parametrize(
    "text",
    ["😀" * 100000, "x\n" * 100000, "z" * 1000000, ("€" * 3000 + "\n") * 10],
    ids=["unicode", "many-lines", "long-line", "byte-limit"],
)
def test_output_limits_bytes_lines_and_single_line(text):
    output = BoundedOutput()
    assert output.write(text) == len(text)
    assert output.truncated
    assert len(output.text.encode("utf-8")) <= OUTPUT_BYTES
    assert output.text.count("\n") <= OUTPUT_LINES
    assert max(map(len, output.text.splitlines())) <= LINE_CHARACTERS
    assert "truncated" in output.value


def test_gui_command_vocabulary_and_count_are_bounded():
    commands = GuiCommands()
    with pytest.raises(ValueError):
        commands.show_panel("arbitrary widget")
    with pytest.raises(AttributeError):
        commands.get_main_window()
    for _ in range(32):
        commands.refresh_views()
    with pytest.raises(ValueError, match="32"):
        commands.show_panel("viewer")


def test_rejected_new_run_revokes_older_intent(qapp, scripts):
    connector, service, output, statuses, results = scripts
    first = service.run("print('older request')")
    assert service.run("x" * (1024 * 1024 + 1)) is None
    wait_for(qapp, lambda: results)
    assert results[-1].request is first
    assert results[-1].status == "cancelled"
    assert service.request is None and not service.can_apply()


def test_scientific_run_keeps_gui_and_preview_queue_free_and_apply_is_explicit(
    qapp, scripts
):
    connector, service, output, statuses, results = scripts
    original = connector.get_optic()
    connector.calculation_jobs._command = [
        sys.executable,
        "-u",
        str(Path(__file__).with_name("calculation_worker_fixture.py")),
    ]
    previews, ticks, commands = [], [], []
    connector.calculation_jobs.finished.connect(previews.append)
    service.commands_ready.connect(commands.append)
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    request = service.run(
        "import time\nprint('started')\n"
        "optic.surfaces[1].comment = 'script result'\n"
        "time.sleep(0.5)\ngui.show_panel('viewer')\nprint('done')"
    )
    connector.calculation_jobs.submit("preview", "unused", None, {"value": 123})
    wait_for(qapp, lambda: results and previews)
    timer.stop()
    assert results[-1].request is request
    assert previews[-1].data == 123
    assert len(ticks) > 15
    assert connector.get_optic() is original
    assert original.surfaces[1].comment != "script result"
    assert "done" in output[-1]
    assert commands == [(("show_panel", "viewer"),)]
    assert service.can_apply()
    assert service.apply_candidate()
    assert connector.get_optic().surfaces[1].comment == "script result"
    assert connector._undo_redo_manager.can_undo()
    connector.undo()
    assert connector.get_optic().surfaces[1].comment != "script result"


def test_comment_edit_revokes_candidate_and_commands(qapp, scripts):
    connector, service, output, statuses, results = scripts
    commands = []
    service.commands_ready.connect(commands.append)
    service.run("import time\ntime.sleep(.2)\ngui.show_panel('viewer')")
    connector.get_optic().surfaces[1].comment = "intervening edit"
    connector.notify_change("metadata", surface_indices=(1,), columns=(1,))
    wait_for(qapp, lambda: results)
    assert service.candidate is not None
    assert not service.can_apply()
    assert not service.apply_candidate()
    assert connector.get_optic().surfaces[1].comment == "intervening edit"
    assert not commands


def test_script_error_preserves_document_and_bounds_output(qapp, scripts):
    connector, service, output, statuses, results = scripts
    original = connector.get_optic()
    service.run("print('x' * 1000000)\nraise ValueError('failed calculation')")
    wait_for(qapp, lambda: results)
    assert connector.get_optic() is original
    assert service.candidate is None
    assert "Output truncated" in output[-1]
    assert "failed calculation" in output[-1]
    assert len(output[-1]) < 2 * OUTPUT_BYTES + 100


def test_stop_and_restart_reap_isolated_process_and_preserve_document(qapp, scripts):
    connector, service, output, statuses, results = scripts
    original = connector.get_optic()
    service.run("print('loop entered')\nwhile True: pass")
    wait_for(qapp, lambda: any("loop entered" in text for text in output))
    started = time.monotonic()
    service.cancel()
    assert time.monotonic() - started < 0.1
    wait_for(qapp, lambda: results)
    assert results[-1].status == "cancelled"
    assert connector.get_optic() is original
    old = service.jobs
    service.restart()
    wait_for(qapp, lambda: service.jobs is not old)
    assert old not in connector.calculation_services
    assert service.jobs in connector.calculation_services
    service.run("print('new process')")
    wait_for(qapp, lambda: service.candidate is not None)
    assert "new process" in output[-1]


def test_backend_change_revokes_every_registered_service_without_persisted_edit(
    qapp, scripts, monkeypatch
):
    from optiland_gui.services.job_records import BackendConfig

    connector, service, output, statuses, results = scripts
    called = []
    for index, jobs in enumerate(connector.calculation_services):
        monkeypatch.setattr(
            jobs, "cancel_cancellable", lambda i=index: called.append(i)
        )
    token = connector.document_state.edit_token
    monkeypatch.setattr(BackendConfig, "capture", lambda: BackendConfig("torch"))
    connector._check_calculation_backend()
    assert called == [0, 1]
    assert connector.document_state.edit_token == token


def test_main_window_close_waits_for_both_services(qapp, scripts):
    from optiland_gui.main_window import MainWindow

    connector, service, output, statuses, results = scripts
    closed = []

    class Window(QWidget):
        closeEvent = MainWindow.closeEvent
        _calculations_stopped = MainWindow._calculations_stopped
        _files_settled = MainWindow._files_settled
        _file_close_aborted = MainWindow._file_close_aborted
        _confirm_discard_changes = MainWindow._confirm_discard_changes
        _confirm_close_intent = MainWindow._confirm_close_intent

    window = Window()
    window.connector = connector
    window.panel_manager = SimpleNamespace(
        python_terminal=SimpleNamespace(shutdown_kernel=lambda: closed.append(True))
    )
    window.show()
    service.run("print('loop entered')\nwhile True: pass")
    wait_for(qapp, lambda: any("loop entered" in text for text in output))
    assert not window.close()
    assert window.isVisible()
    wait_for(qapp, lambda: not window.isVisible())
    assert closed == [True]
    assert all(jobs._process is None for jobs in connector.calculation_services)
    window.deleteLater()


def test_original_console_keeps_real_connector_iface_and_editor_execution(
    qapp, monkeypatch
):
    from optiland_gui.widgets.python_terminal import PythonTerminalWidget

    connector = OptilandConnector()
    iface = object()
    widget = PythonTerminalWidget(
        custom_variables={"connector": connector, "iface": iface}
    )
    try:
        namespace = widget.kernel_manager.kernel.shell.user_ns
        assert namespace["connector"] is connector
        assert namespace["iface"] is iface
        submitted = []
        monkeypatch.setattr(
            widget.kernel_client,
            "execute",
            lambda code, **kwargs: submitted.append(code),
        )
        widget._get_current_editor().setPlainText("connector.get_optic()")
        widget._run_script_from_editor()
        assert submitted == ["connector.get_optic()"]
        assert widget.scientific_panel.service.jobs is not connector.calculation_jobs
        scientific = []
        monkeypatch.setattr(widget.scientific_panel.service, "run", scientific.append)
        widget._run_scientific_from_editor()
        assert scientific == ["connector.get_optic()"]
        widget._get_current_editor().setPlainText("   ")
        widget._run_scientific_from_editor()
        assert len(scientific) == 1
        widget._get_current_editor().setPlainText("connector.get_optic()")
        widget.scientific_panel.service.restart()
        assert widget._get_current_editor().toPlainText() == "connector.get_optic()"
    finally:
        connector._calculation_shutdown_started = True
        for jobs in tuple(connector.calculation_services):
            jobs.shutdown()
        wait_for(
            qapp,
            lambda: all(j._process is None for j in connector.calculation_services),
        )
        widget.shutdown_kernel()
        widget.deleteLater()


@pytest.mark.parametrize(
    "signal_name", ["output_changed", "candidate_changed", "status_changed"]
)
def test_completion_listener_starting_new_run_cannot_publish_old_candidate(
    scripts, monkeypatch, signal_name
):
    from optiland_gui.services.job_records import JobResult
    from optiland_gui.services.prepared_optic import PreparedOptic

    connector, service, output, statuses, results = scripts
    monkeypatch.setattr(service.jobs, "_dispatch", lambda: None)
    old = service.run("print('older')")
    candidate = PreparedOptic.capture(connector.get_optic())
    newer = []
    commands = []
    service.commands_ready.connect(commands.append)

    def restart_once(*args):
        if not newer:
            newer.append(None)
            newer[0] = service.run("print('newer')")

    getattr(service, signal_name).connect(restart_once)
    service._finished(
        JobResult(
            old,
            "succeeded",
            {
                "output": "older output",
                "error": "",
                "candidate": candidate,
                "commands": (("show_panel", "viewer"),),
            },
            current=True,
        )
    )
    assert service.request is newer[0]
    assert service.candidate is None
    assert not service.can_apply()
    assert not commands
    assert statuses[-1] == ("Running scientific script…", True)


@pytest.mark.parametrize("notification", ["progress", "failure"])
def test_output_listener_starting_new_run_keeps_new_status(
    scripts, monkeypatch, notification
):
    from optiland_gui.services.job_records import JobResult

    connector, service, output, statuses, results = scripts
    monkeypatch.setattr(service.jobs, "_dispatch", lambda: None)
    old = service.run("print('older')")
    newer = []

    def restart_once(text):
        if not newer:
            newer.append(None)
            newer[0] = service.run("print('newer')")

    service.output_changed.connect(restart_once)
    if notification == "progress":
        service._progress(old, {"stage": "Older stage", "details": {"output": "old"}})
    else:
        service._finished(JobResult(old, "failed", error="old failure"))
    assert service.request is newer[0]
    assert statuses[-1] == ("Running scientific script…", True)
    service._progress(old, {"stage": "stale"})
    service._finished(JobResult(old, "failed", error="late failure"))
    assert statuses[-1] == ("Running scientific script…", True)


def test_apply_installs_prepared_model_without_gui_catalog_or_optical_work(
    qapp, scripts, monkeypatch
):
    from optiland.materials.base import BaseMaterial
    from optiland.optic import Optic

    connector, service, output, statuses, results = scripts
    service.run("optic.surfaces[1].comment = 'prepared candidate'")
    wait_for(qapp, lambda: service.candidate is not None)
    original = connector.get_optic()
    observed = []
    connector._undo_redo_manager.undoStackAvailabilityChanged.connect(
        lambda available: observed.append(
            (available, connector.get_optic().surfaces[1].comment)
        )
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Apply reconstructed or solved the model on the GUI thread")

    monkeypatch.setattr(Optic, "from_dict", forbidden)
    monkeypatch.setattr(BaseMaterial, "from_dict", forbidden)
    monkeypatch.setattr(type(original.updater), "update", forbidden)
    assert service.apply_candidate()
    assert connector.get_optic() is not original
    assert connector.get_optic().surfaces[1].comment == "prepared candidate"
    assert observed and all(comment == "prepared candidate" for _, comment in observed)
    assert connector._undo_redo_manager.can_undo()


def test_apply_rechecks_revision_after_capture_and_backend_before_restore(
    scripts, monkeypatch
):
    from dataclasses import replace

    from optiland_gui.services.job_records import BackendConfig
    from optiland_gui.services.prepared_optic import PreparedOptic

    connector, service, output, statuses, results = scripts
    monkeypatch.setattr(service.jobs, "_dispatch", lambda: None)
    service.run("print('candidate')")
    original = connector.get_optic()
    candidate = PreparedOptic.capture(original)
    service.candidate = replace(candidate, backend=BackendConfig("unavailable"))
    assert not service.can_apply()
    assert not service.apply_candidate()
    service.candidate = candidate
    capture = connector._capture_optic_state

    def changed_capture():
        state = capture()
        connector.notify_change("metadata", surface_indices=(1,), columns=(1,))
        return state

    monkeypatch.setattr(connector, "_capture_optic_state", changed_capture)
    assert not service.apply_candidate()
    assert connector.get_optic() is original
    assert not connector._undo_redo_manager.can_undo()


@pytest.mark.parametrize("action", ["cancel", "apply_candidate"])
def test_completion_callback_can_stop_or_apply_without_overwriting_its_status(
    scripts, monkeypatch, action
):
    from optiland_gui.services.job_records import JobResult
    from optiland_gui.services.prepared_optic import PreparedOptic

    connector, service, output, statuses, results = scripts
    monkeypatch.setattr(service.jobs, "_dispatch", lambda: None)
    request = service.run("print('candidate')")
    candidate = PreparedOptic.capture(connector.get_optic())
    service.output_changed.connect(lambda text: getattr(service, action)())
    service._finished(
        JobResult(
            request,
            "succeeded",
            {
                "output": "complete",
                "error": "",
                "candidate": candidate,
                "commands": (),
            },
            current=True,
        )
    )
    assert service.candidate is None
    assert statuses[-1][1] is False
    assert statuses[-1][0] == (
        "Script cancelled."
        if action == "cancel"
        else "Script result applied. Undo is available."
    )


def test_restart_and_run_are_guarded_during_restart_and_shutdown(scripts, monkeypatch):
    connector, service, output, statuses, results = scripts
    monkeypatch.setattr(service.jobs, "shutdown", lambda: None)
    service.restart()
    old = service.jobs
    service.restart()
    assert service.run("print('too soon')") is None
    assert service.jobs is old
    connector._calculation_shutdown_started = True
    service._restart_finished()
    assert old not in connector.calculation_services
    assert service.run("print('after close')") is None
    service.restart()
    assert not service._restarting


def test_scientific_panel_activity_is_delayed_and_stops_cleanly(qapp):
    from optiland_gui.widgets.scientific_script_panel import ScientificScriptPanel

    connector = OptilandConnector()
    panel = ScientificScriptPanel(connector)
    try:
        panel.show()
        panel._status_changed("Working", True)
        assert panel.cancel.isEnabled()
        assert not panel.activity.isVisible()
        panel._status_changed("Still working", True)
        wait_for(qapp, panel.activity.isVisible)
        panel._status_changed("Finished", False)
        assert not panel.cancel.isEnabled()
        assert not panel.activity.isVisible()
        assert not panel.timer.isActive()
    finally:
        for jobs in tuple(connector.calculation_services):
            jobs.shutdown()
        wait_for(
            qapp,
            lambda: all(j._process is None for j in connector.calculation_services),
        )
        panel.close()
        panel.deleteLater()


def test_gui_commands_recheck_currency_between_actions(qapp, scripts, monkeypatch):
    from optiland_gui.widgets.python_terminal import PythonTerminalWidget

    connector, service, output, statuses, results = scripts
    actions = []
    current = [True]
    monkeypatch.setattr(service, "can_apply", lambda: current[0])
    dock = SimpleNamespace(
        show=lambda: actions.append("show"), raise_=lambda: actions.append("raise")
    )
    window = SimpleNamespace(
        panel_manager=SimpleNamespace(
            viewer_dock=dock,
            analysis_dock=dock,
            lens_editor_dock=dock,
        )
    )
    iface = SimpleNamespace(
        get_main_window=lambda: window,
        get_viewer_panel=lambda: SimpleNamespace(
            update=lambda: actions.append("repaint")
        ),
    )
    host = SimpleNamespace(
        scientific_panel=SimpleNamespace(service=service),
        injected_variables={"iface": iface},
    )
    PythonTerminalWidget._apply_scientific_commands(
        host,
        (
            ("refresh_views",),
            ("show_panel", "analysis"),
            ("show_panel", "invalid"),
            ("unknown",),
        ),
    )
    assert actions == ["repaint", "show", "raise"]
    dock.show = lambda: current.__setitem__(0, False)
    PythonTerminalWidget._apply_scientific_commands(
        host,
        (
            ("show_panel", "viewer"),
            ("refresh_views",),
        ),
    )
    assert actions == ["repaint", "show", "raise", "raise"]
    current[0] = True
    host.injected_variables.clear()
    PythonTerminalWidget._apply_scientific_commands(
        host, (("refresh_views",), ("show_panel", "viewer"))
    )
    assert len(actions) == 4


def test_worker_prepares_owned_candidate_for_each_backend(
    set_test_backend, minimal_optic
):
    from optiland_gui.services.job_records import BackendConfig, OpticSnapshot
    from optiland_gui.services.scientific_tasks import execute_scientific

    original_comment = minimal_optic.surfaces[1].comment
    backend = BackendConfig.capture()
    result = execute_scientific(
        OpticSnapshot.capture(minimal_optic),
        {"code": "optic.surfaces[1].comment = 'worker result'\nprint('done')"},
        lambda *a, **kw: None,
        threading.Event(),
    )
    assert not result["error"]
    assert result["output"] == "done\n"
    assert result["candidate"].backend == backend
    restored = result["candidate"].restore()
    assert restored.surfaces[1].comment == "worker result"
    assert minimal_optic.surfaces[1].comment == original_comment


def test_worker_rejects_oversized_or_cancelled_source_before_restoring():
    from optiland_gui.services.job_records import CalculationCancelled
    from optiland_gui.services.scientific_tasks import SCRIPT_BYTES, execute_scientific

    with pytest.raises(ValueError, match="1 MiB"):
        execute_scientific(
            None, {"code": "x" * (SCRIPT_BYTES + 1)}, None, threading.Event()
        )
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(CalculationCancelled):
        execute_scientific(
            None, {"code": "raise RuntimeError('should not run')"}, None, cancelled
        )


def test_worker_normalizes_gallery_subclass_and_rejects_custom_trace(minimal_optic):
    from optiland.optic import Optic
    from optiland_gui.services.job_records import OpticSnapshot
    from optiland_gui.services.scientific_tasks import execute_scientific

    snapshot = OpticSnapshot.capture(minimal_optic)
    result = execute_scientific(
        snapshot,
        {
            "code": "from optiland.samples.objectives import CookeTriplet\n"
            "optic = CookeTriplet()"
        },
        lambda *a, **kw: None,
        threading.Event(),
    )
    assert not result["error"]
    assert type(result["candidate"].restore()) is Optic
    assert result["candidate"].restore().surfaces.num_surfaces == 8
    result = execute_scientific(
        snapshot,
        {"code": "optic.trace = lambda *a, **kw: None"},
        lambda *a, **kw: None,
        threading.Event(),
    )
    assert result["candidate"] is None
    assert "owned snapshot adapter" in result["error"]


def test_cancel_during_worker_preparation_does_not_return_candidate(
    minimal_optic, monkeypatch
):
    import optiland_gui.services.scientific_tasks as module
    from optiland_gui.services.job_records import CalculationCancelled, OpticSnapshot

    cancelled = threading.Event()
    monkeypatch.setattr(
        module, "initialize_loaded_optic", lambda optic: cancelled.set()
    )
    with pytest.raises(CalculationCancelled):
        module.execute_scientific(
            OpticSnapshot.capture(minimal_optic),
            {"code": "print('prepared')"},
            lambda *a, **kw: None,
            cancelled,
        )
