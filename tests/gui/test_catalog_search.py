"""Real helper lifecycle and stalled-worker deadlines without filesystem stalls."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QEventLoop, QProcess, QTimer

from optiland_gui.services.catalog_discovery import SearchLimits
from optiland_gui.services.catalog_search import CatalogSearch


def wait_until(qapp, condition, timeout=3000):
    if condition():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: loop.quit() if condition() else None)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert condition(), "Timed out waiting for a bounded test worker"


def test_real_helper_batches_and_reaps(tmp_path, qapp):
    for i in range(7):
        (tmp_path / f"{i}.GLC").touch()
    search = CatalogSearch(limits=SearchLimits(batch=2, timeout=2))
    batches, outcomes = [], []
    search.batch_ready.connect(lambda i, paths: batches.append((i, paths)))
    search.completed.connect(lambda i, result: outcomes.append((i, result)))
    try:
        identifier = search.start(str(tmp_path))
        wait_until(qapp, lambda: bool(outcomes) and not search.running)
        assert outcomes[0][0] == identifier
        assert outcomes[0][1]["complete"]
        assert sum(len(paths) for _, paths in batches) == 7
        assert all(i == identifier and len(paths) <= 2 for i, paths in batches)
    finally:
        search.shutdown()


def test_stalled_worker_deadline_preserves_partial_results_and_reaps(qapp, tmp_path):
    path = str(tmp_path / "partial.glc")
    script = (
        f"import json,time; print(json.dumps({{'paths': [{path!r}]}}), flush=True); "
        "time.sleep(60)"
    )
    search = CatalogSearch(
        limits=SearchLimits(timeout=0.4),
        worker_command=[sys.executable, "-u", "-c", script],
    )
    batches, outcomes, heartbeats = [], [], []
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: heartbeats.append(1))
    search.batch_ready.connect(lambda _, paths: batches.extend(paths))
    search.completed.connect(lambda _, result: outcomes.append(result))
    try:
        heartbeat.start()
        search.start(str(tmp_path))
        wait_until(qapp, lambda: bool(outcomes) and not search.running)
        assert batches == [path]
        assert not outcomes[0]["complete"]
        assert outcomes[0]["reason"] == "Time limit reached"
        assert len(heartbeats) > 5  # GUI dispatch continued while child was blocked.
    finally:
        heartbeat.stop()
        search.shutdown()


def test_cancel_and_replacement_ignore_late_results(qapp, tmp_path):
    script = (
        "import json,time; time.sleep(.15); "
        "print(json.dumps({'paths':['late.glc']}),flush=True); time.sleep(60)"
    )
    search = CatalogSearch(
        limits=SearchLimits(timeout=2),
        worker_command=[sys.executable, "-u", "-c", script],
    )
    batches, outcomes = [], []
    search.batch_ready.connect(lambda i, paths: batches.append((i, paths)))
    search.completed.connect(lambda i, result: outcomes.append((i, result)))
    try:
        old = search.start(str(tmp_path))
        search.cancel()
        wait_until(qapp, lambda: not search.running)
        assert not batches and not outcomes
        old = search.start(str(tmp_path))
        new = search.start(str(tmp_path))
        wait_until(qapp, lambda: bool(batches))
        assert new != old and all(i == new for i, _ in batches)
        search.cancel()
        wait_until(qapp, lambda: not search.running)
        assert not outcomes
    finally:
        search.shutdown()


def test_gui_clock_rejects_results_even_before_timer_dispatch(qapp, tmp_path):
    clock = [0]
    search = CatalogSearch(clock=lambda: clock[0], limits=SearchLimits(timeout=2))
    batches, outcomes = [], []
    search.batch_ready.connect(lambda *args: batches.append(args))
    search.completed.connect(lambda _, result: outcomes.append(result))
    try:
        search.start(str(tmp_path))
        clock[0] = 3
        wait_until(qapp, lambda: bool(outcomes) and not search.running)
        assert not batches
        assert outcomes[0]["reason"] == "Time limit reached"
    finally:
        search.shutdown()


def test_shutdown_kills_stalled_child(qapp, tmp_path):
    search = CatalogSearch(
        worker_command=[sys.executable, "-c", "import time; time.sleep(60)"]
    )
    search.start(str(tmp_path))
    search.shutdown()
    assert not search.running
    with pytest.raises(RuntimeError, match="closed"):
        search.start(str(tmp_path))


@pytest.mark.parametrize(
    "script",
    [
        "print('not json', flush=True)",
        "print('[]', flush=True)",
        "print('{}', flush=True)",
        "print('{\"paths\":[12]}', flush=True)",
        "pass",
        "import json; print(json.dumps({'paths': ['a.glc']*33}), flush=True)",
        "print('x'*1100000, flush=True)",
    ],
)
def test_bad_or_incomplete_worker_output_is_visible(qapp, tmp_path, script):
    search = CatalogSearch(worker_command=[sys.executable, "-u", "-c", script])
    outcomes = []
    search.completed.connect(lambda _, result: outcomes.append(result))
    try:
        search.start(str(tmp_path))
        wait_until(qapp, lambda: bool(outcomes) and not search.running)
        assert len(outcomes) == 1 and not outcomes[0]["complete"]
    finally:
        search.shutdown()


def test_worker_start_failure_is_reported_and_reaped(qapp, tmp_path):
    search = CatalogSearch(worker_command=[str(tmp_path / "missing-worker")])
    outcomes = []
    search.completed.connect(lambda _, result: outcomes.append(result))
    try:
        search.start(str(tmp_path))
        wait_until(qapp, lambda: bool(outcomes) and not search.running)
        assert len(outcomes) == 1
        assert not outcomes[0]["complete"]
        assert "could not run" in outcomes[0]["reason"]
    finally:
        search.shutdown()


def test_repeated_replacement_and_disposal_reap_each_worker(qapp, tmp_path):
    from shiboken6 import isValid

    for _ in range(8):
        search = CatalogSearch(
            worker_command=[sys.executable, "-c", "import time; time.sleep(60)"]
        )
        for _ in range(4):
            search.start(str(tmp_path))
        search.dispose()
        wait_until(qapp, lambda owner=search: not isValid(owner))


def test_late_finish_and_timeout_do_not_revive_cancelled_search(qapp):
    search = CatalogSearch()
    old_process = QProcess(search)
    outcomes = []
    search.completed.connect(lambda *args: outcomes.append(args))
    search.cancel()
    search._finished(old_process, 0, 1)
    search._timeout()
    assert not outcomes and not search.running
    search.shutdown()


def test_idle_disposal_releases_owner_without_launching_worker(qapp):
    from shiboken6 import isValid

    search = CatalogSearch()
    assert not search.running
    search.dispose()
    wait_until(qapp, lambda: not isValid(search))
