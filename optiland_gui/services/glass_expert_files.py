"""Owned asynchronous GlassExpert file actions on the existing shared queue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal, Slot

from .glass_expert_file_jobs import (
    MAX_REPLAY_BYTES,
    LoadedManifest,
    ManifestExport,
    OpenedCandidate,
    source_warnings,
)
from .glass_expert_records import GlassExpertSetup, PreparedGlassExpertSetup


@dataclass
class _Pending:
    operation_id: str
    kind: str
    handler: str
    request: object = None


class GlassExpertFiles(QObject):
    """File inputs/results have presentation ownership, never Apply authority."""

    loaded = Signal(object)
    exported = Signal(object)
    opened = Signal(object)
    statusChanged = Signal()

    def __init__(self, connector, parent=None):
        super().__init__(parent)
        self.connector = connector
        self.jobs = connector.calculation_jobs
        self.target = "glass-expert-files:" + uuid.uuid4().hex
        self._pending = None
        self._serial = 0
        self._closed = False
        self.state = "idle"
        self.message = ""
        self.jobs.finished.connect(self._finished)
        self.jobs.state_changed.connect(self._state_changed)
        self.jobs.stopped.connect(self.close)

    @property
    def busy(self):
        return self._pending is not None

    def _stamps(self):
        return tuple(
            stamp
            for source_id in self.connector.materials.source_ids
            if (source := self.connector.materials.source_result(source_id)) is not None
            for stamp in source.snapshot.stamps
        )

    def load(self, path):
        return self._submit(
            "load",
            "load_manifest",
            None,
            {
                "path": str(path),
                "run_id": uuid.uuid4().hex,
                "source_stamps": self._stamps(),
            },
        )

    def export(self, kind, prepared, result=None):
        if kind not in {"manifest", "script", "candidate"} or not isinstance(
            prepared, PreparedGlassExpertSetup
        ):
            raise ValueError("Choose a supported export from an exact prepared run.")
        return self._submit(
            kind,
            "prepare_export",
            prepared.setup.baseline,
            {"kind": kind, "prepared": prepared, "result": result},
        )

    def open_candidate(self, prepared, result):
        if not isinstance(prepared, PreparedGlassExpertSetup):
            raise ValueError("An exact prepared run is required.")
        return self._submit(
            "open_candidate",
            "open_candidate",
            prepared.setup.baseline,
            {"prepared": prepared, "result": result},
        )

    def _submit(self, kind, handler, snapshot, parameters):
        if self._closed:
            return None
        self._serial += 1
        serial = self._serial
        self._pending = None
        self.jobs.cancel_target(self.target)
        if self._closed or self._serial != serial:
            return None
        pending = _Pending(
            uuid.uuid4().hex,
            kind,
            "optiland_gui.services.glass_expert_file_jobs:" + handler,
        )
        self._pending = pending
        self.state, self.message = "queued", "GlassExpert file operation queued."
        try:
            request = self.jobs.submit(
                self.target,
                pending.handler,
                snapshot,
                {**parameters, "operation_id": pending.operation_id},
                cancel_on_document_change=False,
                progress_policy="detached",
                context=pending,
            )
        except Exception as error:
            if self._pending is pending:
                self._pending = None
                self.state, self.message = "failed", str(error)[:2048]
                self.statusChanged.emit()
            return None
        if self._pending is not pending:
            return None
        pending.request = request
        return request

    def _owns(self, request):
        pending = self._pending
        if (
            self._closed
            or pending is None
            or request.context is not pending
            or request.target != self.target
            or request.handler != pending.handler
        ):
            return False
        if pending.request is None:
            pending.request = request
        return pending.request is request

    @Slot(object, str)
    def _state_changed(self, request, state):
        if self._owns(request):
            self.state = state
            self.statusChanged.emit()

    @Slot(object)
    def _finished(self, result):
        if not self._owns(result.request):
            return
        pending, serial = self._pending, self._serial
        self._pending = None
        current = self.jobs.is_current(
            replace(result.request, document=self.connector.document_state.token)
        )
        data = result.data
        if result.status == "cancelled" or not current:
            self.state, self.message = (
                "cancelled",
                "GlassExpert file operation cancelled.",
            )
        elif result.status != "succeeded":
            self.state, self.message = "failed", str(result.error)[:2048]
        elif (
            not (
                (
                    type(data) is LoadedManifest
                    and pending.kind == "load"
                    and data.kind == "load"
                    and type(data.prepared) is PreparedGlassExpertSetup
                    and type(data.prepared.setup) is GlassExpertSetup
                    and data.prepared.setup.run_id
                    == result.request.parameters["run_id"]
                )
                or (
                    type(data) is ManifestExport
                    and pending.kind in {"manifest", "script", "candidate"}
                    and data.kind == pending.kind
                    and (
                        data.data is None
                        or (
                            type(data.data) is bytes
                            and len(data.data) <= MAX_REPLAY_BYTES
                        )
                    )
                    and (
                        (data.data is None and type(data.unavailable_reason) is str)
                        or (data.data is not None and data.unavailable_reason is None)
                    )
                )
                or (
                    type(data) is OpenedCandidate
                    and pending.kind == "open_candidate"
                    and data.kind == pending.kind
                    and type(data.prepared)
                    is type(result.request.parameters["prepared"].setup.baseline)
                    and data.run_id
                    == result.request.parameters["prepared"].setup.run_id
                    and data.input_digest
                    == result.request.parameters["prepared"].setup.input_digest
                    and data.prepared.backend
                    == result.request.parameters["prepared"].setup.baseline.backend
                )
            )
            or data.operation_id != pending.operation_id
            or type(getattr(data, "warnings", ())) is not tuple
            or any(
                type(w) is not str or len(w) > 2048
                for w in getattr(data, "warnings", ())
            )
        ):
            self.state, self.message = (
                "failed",
                "GlassExpert file result identity does not match.",
            )
        else:
            if pending.kind == "load":
                warnings = source_warnings(
                    data.prepared.setup.source_stamps, self._stamps()
                )
                data = replace(
                    data, warnings=tuple(dict.fromkeys((*data.warnings, *warnings)))
                )
            self.state, self.message = "ready", "GlassExpert file data is ready."
        self.statusChanged.emit()
        if (
            self._closed
            or self._serial != serial
            or self.state != "ready"
            or not self.jobs.is_current(
                replace(result.request, document=self.connector.document_state.token)
            )
        ):
            return
        signal = (
            self.loaded
            if pending.kind == "load"
            else self.opened
            if pending.kind == "open_candidate"
            else self.exported
        )
        signal.emit(data)

    def stop(self):
        if self._closed:
            return
        self._serial += 1
        self._pending = None
        self.state, self.message = "cancelled", "GlassExpert file operation cancelled."
        self.jobs.cancel_target(self.target)
        self.statusChanged.emit()

    @Slot()
    def close(self):
        if self._closed:
            return
        self._closed = True
        self._serial += 1
        self._pending = None
        self.state, self.message = "closed", "GlassExpert files are closed."
        self.jobs.cancel_target(self.target)
        self.statusChanged.emit()
