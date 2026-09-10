"""Behavioral coverage of owned snapshots, Qt delivery and process lifecycle."""

from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QThread, QTimer, Slot

from optiland_gui.services.calculation_jobs import CalculationJobs, DocumentState
from optiland_gui.services.job_records import OpticSnapshot


def wait_for(qapp, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Calculation condition timed out")
        time.sleep(0.002)


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.results = []
        self.threads = []
        self.progress = []

    @Slot(object)
    def result(self, result):
        self.threads.append(QThread.currentThread())
        self.results.append(result)

    @Slot(object, dict)
    def update(self, request, message):
        self.threads.append(QThread.currentThread())
        self.progress.append(request.job_id)


@pytest.fixture
def jobs(qapp):
    state = DocumentState()
    service = CalculationJobs(state, cancel_grace_ms=40,
                              worker_command=[sys.executable, "-u", str(
                                  Path(__file__).with_name("calculation_worker_fixture.py"))])
    receiver = Receiver()
    service.finished.connect(receiver.result)
    service.progress.connect(receiver.update)
    yield state, service, receiver
    service.shutdown()
    wait_for(qapp, lambda: service._process is None)


def test_snapshot_is_owned_pure_and_numerically_equivalent(minimal_optic, monkeypatch):
    before = pickle.dumps(minimal_optic.to_dict(), protocol=5)
    monkeypatch.setattr(minimal_optic.updater, "update", lambda: pytest.fail("capture ran solves"))
    snapshot = OpticSnapshot.capture(minimal_optic)
    assert pickle.dumps(minimal_optic.to_dict(), protocol=5) == before
    copy = snapshot.restore()
    assert copy is not minimal_optic
    assert copy.surfaces[1] is not minimal_optic.surfaces[1]
    minimal_optic.trace(0, 0, 0.55, 5, "line_y")
    copy.trace(0, 0, 0.55, 5, "line_y")
    np.testing.assert_allclose(copy.surfaces.y, minimal_optic.surfaces.y)
    copy.surfaces[1].comment = "Worker changed"
    assert minimal_optic.surfaces[1].comment != "Worker changed"


def test_polarized_snapshot_preserves_aperture_and_incident_state(minimal_optic):
    from optiland.physical_apertures import RectangularAperture
    from optiland.rays import PolarizationState

    minimal_optic.polarization = PolarizationState(is_polarized=False)
    minimal_optic.surfaces[1].aperture = RectangularAperture(
        x_min=-4.0, x_max=4.0, y_min=-3.0, y_max=3.0)
    copy = OpticSnapshot.capture(minimal_optic).restore()
    assert not copy.polarization.is_polarized
    assert copy.surfaces[1].aperture.to_dict() == minimal_optic.surfaces[1].aperture.to_dict()


def test_worker_keeps_heartbeat_and_delivers_slots_on_gui(qapp, jobs):
    state, service, receiver = jobs
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    request = service.submit("2d", "unused", None, {"delay": 0.3})
    wait_for(qapp, lambda: len(receiver.results) == 1)
    timer.stop()
    assert len(ticks) >= 10
    assert receiver.results[0].request == request
    assert receiver.results[0].current
    assert receiver.results[0].data == 42
    assert all(thread == qapp.thread() for thread in receiver.threads)


def test_replacement_kills_stubborn_active_and_runs_latest(qapp, jobs):
    state, service, receiver = jobs
    first = service.submit("2d", "unused", None, {"delay": 10})
    wait_for(qapp, lambda: first.job_id in receiver.progress)
    second = service.submit("2d", "unused", None, {"value": "latest"})
    wait_for(qapp, lambda: len(receiver.results) == 2)
    assert [r.status for r in receiver.results] == ["cancelled", "succeeded"]
    assert receiver.results[1].request == second
    assert receiver.results[1].data == "latest"
    assert not receiver.results[0].current


def test_document_replacement_rejects_old_completion(qapp, jobs):
    state, service, receiver = jobs
    first = service.submit("2d", "unused", None, {"delay": 0.15})
    wait_for(qapp, lambda: first.job_id in receiver.progress)
    state.replace()
    wait_for(qapp, lambda: len(receiver.results) == 1)
    assert not receiver.results[0].current
    assert receiver.results[0].status == "cancelled"


def test_pending_replacement_has_exactly_one_terminal_each(qapp, jobs):
    state, service, receiver = jobs
    requests = [service.submit("2d", "unused", None, {"value": value})
                for value in range(5)]
    wait_for(qapp, lambda: len(receiver.results) == 5)
    assert sorted(r.request.job_id for r in receiver.results) == [r.job_id for r in requests]
    assert sum(r.status == "succeeded" for r in receiver.results) == 1
    assert receiver.results[-1].data == 4


def test_hidden_target_never_dispatches(qapp, jobs):
    state, service, receiver = jobs
    service.set_target_visible("3d", False)
    service.submit("3d", "unused", None, {})
    wait_for(qapp, lambda: receiver.results)
    assert receiver.results[0].status == "cancelled"
    assert not receiver.progress


def test_crash_has_terminal_error_and_service_recovers(qapp, jobs):
    state, service, receiver = jobs
    service.submit("2d", "unused", None, {"crash": True})
    wait_for(qapp, lambda: receiver.results)
    assert receiver.results[0].status == "failed"
    service.submit("2d", "unused", None, {})
    wait_for(qapp, lambda: len(receiver.results) == 2)
    assert receiver.results[1].status == "succeeded"


def test_shutdown_revokes_and_reaps_without_blocking(qapp, jobs):
    state, service, receiver = jobs
    request = service.submit("2d", "unused", None, {"delay": 10})
    wait_for(qapp, lambda: request.job_id in receiver.progress)
    start = time.monotonic()
    service.shutdown()
    assert time.monotonic() - start < 0.1
    wait_for(qapp, lambda: service._process is None)
    assert len(receiver.results) == 1
    assert not receiver.results[0].current
    with pytest.raises(RuntimeError, match="closed"):
        service.submit("2d", "unused", None, {})


def test_failed_start_finishes_queued_request(qapp):
    service = CalculationJobs(DocumentState(), worker_command=["nonexistent-worker"])
    receiver = Receiver()
    service.finished.connect(receiver.result)
    service.submit("2d", "unused", None, {})
    wait_for(qapp, lambda: receiver.results)
    assert receiver.results[0].status == "failed"
    assert not service.running
    service.shutdown()


def test_explicit_jobs_keep_fifo_and_enforce_queue_bound(qapp, jobs):
    state, service, receiver = jobs
    service.max_pending = 2
    service.submit("analysis-a", "unused", None, {"value": "a"}, replace=False)
    service.submit("analysis-b", "unused", None, {"value": "b"}, replace=False)
    with pytest.raises(RuntimeError, match="queue is full"):
        service.submit("analysis-c", "unused", None, {}, replace=False)
    wait_for(qapp, lambda: len(receiver.results) == 2)
    assert [r.data for r in receiver.results] == ["a", "b"]


def test_actual_worker_reports_handler_failure_and_closes(qapp):
    state = DocumentState()
    service = CalculationJobs(state)
    receiver = Receiver()
    service.finished.connect(receiver.result)
    service.submit("bad", "optiland_gui.services.job_records:does_not_exist", None, {})
    try:
        wait_for(qapp, lambda: receiver.results)
        assert receiver.results[0].status == "failed"
        assert "does_not_exist" in receiver.results[0].error
    finally:
        service.shutdown()
        wait_for(qapp, lambda: service._process is None)
