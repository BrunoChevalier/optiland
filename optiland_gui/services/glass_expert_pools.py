"""GUI lifetime for explicit pool creation, using the existing job queue."""

from __future__ import annotations

import uuid

from PySide6.QtCore import QObject, Signal, Slot

from .glass_expert_pool_jobs import PreparedPool
from .job_records import BackendConfig


class GlassExpertPools(QObject):
    """Publish current pool results without mutating a design or active run."""

    ready = Signal(object)
    changed = Signal()

    def __init__(self, connector, parent=None):
        super().__init__(parent)
        self.connector = connector
        self.jobs = connector.calculation_jobs
        self.target = "glass-expert-pool:" + uuid.uuid4().hex
        self._pending = None
        self._closed = False
        self.message = ""
        self.jobs.finished.connect(self._finished)
        self.jobs.progress.connect(self._progress)
        self.jobs.stopped.connect(self.close)
        jobs, target = self.jobs, self.target
        self.destroyed.connect(lambda: jobs.cancel_target(target))

    @property
    def busy(self):
        return self._pending is not None

    def build(self, entries, wavelengths):
        if self._closed:
            raise ValueError("Pool preparation is closed.")
        context = object()
        self._pending = context
        # Reserve the new owner before cancellation can synchronously notify a
        # closing panel or a newer explicit pool action.
        self.jobs.cancel_target(self.target)
        if self._closed or self._pending is not context:
            return None
        self.message = "Validating the selected exact materials…"
        try:
            request = self.jobs.submit(
                self.target,
                "optiland_gui.services.glass_expert_pool_jobs:prepare_pool",
                None,
                {
                    "entries": tuple(entries),
                    "wavelengths": tuple(wavelengths),
                    "backend": BackendConfig.capture(),
                },
                context=context,
            )
        except Exception as error:
            if self._pending is context:
                self._pending = None
                self.message = str(error)[:2048]
                self.changed.emit()
            return None
        self.changed.emit()
        return request if self._pending is context else None

    @Slot(object, dict)
    def _progress(self, request, message):
        if (
            self._pending is not None
            and request.context is self._pending
            and self.jobs.is_current(request)
        ):
            self.message = (
                f"Validating exact materials: {message.get('completed', 0)} / "
                f"{message.get('total', '?')}"
            )
            self.changed.emit()

    @Slot(object)
    def _finished(self, result):
        context = self._pending
        if self._closed or context is None or result.request.context is not context:
            return
        data = result.data
        accepted = False
        if result.status != "succeeded":
            message = (
                "Pool preparation cancelled."
                if result.status == "cancelled"
                else str(result.error)[-2048:]
            )
        elif not self.jobs.is_current(result.request) or (
            result.request.parameters["backend"] != BackendConfig.capture()
        ):
            message = "The design or backend changed. Create the pool again."
        elif not isinstance(data, PreparedPool):
            message = "The pool worker returned an invalid result."
        elif any(
            self.connector.materials.source_result(stamp.source_id) is None
            or self.connector.materials.source_result(stamp.source_id).snapshot.stamps
            != (stamp,)
            for stamp in data.stamps
        ):
            message = "A selected source changed. Create the pool again."
        else:
            message = (
                f"{len(data.candidates)} eligible; {len(data.exclusions)} excluded."
            )
            accepted = True
        if self._closed or self._pending is not context:
            return
        self._pending = None
        self.message = message
        if accepted:
            self.ready.emit(data)
        self.changed.emit()

    @Slot()
    def stop(self):
        self._pending = None
        self.jobs.cancel_target(self.target)
        self.changed.emit()

    @Slot()
    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop()
