"""Read-only map, spectral and nearby jobs on the shared material executor."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from optiland.materials.comparison import (
    MapViewResult,
    NearbyResult,
    SpectralRequest,
    SpectralResult,
)
from optiland.materials.properties import MaterialSnapshot

from .job_records import BackendConfig

if TYPE_CHECKING:
    from optiland.materials.comparison import MapView
    from optiland.materials.properties import MaterialKey, SourceStamp
    from optiland_gui.optiland_connector import OptilandConnector

    from .job_records import DocumentToken, JobRequest, JobResult

ComparisonResult = MapViewResult | SpectralResult | NearbyResult


@dataclass
class _Computation:
    kind: str
    serial: int
    backend: BackendConfig
    stamps: tuple[SourceStamp, ...]
    document: DocumentToken | None
    spec: object
    sources: frozenset[str]
    request: JobRequest | None = None


class MaterialComparisonService(QObject):
    """Own immutable computed results, independently of lens mutation authority.

    Map and nearby jobs borrow cached source-summary bytes. Spectral jobs receive
    at most four portable material definitions. Each kind supersedes only its own
    target and validates source, backend and relevant document revisions before
    presentation. There is no second queue or optical work on Qt.
    """

    finished = Signal(str, object)
    failed = Signal(str, str)
    invalidated = Signal(str)
    state_changed = Signal(str, str)
    KINDS = ("view", "spectral", "nearby")

    def __init__(
        self, connector: OptilandConnector, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.materials = connector.materials
        self.jobs = connector.calculation_jobs
        self.document = connector.document_state
        self._prefix = f"material-comparison:{uuid.uuid4().hex}"
        self._pending: dict[str, _Computation] = {}
        self._accepted: dict[str, tuple[_Computation, ComparisonResult]] = {}
        self._serials = dict.fromkeys(self.KINDS, 0)
        self._closed = False
        self.jobs.finished.connect(self._finished)
        self.jobs.stopped.connect(self.close)
        self.materials.source_changed.connect(self._source_changed)
        self.materials.source_removed.connect(self._source_changed)
        self.document.changed.connect(self._document_changed)

    def result(self, kind: str) -> ComparisonResult | None:
        """Return a still-current result, never a stale result awaiting repaint."""
        pair = self._accepted.get(kind)
        return pair[1] if pair is not None and self._current(pair[0]) else None

    def is_pending(self, kind: str) -> bool:
        """Report current owned work, so unrelated edits do not resubmit it."""
        pending = self._pending.get(kind)
        return pending is not None and self._current(pending)

    def request_view(self, view: MapView) -> JobRequest | None:
        if self._closed:
            return None
        stamps, blobs = self.materials.query_transport()
        return self._submit(
            "view",
            view,
            stamps,
            frozenset(s.source_id for s in stamps),
            "compute_map_view",
            {"material_blobs": blobs, "stamps": stamps, "view": view},
        )

    def request_comparison(self, request: SpectralRequest) -> JobRequest | None:
        """Capture only requested exact records, retaining unavailable keys."""
        if self._closed:
            return None
        if not isinstance(request, SpectralRequest):
            raise ValueError("A spectral request is required")
        records = tuple(
            record
            for key in request.keys
            if (record := self.materials.material(key)) is not None
        )
        sources = frozenset(key.source_id for key in request.keys)
        stamps = tuple(
            source.snapshot.stamps[0]
            for source_id in sorted(sources)
            if (source := self.materials.source_result(source_id)) is not None
        )
        return self._submit(
            "spectral",
            request,
            stamps,
            sources,
            "compute_comparison",
            {
                "material_snapshot": MaterialSnapshot(stamps, records),
                "request": request,
            },
        )

    def request_nearby(
        self, key: MaterialKey, nd_scale: float, vd_scale: float
    ) -> JobRequest | None:
        if self._closed:
            return None
        stamps, blobs = self.materials.query_transport()
        spec = (key, nd_scale, vd_scale)
        return self._submit(
            "nearby",
            spec,
            stamps,
            frozenset(s.source_id for s in stamps),
            "compute_nearby",
            {
                "material_blobs": blobs,
                "stamps": stamps,
                "target_key": key,
                "nd_scale": nd_scale,
                "vd_scale": vd_scale,
                "limit": 10,
            },
        )

    def cancel(self, kind: str) -> None:
        """Revoke one computation and its previous presentation authority."""
        if kind not in self.KINDS:
            raise ValueError("Unknown material computation")
        self._serials[kind] += 1
        had = self._pending.pop(kind, None) is not None
        had = self._accepted.pop(kind, None) is not None or had
        self.jobs.cancel_target(f"{self._prefix}:{kind}")
        if had:
            self.invalidated.emit(kind)

    def _submit(
        self,
        kind: str,
        spec: object,
        stamps: tuple[SourceStamp, ...],
        sources: frozenset[str],
        handler: str,
        parameters: dict[str, Any],
    ) -> JobRequest | None:
        if self._closed:
            return None
        serial = self._serials[kind] + 1
        self.cancel(kind)
        if self._closed or self._serials[kind] != serial:
            return None
        backend = BackendConfig.capture()
        document = (
            self.document.token
            if any(source_id.startswith("design:") for source_id in sources)
            else None
        )
        pending = _Computation(kind, serial, backend, stamps, document, spec, sources)
        self._pending[kind] = pending
        try:
            request = self.jobs.submit(
                f"{self._prefix}:{kind}",
                f"optiland_gui.services.material_comparison_jobs:{handler}",
                None,
                {**parameters, "backend": backend},
                cancel_on_document_change=document is not None,
                context=pending,
            )
        except Exception as error:
            if self._pending.get(kind) is pending:
                self._pending.pop(kind)
                self.failed.emit(kind, str(error)[:2000])
            return None
        pending.request = request
        if self._pending.get(kind) is not pending:
            return None
        if not self._current(pending):
            self.cancel(kind)
            return None
        self.state_changed.emit(kind, "Calculating…")
        return request if self._pending.get(kind) is pending else None

    def _current(self, pending: _Computation) -> bool:
        if (
            self._closed
            or self._serials[pending.kind] != pending.serial
            or BackendConfig.capture() != pending.backend
            or (
                pending.document is not None and pending.document != self.document.token
            )
        ):
            return False
        request = pending.request
        if request is not None:
            # Catalog jobs survive unrelated optical edits, but never revoked
            # queue generations, hidden targets or executor shutdown.
            checked_request = (
                request
                if pending.document is not None
                else replace(request, document=self.document.token)
            )
            if not self.jobs.is_current(checked_request):
                return False
        source_ids = (
            sorted(pending.sources)
            if pending.kind == "spectral"
            else self.materials.source_ids
        )
        # Compare the complete relevant set, including a previously missing
        # source that an invalidated/Ready callback may have just restored.
        return pending.stamps == tuple(
            source.snapshot.stamps[0]
            for source_id in source_ids
            if (source := self.materials.source_result(source_id)) is not None
        )

    @Slot(object)
    def _finished(self, result: JobResult) -> None:
        pending = result.request.context
        if (
            not isinstance(pending, _Computation)
            or self._pending.get(pending.kind) is not pending
            or result.request.target != f"{self._prefix}:{pending.kind}"
            or (pending.request is not None and result.request is not pending.request)
        ):
            return
        kind = pending.kind
        # A queued callback can finish/cancel synchronously before submit returns.
        pending.request = result.request
        self._pending.pop(kind)
        if not self._current(pending) or result.status == "cancelled":
            self.invalidated.emit(kind)
            return
        if result.status != "succeeded":
            self.failed.emit(kind, result.error[-2000:])
            return
        data = result.data
        expected = {
            "view": MapViewResult,
            "spectral": SpectralResult,
            "nearby": NearbyResult,
        }[kind]
        valid = isinstance(data, expected) and data.stamps == pending.stamps
        if valid and kind == "view":
            valid = data.view == pending.spec
        elif valid and kind == "spectral":
            valid = data.request == pending.spec
        elif valid:
            valid = (data.target_key, data.nd_scale, data.vd_scale) == pending.spec
        if not valid:
            self.failed.emit(
                kind, "The calculation returned different material inputs."
            )
            return
        self._accepted[kind] = (pending, data)
        self.state_changed.emit(kind, "Ready")
        if self.result(kind) is data:
            self.finished.emit(kind, data)

    @Slot(str)
    def _source_changed(self, source_id: str) -> None:
        self._invalidate_stale()

    @Slot(object)
    def _document_changed(self, token: DocumentToken) -> None:
        self._invalidate_stale()

    def _owner(self, kind: str) -> _Computation | None:
        pair = self._accepted.get(kind)
        return self._pending.get(kind) or (pair[0] if pair else None)

    def _invalidate_stale(self) -> None:
        # Cancellation emits public signals. A listener may already replace a
        # sibling computation with fresh inputs before this loop reaches it.
        owners = tuple((kind, self._owner(kind)) for kind in self.KINDS)
        for kind, owner in owners:
            if (
                owner is not None
                and self._owner(kind) is owner
                and not self._current(owner)
            ):
                self.cancel(kind)

    @Slot()
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for kind in self.KINDS:
            self.cancel(kind)
