"""GUI source requests and bounded capture of live document material occurrences."""

from __future__ import annotations

import time
import uuid
import weakref
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from optiland.materials.registry import MaterialRegistry
from optiland.materials.sources import (
    DirectorySource,
    GlcSource,
    InstalledSource,
    OwnedEntry,
    OwnedSource,
    RegistrySource,
    capture_design,
)

if TYPE_CHECKING:
    from optiland_gui.optiland_connector import OptilandConnector


class MaterialBrowserSources(QObject):
    """Keep file discovery off Qt and capture live state in short event-loop batches.

    The material service owns accepted data. This adapter remembers reload
    requests and presentation labels; it never creates a second material cache.
    Any optical edit during a multi-batch design capture discards that capture.
    """

    error = Signal(str)

    def __init__(
        self, connector: OptilandConnector, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.connector = connector
        self.service = connector.materials
        self.requests: dict[
            str, InstalledSource | RegistrySource | DirectorySource | GlcSource
        ] = {}
        self.labels: dict[str, str] = {}
        self.design_labels: dict[str, str] = {}
        self._surface_ids: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._active = False
        self._started = False
        self._closed = False
        self._capture = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._capture_batch)
        connector.document_state.changed.connect(self._document_changed)
        connector.calculation_jobs.stopped.connect(self.close)

    def close(self) -> None:
        """Prevent queued capture callbacks from submitting after worker shutdown."""
        self.set_active(False)
        self._closed = True

    def set_active(self, active: bool) -> None:
        """Begin lazy source discovery when Materials is first made visible."""
        if self._closed:
            return
        self._active = active
        if active:
            if not self._started:
                self._started = True
                self.add_source(InstalledSource(), "Installed glass catalogs")
                self._load_registered()
            self.refresh_design()
        else:
            self._timer.stop()
            self._capture = None

    def _load_registered(self) -> None:
        entries = MaterialRegistry.registered_snapshot()
        if entries:
            self.add_source(RegistrySource("rii:user", entries), "Registered materials")
        elif "rii:user" in self.requests:
            self.remove_source("rii:user")

    def add_source(
        self,
        request: InstalledSource | RegistrySource | DirectorySource | GlcSource,
        label: str,
    ) -> None:
        """Load one caller-qualified request without changing the global registry."""
        if self._closed:
            return
        source_id = request.source_id
        self.requests[source_id] = request
        self.labels[source_id] = label
        self.service.load_source(request)

    def remove_source(self, source_id: str) -> None:
        """Forget one browser source, retaining its original files unchanged."""
        self.requests.pop(source_id, None)
        self.labels.pop(source_id, None)
        self.service.remove_source(source_id)

    def reload(self) -> None:
        """Refresh captured file requests and the current registered metadata."""
        if self._closed:
            return
        self._load_registered()
        for source_id, request in tuple(self.requests.items()):
            if source_id != "rii:user":
                self.service.load_source(request)
        self.refresh_design()

    def stop(self) -> None:
        """Cancel browser work without stopping another shared-worker consumer."""
        self._timer.stop()
        self._capture = None
        for source_id in tuple(self.requests):
            self.service.cancel_source(source_id)
        self.service.cancel_source(
            f"design:{self.connector.document_state.token.document_id}"
        )
        self.service.cancel_query()

    def _document_changed(self, token: object) -> None:
        if self._active:
            self.refresh_design()

    def refresh_design(self) -> None:
        """Capture retained surface identities; no spectral function runs on Qt."""
        if not self._active:
            return
        token = self.connector.document_state.token
        surfaces = tuple(self.connector.get_optic().surfaces)
        self._capture = (token, surfaces, 0, [], {}, {})
        self._timer.start(0)

    def _capture_batch(self) -> None:
        if self._capture is None or not self._active:
            return
        token, surfaces, position, entries, labels, payloads = self._capture
        if token != self.connector.document_state.token:
            self.refresh_design()
            return
        deadline = time.perf_counter() + 0.008
        try:
            processed = 0
            while position < len(surfaces) and processed < 20:
                surface = surfaces[position]
                record_id = self._surface_ids.setdefault(surface, uuid.uuid4().hex)
                material = surface.material_post
                cached = payloads.get(id(material))
                if cached is None:
                    cached = capture_design(
                        ((record_id, material),), document_id=token.document_id
                    ).entries[0]
                    payloads[id(material)] = cached
                entries.append(
                    OwnedEntry(
                        record_id, cached.display_name, cached.payload, cached.reason
                    )
                )
                labels[record_id] = f"Medium after surface {position}"
                position += 1
                processed += 1
                if time.perf_counter() >= deadline:
                    break
            if token != self.connector.document_state.token:
                self.refresh_design()
                return
            if position < len(surfaces):
                self._capture = (token, surfaces, position, entries, labels, payloads)
                self._timer.start(0)
                return
            source = OwnedSource(f"design:{token.document_id}", tuple(entries))
            self._capture = None
            self.design_labels = labels
            self.service.load_source(source)
        except (ValueError, TypeError, AttributeError) as exc:
            self._capture = None
            self.error.emit(f"Current-design materials could not be captured: {exc}")
