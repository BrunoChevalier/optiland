"""Qt lifecycle for a killable, bounded catalog-discovery helper process."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, Signal, Slot

from optiland_gui.services.catalog_discovery import SearchLimits

if TYPE_CHECKING:
    from collections.abc import Callable


class CatalogSearch(QObject):
    """Stream results with search IDs; revoke results before cancelling workers.

    There is at most one child process. Replacement searches wait asynchronously
    for the killed child to finish before starting. The GUI-owned deadline does
    not depend on any filesystem call returning. All ordinary cancellation is
    asynchronous; application shutdown only reaps the already-killed child.
    """

    batch_ready = Signal(int, list)
    completed = Signal(int, dict)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        limits: SearchLimits | None = None,
        clock: Callable[[], float] = time.monotonic,
        worker_command: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.limits = limits or SearchLimits()
        self._clock = clock
        self._command = worker_command or [
            sys.executable,
            "-u",
            str(Path(__file__).with_name("catalog_discovery.py")),
        ]
        self._process: QProcess | None = None
        self._pending: tuple[int, str] | None = None
        self._search_id = 0
        self._accepting = False
        self._closed = False
        self._buffer = b""
        self._found = 0
        self._deadline = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    @property
    def running(self) -> bool:
        return self._process is not None or self._pending is not None

    def start(self, directory: str) -> int:
        """Replace a search and return its ID before asynchronous results arrive."""
        if self._closed:
            raise RuntimeError("Catalog search has been closed")
        self.cancel()
        self._search_id += 1
        self._pending = (self._search_id, directory)
        self._accepting = True
        self._found = 0
        self._deadline = self._clock() + self.limits.timeout
        self._timer.start(max(1, int(self.limits.timeout * 1000)))
        if self._process is None:
            self._launch_pending()
        return self._search_id

    def _launch_pending(self) -> None:
        if self._pending is None or not self._accepting:
            return
        search_id, directory = self._pending
        self._pending = None
        self._buffer = b""
        process = self._process = QProcess(self)
        process.setProperty("search_id", search_id)
        process.setProgram(self._command[0])
        process.setArguments(
            [*self._command[1:], directory, json.dumps(asdict(self.limits))]
        )
        # QProcess creates a background child on Windows (no shell/console).
        # Receiver-bound slots disconnect with the owner. Lambdas capturing the
        # process/owner form cycles around deleted Qt wrappers on rapid disposal.
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)
        process.start()

    @Slot()
    def _read_stdout(self) -> None:
        process = self.sender()
        self._read(process, process.property("search_id"))

    @Slot(int, QProcess.ExitStatus)
    def _process_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        process = self.sender()
        self._finished(process, process.property("search_id"), code)

    @Slot(QProcess.ProcessError)
    def _process_error(self, error: QProcess.ProcessError) -> None:
        process = self.sender()
        self._error(process, process.property("search_id"), error)

    def _current(self, process: QProcess, search_id: int) -> bool:
        return (
            process is self._process
            and search_id == self._search_id
            and self._accepting
        )

    def _read(self, process: QProcess, search_id: int) -> None:
        if not self._current(process, search_id):
            return
        raw = bytes(process.readAllStandardOutput())
        if self._clock() >= self._deadline:
            self._timeout()
            return
        self._buffer += raw
        if len(self._buffer) > 1024 * 1024:
            self._stop_incomplete("Worker output exceeded its size limit")
            return
        while b"\n" in self._buffer and self._accepting:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("expected result object")
                if "paths" in message:
                    paths = message["paths"]
                    if not isinstance(paths, list) or not all(
                        isinstance(p, str) for p in paths
                    ):
                        raise ValueError("invalid result paths")
                    if (
                        len(paths) > self.limits.batch
                        or self._found + len(paths) > self.limits.catalogs
                    ):
                        raise ValueError("result count exceeded limit")
                    self._found += len(paths)
                    self.batch_ready.emit(search_id, paths)
                elif message.get("done") is True:
                    self._accepting = False
                    self._timer.stop()
                    self.completed.emit(search_id, message)
                    process.kill()
                else:
                    raise ValueError("unknown result message")
            except (ValueError, TypeError) as exc:
                self._stop_incomplete(f"Invalid search-worker result: {exc}")

    def _error(
        self, process: QProcess, search_id: int, error: QProcess.ProcessError
    ) -> None:
        if self._current(process, search_id):
            self._stop_incomplete("Catalog search worker could not run")
        if error == QProcess.ProcessError.FailedToStart:
            self._finished(process, search_id, -1)

    def _finished(self, process: QProcess, search_id: int, code: int) -> None:
        if process is not self._process:
            return
        self._read(process, search_id)
        if self._current(process, search_id):
            self._stop_incomplete(f"Catalog search stopped unexpectedly (exit {code})")
        if process is self._process:
            self._process = None
        process.deleteLater()
        self._launch_pending()
        if self._closed:
            self.deleteLater()

    def _stop_incomplete(self, reason: str) -> None:
        if not self._accepting:
            return
        search_id, found = self._search_id, self._found
        self.cancel()
        self.completed.emit(
            search_id,
            {
                "done": True,
                "complete": False,
                "reason": reason,
                "found": found,
                "skipped": 0,
            },
        )

    def _timeout(self) -> None:
        self._stop_incomplete("Time limit reached")

    def cancel(self) -> None:
        """Discard pending results and kill the helper without waiting in the GUI."""
        self._accepting = False
        self._pending = None
        self._buffer = b""
        self._timer.stop()
        if self._process is not None:
            self._process.kill()

    @Slot()
    def shutdown(self) -> None:
        """Revoke work and reap the killed process on application shutdown.

        The one-second cap concerns process reaping only; no live enumerator is
        awaited. Normal Cancel/close/replace paths use asynchronous cancel().
        """
        self._closed = True
        self.cancel()
        if self._process is not None:
            self._process.waitForFinished(1000)

    def dispose(self) -> None:
        """Release the owner after its killed process finishes asynchronously."""
        self._closed = True
        self.cancel()
        if self._process is None:
            self.deleteLater()
