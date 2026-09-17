"""Live document material capture stays bounded, detached and generation-owned."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from optiland.materials import IdealMaterial
from optiland.materials.portability import restore_material
from optiland.materials.properties import MaterialSnapshot, query_materials
from optiland.materials.registry import MaterialRegistry, RegistryEntry
from optiland.materials.sources import InstalledSource, OwnedSource, RegistrySource
from optiland_gui.services.calculation_jobs import DocumentState
from optiland_gui.services.material_browser import MaterialBrowserSources


class MemoryMaterials(QObject):
    """Small transport boundary; panel tests still use real core records/queries."""

    source_changed = Signal(str)
    source_removed = Signal(str)
    source_failed = Signal(str, str)
    query_finished = Signal(object)
    query_failed = Signal(str)
    query_invalidated = Signal()
    state_changed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.loads, self.cancelled, self.queries = [], [], []
        self.accepted = {}
        self.errors = {}
        self.last_query_result = None
        self.query_cancelled = 0
        self.pending = None

    @property
    def source_ids(self):
        return tuple(self.accepted)

    @property
    def snapshot(self):
        return MaterialSnapshot(
            tuple(
                stamp
                for source in self.accepted.values()
                for stamp in source.snapshot.stamps
            ),
            tuple(
                record
                for source in self.accepted.values()
                for record in source.snapshot.materials
            ),
        )

    def load_source(self, request):
        self.loads.append(request)

    def source_result(self, source_id):
        return self.accepted.get(source_id)

    def source_error(self, source_id):
        return self.errors.get(source_id)

    def material(self, key):
        return next(
            (record for record in self.snapshot.materials if record.key == key), None
        )

    def cancel_source(self, source_id):
        self.cancelled.append(source_id)

    def cancel_query(self):
        self.query_cancelled += 1
        self.pending = None
        self.last_query_result = None
        self.query_invalidated.emit()

    def remove_source(self, source_id):
        self.accepted.pop(source_id, None)
        self.source_removed.emit(source_id)

    def query(self, query):
        self.queries.append(query)
        self.pending = query
        self.last_query_result = None

    def complete(self):
        result = query_materials(
            self.snapshot, self.pending, request_id=len(self.queries)
        )
        self.pending = None
        self.last_query_result = result
        self.query_finished.emit(result)
        return result


class Surface:
    def __init__(self, material):
        self.material_post = material


class Jobs(QObject):
    stopped = Signal()


def make_connector(surfaces=()):
    optic = SimpleNamespace(surfaces=list(surfaces))
    return SimpleNamespace(
        document_state=DocumentState(),
        materials=MemoryMaterials(),
        calculation_jobs=Jobs(),
        get_optic=lambda: optic,
    )


@pytest.fixture
def browser(qapp):
    connector = make_connector()
    controller = MaterialBrowserSources(connector)
    yield connector, controller
    controller.set_active(False)
    controller.deleteLater()


def test_construct_and_activate_capture_requests_without_discovery_io(
    browser, monkeypatch
):
    connector, controller = browser
    called = []
    monkeypatch.setattr(
        MaterialRegistry,
        "entries",
        lambda *args, **kwargs: pytest.fail("GUI discovery"),
    )
    monkeypatch.setattr(
        MaterialRegistry, "instance", lambda: pytest.fail("GUI initialization")
    )
    entry = RegistryEntry("rii:user", "row", "Lab", "user", "A", None, "unused.yml")
    monkeypatch.setattr(
        MaterialRegistry, "registered_snapshot", lambda: called.append(True) or (entry,)
    )
    assert not called and not connector.materials.loads
    controller.set_active(True)
    controller._timer.stop()
    assert called == [True]
    assert isinstance(connector.materials.loads[0], InstalledSource)
    assert connector.materials.loads[1] == RegistrySource("rii:user", (entry,))
    controller.set_active(False)
    controller.set_active(True)
    controller._timer.stop()
    assert called == [True] and len(connector.materials.loads) == 2


def test_capture_batches_share_payloads_but_preserve_occurrence_identity(
    browser, monkeypatch
):
    from optiland_gui.services import material_browser

    connector, controller = browser
    shared = IdealMaterial(1.6)
    connector.get_optic().surfaces = [Surface(shared) for _ in range(45)]
    calls = []
    capture = material_browser.capture_design
    monkeypatch.setattr(
        material_browser,
        "capture_design",
        lambda *args, **kwargs: calls.append(True) or capture(*args, **kwargs),
    )
    monkeypatch.setattr(material_browser.time, "perf_counter", lambda: 0)
    controller._active = True
    controller.refresh_design()
    controller._timer.stop()
    controller._capture_batch()
    assert not connector.materials.loads and controller._capture[2] == 20
    controller._timer.stop()
    controller._capture_batch()
    assert not connector.materials.loads and controller._capture[2] == 40
    controller._timer.stop()
    controller._capture_batch()
    source = connector.materials.loads[-1]
    assert isinstance(source, OwnedSource) and len(source.entries) == 45
    assert len({entry.record_id for entry in source.entries}) == 45
    assert len({entry.payload for entry in source.entries}) == 1
    assert calls == [True]
    assert restore_material(source.entries[0].payload).n(0.5).item() == 1.6
    assert len(controller.design_labels) == 45


def test_elapsed_time_budget_yields_before_maximum_batch(browser, monkeypatch):
    from optiland_gui.services import material_browser

    connector, controller = browser
    connector.get_optic().surfaces = [Surface(IdealMaterial(1.5)) for _ in range(3)]
    ticks = iter((0, 0.02))
    monkeypatch.setattr(material_browser.time, "perf_counter", lambda: next(ticks))
    controller._active = True
    controller.refresh_design()
    controller._timer.stop()
    controller._capture_batch()
    assert controller._capture[2] == 1 and not connector.materials.loads


def test_optical_edit_discards_partial_capture_and_uses_live_parameters(
    browser, monkeypatch
):
    from optiland_gui.services import material_browser

    connector, controller = browser
    material = IdealMaterial(1.5)
    connector.get_optic().surfaces = [Surface(material) for _ in range(25)]
    monkeypatch.setattr(material_browser.time, "perf_counter", lambda: 0)
    controller._active = True
    controller.refresh_design()
    controller._timer.stop()
    controller._capture_batch()
    assert controller._capture[2] == 20
    material.index[:] = 1.7
    connector.document_state.change()
    assert controller._capture[2] == 0
    while controller._capture is not None:
        controller._timer.stop()
        controller._capture_batch()
    assert len(connector.materials.loads) == 1
    source = connector.materials.loads[0]
    assert all(
        restore_material(entry.payload).n(0.5).item() == 1.7 for entry in source.entries
    )


def test_surface_reorder_retains_identity_and_document_replacement_changes_source(
    browser,
):
    connector, controller = browser
    first, second = Surface(IdealMaterial(1.5)), Surface(IdealMaterial(1.6))
    connector.get_optic().surfaces = [first, second]
    controller._active = True

    def capture():
        controller.refresh_design()
        controller._timer.stop()
        controller._capture_batch()
        return connector.materials.loads[-1]

    original = capture()
    connector.get_optic().surfaces.reverse()
    connector.document_state.record("structure")
    reordered = capture()
    assert [e.record_id for e in reordered.entries] == [
        e.record_id for e in reversed(original.entries)
    ]
    connector.document_state.replace()
    replaced = capture()
    assert replaced.source_id != original.source_id


def test_hiding_or_stopping_discards_batches_and_only_cancels_material_work(browser):
    connector, controller = browser
    controller._active = True
    controller.refresh_design()
    controller.set_active(False)
    controller._capture_batch()
    assert not connector.materials.loads and controller._capture is None
    controller.requests["rii:installed"] = InstalledSource()
    controller.stop()
    assert connector.materials.cancelled == [
        "rii:installed",
        f"design:{connector.document_state.token.document_id}",
    ]
    assert connector.materials.query_cancelled == 1


def test_capture_failure_is_visible_without_partial_publication(browser, monkeypatch):
    from optiland_gui.services import material_browser

    connector, controller = browser
    connector.get_optic().surfaces = [Surface(IdealMaterial(1.5))]
    errors = []
    controller.error.connect(errors.append)
    monkeypatch.setattr(
        material_browser,
        "capture_design",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("broken capture")),
    )
    controller._active = True
    controller.refresh_design()
    controller._timer.stop()
    controller._capture_batch()
    assert errors and "broken capture" in errors[0]
    assert controller._capture is None and not connector.materials.loads


def test_worker_shutdown_closes_capture_before_queued_timer_can_publish(browser):
    connector, controller = browser
    connector.get_optic().surfaces = [Surface(IdealMaterial(1.5))]
    controller._active = True
    controller.refresh_design()
    assert controller._timer.isActive()
    connector.calculation_jobs.stopped.emit()
    assert not controller._timer.isActive() and controller._capture is None
    controller._capture_batch()
    controller.set_active(True)
    controller.reload()
    controller.add_source(InstalledSource(), "Late callback")
    connector.document_state.change()
    assert not connector.materials.loads and not controller._active


def test_reload_keeps_explicit_source_identity_and_label_after_failure(
    browser, monkeypatch
):
    from optiland.materials.sources import DirectorySource

    connector, controller = browser
    monkeypatch.setattr(MaterialRegistry, "registered_snapshot", lambda: ())
    request = DirectorySource("local:stable-id", "unread-by-controller")
    controller.add_source(request, "Private lab catalog")
    connector.materials.source_failed.emit(request.source_id, "Unreadable catalog")
    controller.reload()
    assert connector.materials.loads == [request, request]
    assert controller.requests == {request.source_id: request}
    assert controller.labels == {request.source_id: "Private lab catalog"}
    controller.remove_source(request.source_id)
    assert not controller.requests and not controller.labels


def test_registered_source_is_refreshed_once_then_removed_when_registry_is_empty(
    browser, monkeypatch
):
    connector, controller = browser
    entry = RegistryEntry("rii:user", "row", "Lab", "user", "A", None, "unused.yml")
    monkeypatch.setattr(MaterialRegistry, "registered_snapshot", lambda: (entry,))
    controller.reload()
    assert connector.materials.loads == [RegistrySource("rii:user", (entry,))]
    assert "rii:user" in controller.requests
    monkeypatch.setattr(MaterialRegistry, "registered_snapshot", lambda: ())
    controller.reload()
    assert "rii:user" not in controller.requests and "rii:user" not in controller.labels
    assert len(connector.materials.loads) == 1


@pytest.mark.parametrize("during", [False, True])
def test_capture_token_checks_reject_changed_document_before_or_during_batch(
    browser, monkeypatch, during
):
    from optiland_gui.services import material_browser

    connector, controller = browser
    connector.get_optic().surfaces = [Surface(IdealMaterial(1.5))]
    controller._active = True
    controller.refresh_design()
    controller._timer.stop()
    old_token = controller._capture[0]

    def change_without_delivery():
        # A token can advance before the queued document notification reaches
        # this consumer. Both capture checkpoints must independently reject it.
        connector.document_state.blockSignals(True)
        connector.document_state.change()
        connector.document_state.blockSignals(False)

    if during:
        original = material_browser.capture_design

        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            change_without_delivery()
            return result

        monkeypatch.setattr(material_browser, "capture_design", capture)
    else:
        change_without_delivery()
    controller._capture_batch()
    assert not connector.materials.loads
    assert controller._capture[0] != old_token
    assert controller._capture[2] == 0
