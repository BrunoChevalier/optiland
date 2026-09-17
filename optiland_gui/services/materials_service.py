"""GUI-owned material snapshots, source generations and asynchronous queries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal, Slot

from optiland.materials.properties import (
    MaterialKey,
    MaterialQuery,
    MaterialQueryResult,
    MaterialSnapshot,
    MaterialSummary,
)
from optiland.materials.sources import SourceResult

from .job_records import BackendConfig, DocumentToken, JobRequest


@dataclass
class _Load:
    request: JobRequest | None
    backend: BackendConfig
    document: DocumentToken | None


@dataclass
class _Query:
    request: JobRequest | None
    backend: BackendConfig
    document: DocumentToken | None
    snapshot: MaterialSnapshot
    serial: int
    spec: MaterialQuery


class MaterialsService(QObject):
    """Own a bounded material cache without running catalog evaluation on Qt.

    Every load replaces one source atomically. An unsuccessful reload leaves its
    previous snapshot available and emits ``source_failed``. A successful source
    publication or removal revokes queries that could include that source.
    Optical edits revoke
    current-design snapshots and mixed queries, while independent catalog jobs
    continue. The cache retains portable optical data; query jobs receive only
    lightweight property summaries.

    This service borrows the connector's ``CalculationJobs``. Closing it cancels
    only its own targets and never shuts down the shared worker.
    """

    source_changed = Signal(str)
    source_removed = Signal(str)
    source_failed = Signal(str, str)
    query_finished = Signal(object)
    query_failed = Signal(str)
    query_invalidated = Signal()
    state_changed = Signal(str, str)

    def __init__(
        self,
        jobs,
        document,
        parent=None,
        *,
        max_sources=32,
        max_records=100000,
        max_payload_bytes=128 * 1024 * 1024,
    ):
        super().__init__(parent)
        if max_sources < 1 or max_records < 1 or max_payload_bytes < 1:
            raise ValueError("Material cache limits must be positive")
        self.jobs = jobs
        self.document = document
        self.max_sources = max_sources
        self.max_records = max_records
        self.max_payload_bytes = max_payload_bytes
        self._prefix = f"materials:{uuid.uuid4().hex}"
        self._query_target = f"{self._prefix}:query"
        self._targets = {self._query_target}
        self._sources: dict[str, SourceResult] = {}
        self._loads: dict[str, _Load] = {}
        self._errors: dict[str, str] = {}
        self._query: _Query | None = None
        self._query_serial = 0
        self._backend = BackendConfig.capture()
        self._last_query_result = None
        self._last_query_spec: MaterialQuery | None = None
        self._closed = False
        jobs.finished.connect(self._on_finished)
        document.changed.connect(self._document_changed)
        # Retain only the queue and target set in the destruction hook.
        targets = self._targets
        self.destroyed.connect(lambda: [jobs.cancel_target(t) for t in targets])

    @property
    def source_ids(self) -> tuple[str, ...]:
        """Return source IDs in their stable publication order."""
        return tuple(self._sources)

    @property
    def snapshot(self) -> MaterialSnapshot:
        """Expose immutable published records with their owned optical data."""
        return MaterialSnapshot(
            tuple(
                s for source in self._sources.values() for s in source.snapshot.stamps
            ),
            tuple(
                m
                for source in self._sources.values()
                for m in source.snapshot.materials
            ),
        )

    @property
    def last_query_result(self) -> MaterialQueryResult | None:
        """Return the most recent still-valid query result."""
        return self._last_query_result

    @property
    def busy(self) -> bool:
        """Report whether this service owns any pending material work."""
        return bool(self._loads) or self._query is not None

    def source_result(self, source_id: str) -> SourceResult | None:
        """Read a published source and its per-record diagnostics."""
        return self._sources.get(source_id)

    def source_error(self, source_id: str) -> str | None:
        """Read the latest bounded source failure, if any."""
        return self._errors.get(source_id)

    def material(self, key: MaterialKey) -> MaterialSummary | None:
        """Resolve an exact identity in the retained portable material cache."""
        source = self._sources.get(key.source_id)
        if source is not None:
            return next((m for m in source.snapshot.materials if m.key == key), None)
        return None

    def _target(self, source_id):
        return f"{self._prefix}:source:{source_id}"

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("Materials service is closed")

    def load_source(self, source) -> JobRequest | None:
        """Queue source IO/evaluation; only native owned capture belongs on Qt."""
        self._ensure_open()
        source_id = source.source_id
        document = None
        if source_id.startswith("design:"):
            document = self.document.token
            if source_id != f"design:{document.document_id}":
                self._source_failure(
                    source_id, "Current-design source belongs to another document"
                )
                return None
        if len(set(self._sources) | set(self._loads) | {source_id}) > self.max_sources:
            self._source_failure(
                source_id,
                "Material source limit exceeded; remove a source before loading more",
            )
            return None
        backend = BackendConfig.capture()
        pending = _Load(None, backend, document)
        self._loads[source_id] = pending
        self._targets.add(self._target(source_id))
        self._errors.pop(source_id, None)
        try:
            request = self.jobs.submit(
                self._target(source_id),
                "optiland_gui.services.material_jobs:load_material_source",
                None,
                {"source": source, "backend": backend},
                cancel_on_document_change=document is not None,
                context=pending,
            )
        except Exception as error:
            if self._loads.get(source_id) is pending:
                self._loads.pop(source_id)
                self._targets.discard(self._target(source_id))
                self._source_failure(source_id, str(error))
            return None
        pending.request = request
        if self._loads.get(source_id) is not pending:
            return None
        if not self._current(pending):
            self.cancel_source(source_id)
            return None
        self.state_changed.emit(source_id, "loading")
        return request

    def cancel_source(self, source_id: str) -> None:
        """Revoke an in-flight reload while keeping any published source."""
        if self._loads.pop(source_id, None) is not None:
            self._targets.discard(self._target(source_id))
            self.jobs.cancel_target(self._target(source_id))
            if source_id not in self._loads:
                self.state_changed.emit(source_id, "cancelled")

    def remove_source(self, source_id: str) -> None:
        """Remove a source and revoke its in-flight and cached query ownership."""
        self.cancel_source(source_id)
        self._errors.pop(source_id, None)
        if self._sources.pop(source_id, None) is not None:
            self._invalidate_source_query(source_id)
            self.source_removed.emit(source_id)

    @staticmethod
    def _includes_source(query: MaterialQuery, source_id: str) -> bool:
        return (not query.source_ids or source_id in query.source_ids) and (
            query.catalog_keys is None
            or any(key[0] == source_id for key in query.catalog_keys)
        )

    def _invalidate_source_query(self, source_id: str) -> None:
        spec = self._query.spec if self._query is not None else self._last_query_spec
        if spec is not None and self._includes_source(spec, source_id):
            self._invalidate_query()

    def query(self, query: MaterialQuery) -> JobRequest | None:
        """Submit the latest query over only its selected, published sources."""
        self._ensure_open()
        self._query_serial += 1
        serial = self._query_serial
        self._invalidate_query()
        if self._closed or self._query_serial != serial:
            return None
        sources = tuple(
            source
            for source_id, source in self._sources.items()
            if self._includes_source(query, source_id)
        )
        snapshot = MaterialSnapshot(
            tuple(s for source in sources for s in source.snapshot.stamps),
            tuple(
                replace(m, owned_data=None)
                for source in sources
                for m in source.snapshot.materials
            ),
        )
        document = (
            self.document.token
            if any(s.source_id.startswith("design:") for s in snapshot.stamps)
            else None
        )
        backend = BackendConfig.capture()
        pending = _Query(None, backend, document, snapshot, serial, query)
        self._query = pending
        try:
            request = self.jobs.submit(
                self._query_target,
                "optiland_gui.services.material_jobs:filter_materials",
                None,
                {
                    "materials": snapshot,
                    "query": query,
                    "request_id": serial,
                    "backend": backend,
                },
                cancel_on_document_change=document is not None,
                context=pending,
            )
        except Exception as error:
            if self._query is pending:
                self._query = None
                self.query_failed.emit(str(error)[:2000])
            return None
        pending.request = request
        if self._query is not pending:
            return None
        if not self._current(pending):
            self._invalidate_query()
            return None
        self.state_changed.emit("query", "loading")
        return request

    def cancel_query(self) -> None:
        """Revoke the active query and any retained result."""
        self._query_serial += 1
        self._invalidate_query()

    def _invalidate_query(self):
        had_result = self._query is not None or self._last_query_result is not None
        self._query = None
        self._last_query_result = None
        self._last_query_spec = None
        self.jobs.cancel_target(self._query_target)
        if had_result:
            self.query_invalidated.emit()

    @Slot(object)
    def _document_changed(self, token):
        backend = BackendConfig.capture()
        if backend != self._backend:
            self._backend = backend
            self._invalidate_query()
        for source_id in tuple(set(self._sources) | set(self._loads)):
            if source_id.startswith("design:"):
                self.cancel_source(source_id)
                self._errors.pop(source_id, None)
                if self._sources.pop(source_id, None) is not None:
                    self._invalidate_source_query(source_id)
                    self.source_removed.emit(source_id)

    def _source_failure(self, source_id, error):
        error = error[:2000]
        if source_id not in self._errors and len(self._errors) >= self.max_sources:
            self._errors.pop(next(iter(self._errors)))
        self._errors[source_id] = error
        self.state_changed.emit(source_id, "failed")
        self.source_failed.emit(source_id, error)

    @Slot(object)
    def _on_finished(self, result):
        if self._closed:
            return
        for source_id, pending in tuple(self._loads.items()):
            if result.request.context is pending:
                self._loads.pop(source_id)
                self._targets.discard(result.request.target)
                self._finish_source(source_id, pending, result)
                return
        if self._query is not None and result.request.context is self._query:
            pending, self._query = self._query, None
            self._finish_query(pending, result)

    def _current(self, pending):
        # CalculationJobs.is_current includes document revision unconditionally;
        # catalog jobs deliberately use their own ownership and source stamps.
        return pending.backend == BackendConfig.capture() and (
            pending.document is None or pending.document == self.document.token
        )

    def _finish_source(self, source_id, pending, result):
        if result.status == "cancelled" or not self._current(pending):
            self.state_changed.emit(source_id, "cancelled")
            return
        if result.status != "succeeded":
            self._source_failure(source_id, result.error)
            return
        data = result.data
        if (
            not isinstance(data, SourceResult)
            or len(data.snapshot.stamps) != 1
            or data.snapshot.stamps[0].source_id != source_id
        ):
            self._source_failure(source_id, "Invalid material source response")
            return
        if data.failed:
            self._source_failure(
                source_id,
                "; ".join(d.message for d in data.diagnostics)
                or "Source loading failed",
            )
            return
        others = [s for key, s in self._sources.items() if key != source_id]
        payload_size = sum(
            len(material.owned_data or b"")
            for source in (*others, data)
            for material in source.snapshot.materials
        )
        if (
            len(others) + 1 > self.max_sources
            or payload_size > self.max_payload_bytes
            or (
                sum(len(s.snapshot.materials) for s in others)
                + len(data.snapshot.materials)
                > self.max_records
            )
        ):
            self._source_failure(
                source_id,
                "Material cache capacity exceeded; remove a source before loading more",
            )
            return
        self._sources[source_id] = data
        self._errors.pop(source_id, None)
        self._invalidate_source_query(source_id)
        if self._sources.get(source_id) is data and not self._closed:
            self.state_changed.emit(source_id, "ready")
            if self._sources.get(source_id) is data and not self._closed:
                self.source_changed.emit(source_id)

    def _finish_query(self, pending, result):
        if result.status == "cancelled" or not self._current(pending):
            self.query_invalidated.emit()
            return
        if result.status != "succeeded":
            self.query_failed.emit(result.error[:2000])
            return
        data = result.data
        stamps = tuple(
            stamp
            for stamp in pending.snapshot.stamps
            if stamp.source_id in self._sources
            and self._sources[stamp.source_id].snapshot.stamps == (stamp,)
        )
        if (
            not isinstance(data, MaterialQueryResult)
            or data.request_id != pending.serial
            or data.stamps != pending.snapshot.stamps
            or stamps != pending.snapshot.stamps
        ):
            self.query_invalidated.emit()
            return
        self._last_query_result = data
        self._last_query_spec = pending.spec
        self.state_changed.emit("query", "ready")
        if self._last_query_result is data and self._current(pending):
            self.query_finished.emit(data)

    def close(self) -> None:
        """Revoke all service targets without affecting other queue consumers."""
        if self._closed:
            return
        self._closed = True
        self._loads.clear()
        self._invalidate_query()
        for target in self._targets:
            self.jobs.cancel_target(target)
        self._sources.clear()
        self._errors.clear()
