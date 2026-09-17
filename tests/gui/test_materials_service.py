"""Material publication ownership across source, query and document lifecycles."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QThread

from optiland.materials.portability import restore_material
from optiland.materials.properties import (
    MaterialKey,
    MaterialQuery,
    MaterialSnapshot,
    SourceStamp,
    query_materials,
)
from optiland.materials.registry import RegistryEntry
from optiland.materials.sources import (
    GlcSource,
    RegistrySource,
    SourceDiagnostic,
    SourceResult,
    load_source,
)
from optiland_gui.services.calculation_jobs import CalculationJobs, DocumentState
from optiland_gui.services.job_records import BackendConfig, JobResult
from optiland_gui.services.materials_service import MaterialsService
from tests.gui.test_calculation_jobs import wait_for
from tests.gui.test_material_jobs import owned_source


@pytest.fixture
def materials(qapp):
    state = DocumentState()
    jobs = CalculationJobs(state, cancel_grace_ms=50)
    service = MaterialsService(jobs, state)
    yield state, jobs, service
    service.close()
    jobs.shutdown()
    wait_for(qapp, lambda: jobs._process is None)


def publish(jobs, service, source):
    request = service.load_source(source)
    result = load_source(source)
    jobs.finished.emit(JobResult(request, "succeeded", result, current=True))
    return request, result


def finish_query(jobs, service, *, current=True):
    pending = service._query
    result = query_materials(
        pending.snapshot, pending.request.parameters["query"], request_id=pending.serial
    )
    jobs.finished.emit(JobResult(pending.request, "succeeded", result, current=current))
    return result


def test_real_worker_atomic_load_and_lightweight_query_survive_unrelated_edits(
    materials, qapp
):
    state, jobs, service = materials
    delivered_threads = []
    service.source_changed.connect(
        lambda _: delivered_threads.append(QThread.currentThread())
    )
    request = service.load_source(owned_source())
    state.change()
    state.record("metadata")
    state.record("presentation")
    wait_for(qapp, lambda: service.source_ids == ("catalog:a",), timeout=30)
    assert not jobs.is_current(request)  # document revision is intentionally irrelevant
    assert delivered_threads == [qapp.thread()]
    cached = service.snapshot.materials[0]
    assert cached.owned_data
    assert service.material(cached.key) == cached
    assert service.material(MaterialKey("missing", "", "")) is None
    assert service.material(replace(cached.key, record_id="missing")) is None
    query = service.query(MaterialQuery(name="Flint"))
    assert all(m.owned_data is None for m in query.parameters["materials"].materials)
    state.change()
    wait_for(qapp, lambda: service.last_query_result is not None, timeout=30)
    assert len(service.last_query_result.matches) == 1
    assert service.last_query_result.matches[0].key.record_id == "1"
    assert service.material(cached.key).owned_data == cached.owned_data


def test_real_worker_failed_reload_keeps_healthy_sources_and_remove_revokes_pending(
    materials, qapp, tmp_path
):
    state, jobs, service = materials
    service.load_source(owned_source())
    service.load_source(owned_source("catalog:b"))
    wait_for(qapp, lambda: len(service.source_ids) == 2, timeout=30)
    original = service.source_result("catalog:a")
    service.load_source(GlcSource("catalog:a", str(tmp_path / "missing.glc")))
    wait_for(qapp, lambda: service.source_error("catalog:a") is not None, timeout=30)
    assert service.source_result("catalog:a") == original
    assert len(service.snapshot.materials) == 4
    service.load_source(owned_source("catalog:a", ("Replacement",)))
    service.remove_source("catalog:a")
    service.remove_source("catalog:a")
    wait_for(qapp, lambda: not jobs.running, timeout=30)
    assert service.source_ids == ("catalog:b",)
    assert service.source_error("catalog:a") is None


def test_real_worker_design_invalidation_cancellation_and_close(materials, qapp):
    state, jobs, service = materials
    design_id = f"design:{state.token.document_id}"
    request = service.load_source(owned_source(design_id))
    started = []

    def change_while_running(job, status):
        if job.job_id == request.job_id and status == "running":
            started.append(True)
            state.change()  # active source is revoked before process publication

    jobs.state_changed.connect(change_while_running)
    wait_for(qapp, lambda: started and not jobs.running, timeout=30)
    jobs.state_changed.disconnect(change_while_running)
    assert service.source_ids == ()
    service.load_source(owned_source())
    wait_for(qapp, lambda: bool(service.source_ids), timeout=30)
    service.query(MaterialQuery())
    service.cancel_query()
    wait_for(qapp, lambda: not jobs.running, timeout=30)
    assert service.last_query_result is None
    service.load_source(owned_source("catalog:b"))
    service.close()
    service.close()
    wait_for(qapp, lambda: not jobs.running, timeout=30)
    assert not service.busy and not service.source_ids
    # Other consumers can still submit on the same persistent CalculationJobs.
    assert not jobs._closed
    with pytest.raises(RuntimeError, match="closed"):
        service.load_source(owned_source())
    with pytest.raises(RuntimeError, match="closed"):
        service.query(MaterialQuery())


def test_latest_source_and_query_replace_older_delivery(materials):
    state, jobs, service = materials
    first = service.load_source(owned_source())
    second = service.load_source(owned_source(names=("New",)))
    stale = load_source(owned_source())
    jobs.finished.emit(JobResult(first, "succeeded", stale, current=True))
    assert not service.source_ids
    latest = load_source(owned_source(names=("New",)))
    jobs.finished.emit(JobResult(second, "succeeded", latest, current=True))
    assert service.snapshot.materials[0].display_name == "New"
    first_query = service.query(MaterialQuery(name="Nothing"))
    service.query(MaterialQuery(name="New"))
    jobs.finished.emit(
        JobResult(
            first_query,
            "succeeded",
            query_materials(stale.snapshot, MaterialQuery()),
            current=True,
        )
    )
    assert service.last_query_result is None
    assert len(finish_query(jobs, service).matches) == 1
    assert service.last_query_result is not None
    service.load_source(owned_source(names=("Newest",)))
    service.cancel_source("catalog:a")
    service.cancel_source("catalog:a")  # cancelling an idle source is harmless
    assert service.source_result("catalog:a") == latest
    service.remove_source("catalog:a")
    assert service.last_query_result is None and not service.source_ids


def test_real_worker_registry_result_survives_source_file_removal(
    materials, qapp, tmp_path
):
    state, jobs, service = materials
    path = tmp_path / "private.yml"
    path.write_text(
        "DATA:\n  - type: formula 5\n    wavelength_range: 0.4 0.8\n"
        "    coefficients: 1.5 0.02 -2\n",
        encoding="utf-8",
    )
    entry = RegistryEntry(
        "private", "exact-row", "Lab", "glass", "Private", None, str(path)
    )
    service.load_source(RegistrySource("private", (entry,)))
    wait_for(qapp, lambda: service.source_result("private") is not None, timeout=30)
    saved = service.snapshot.materials[0]
    path.unlink()
    service.query(MaterialQuery())
    wait_for(qapp, lambda: service.last_query_result is not None, timeout=30)
    assert service.last_query_result.matches[0].key == saved.key
    material = restore_material(service.material(saved.key).owned_data)
    assert material.n(0.5).item() == pytest.approx(1.58)


def test_connector_owns_one_shared_queue_and_closes_materials(qapp, monkeypatch):
    from optiland_gui.optiland_connector import OptilandConnector

    connector = OptilandConnector()
    service, jobs = connector.materials, connector.calculation_jobs
    try:
        assert service.jobs is jobs
        assert service.document is connector.document_state
        assert connector.calculation_services == [jobs]
        service.load_source(owned_source())
        wait_for(qapp, lambda: service.source_ids, timeout=30)
        service.query(MaterialQuery())
        wait_for(qapp, lambda: service.last_query_result is not None, timeout=30)
        original = service.snapshot
        monkeypatch.setattr(BackendConfig, "capture", lambda: BackendConfig("torch"))
        connector._check_calculation_backend()
        assert service.last_query_result is None
        assert service.snapshot == original  # portable source definitions survive
        jobs.shutdown()
        wait_for(qapp, lambda: service._closed and jobs._process is None, timeout=30)
        assert not service.busy and not service.source_ids
        assert jobs._closed
    finally:
        connector._backend_timer.stop()
        jobs.shutdown()
        wait_for(qapp, lambda: jobs._process is None, timeout=30)
        connector.deleteLater()


def test_design_changes_revoke_only_design_and_mixed_queries(materials):
    state, jobs, service = materials
    design_id = f"design:{state.token.document_id}"
    publish(jobs, service, owned_source())
    publish(jobs, service, owned_source(design_id))
    service.query(MaterialQuery(source_ids=("catalog:a",)))
    state.change()
    assert service.source_ids == ("catalog:a",)
    assert service._query is not None
    finish_query(jobs, service, current=False)
    assert service.last_query_result is not None
    publish(jobs, service, owned_source(design_id))
    service.query(MaterialQuery())
    state.record("metadata")
    state.record("presentation")
    assert service._query is not None
    state.change()
    assert service._query is None
    publish(jobs, service, owned_source(design_id))
    service.query(MaterialQuery())
    finish_query(jobs, service)
    state.replace()
    assert service.last_query_result is None
    assert service.source_ids == ("catalog:a",)
    assert service.load_source(owned_source(design_id)) is None
    assert "another document" in service.source_error(design_id)


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize("qualified", [False, True])
def test_excluded_source_publication_and_removal_preserve_catalog_query(
    materials, finished, qualified
):
    state, jobs, service = materials
    design_id = f"design:{state.token.document_id}"
    publish(jobs, service, owned_source())
    publish(jobs, service, owned_source(design_id))
    query = (
        MaterialQuery(catalog_keys=(("catalog:a", "Owned"),))
        if qualified
        else MaterialQuery(source_ids=("catalog:a",))
    )
    service.query(query)
    pending = service._query
    assert tuple(stamp.source_id for stamp in pending.snapshot.stamps) == ("catalog:a",)
    assert pending.document is None
    original = finish_query(jobs, service) if finished else None
    invalidations = []
    service.query_invalidated.connect(lambda: invalidations.append(True))
    state.change()
    publish(jobs, service, owned_source(design_id, ("Fresh design",)))
    publish(jobs, service, owned_source("catalog:b"))
    service.remove_source("catalog:b")
    service.remove_source(design_id)
    assert not invalidations
    if finished:
        assert service.last_query_result is original
    else:
        assert service._query is pending
        assert len(finish_query(jobs, service).matches) == 2


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize(
    "query",
    [
        MaterialQuery(),
        MaterialQuery(source_ids=("catalog:a", "catalog:new")),
        MaterialQuery(catalog_keys=(("catalog:a", "Owned"), ("catalog:new", "Owned"))),
    ],
)
def test_new_selected_source_invalidates_pending_and_cached_queries(
    materials, finished, query
):
    state, jobs, service = materials
    publish(jobs, service, owned_source())
    service.query(query)
    if finished:
        finish_query(jobs, service)
    invalidations = []
    service.query_invalidated.connect(lambda: invalidations.append(True))
    publish(jobs, service, owned_source("catalog:new"))
    assert invalidations == [True]
    assert service._query is None and service.last_query_result is None


def test_empty_qualified_catalog_selection_never_claims_source_generations(materials):
    state, jobs, service = materials
    publish(jobs, service, owned_source())
    design_id = f"design:{state.token.document_id}"
    publish(jobs, service, owned_source(design_id))
    service.query(MaterialQuery(catalog_keys=()))
    pending = service._query
    assert not pending.snapshot.stamps and not pending.snapshot.materials
    assert pending.document is None
    service.remove_source("catalog:a")
    state.change()
    publish(jobs, service, owned_source("catalog:new"))
    assert service._query is pending
    result = finish_query(jobs, service)
    assert not result.matches and not result.stamps
    service.remove_source("catalog:new")
    assert service.last_query_result is result


@pytest.mark.parametrize("finished", [False, True])
def test_selected_source_removal_invalidates_qualified_query(materials, finished):
    state, jobs, service = materials
    publish(jobs, service, owned_source())
    service.query(MaterialQuery(catalog_keys=(("catalog:a", "Owned"),)))
    if finished:
        finish_query(jobs, service)
    service.remove_source("catalog:a")
    assert service._query is None and service.last_query_result is None


def test_design_removed_callback_can_query_fresh_current_generation(materials):
    state, jobs, service = materials
    design_id = f"design:{state.token.document_id}"
    publish(jobs, service, owned_source(design_id))
    service.query(MaterialQuery())
    fresh = []

    def removed(source_id):
        assert source_id == design_id
        publish(jobs, service, owned_source(design_id, ("Fresh design",)))
        fresh.append(service.query(MaterialQuery(source_ids=(design_id,))))

    service.source_removed.connect(removed)
    state.change()
    assert service._query.request is fresh[0]
    assert service._query.document == state.token
    assert len(finish_query(jobs, service).matches) == 1


def test_cache_limits_fail_atomically_without_evicting_old_data(materials):
    state, jobs, service = materials
    service.max_sources = 1
    service.max_records = 2
    publish(jobs, service, owned_source())
    assert service.load_source(owned_source("catalog:b")) is None
    assert "limit" in service.source_error("catalog:b")
    old = service.source_result("catalog:a")
    request = service.load_source(owned_source(names=("A", "B", "C")))
    jobs.finished.emit(
        JobResult(
            request,
            "succeeded",
            load_source(owned_source(names=("A", "B", "C"))),
            current=True,
        )
    )
    assert service.source_result("catalog:a") == old
    assert "capacity" in service.source_error("catalog:a")
    assert service.source_error("catalog:b") is None  # bounded diagnostic history
    service.max_sources = 2
    request = service.load_source(owned_source("catalog:b"))
    service.max_sources = 1  # capacity can change while a load is in flight
    jobs.finished.emit(
        JobResult(
            request, "succeeded", load_source(owned_source("catalog:b")), current=True
        )
    )
    assert service.source_result("catalog:a") == old
    service.max_payload_bytes = 1
    publish(jobs, service, owned_source())
    assert service.source_result("catalog:a") == old
    for kwargs in ({"max_sources": 0}, {"max_records": 0}, {"max_payload_bytes": 0}):
        with pytest.raises(ValueError, match="positive"):
            MaterialsService(jobs, state, **kwargs)


@pytest.mark.parametrize(
    "status,data,error",
    [
        ("failed", None, "Worker failed"),
        ("succeeded", object(), ""),
        (
            "succeeded",
            SourceResult(MaterialSnapshot((SourceStamp("other", "1"),), ()), (), False),
            "",
        ),
        (
            "succeeded",
            SourceResult(
                MaterialSnapshot((SourceStamp("catalog:a", "1"),), ()),
                (SourceDiagnostic("catalog:a", None, "Unreadable"),),
                True,
            ),
            "",
        ),
        (
            "succeeded",
            SourceResult(
                MaterialSnapshot((SourceStamp("catalog:a", "1"),), ()), (), True
            ),
            "",
        ),
    ],
)
def test_failed_or_invalid_source_response_is_visible(materials, status, data, error):
    state, jobs, service = materials
    request = service.load_source(owned_source())
    jobs.finished.emit(JobResult(request, status, data, error, True))
    assert service.source_error("catalog:a")
    assert not service.source_ids and not service.busy


@pytest.mark.parametrize("status", ["cancelled", "backend_changed"])
def test_cancelled_or_backend_stale_source_is_never_published(
    materials, monkeypatch, status
):
    state, jobs, service = materials
    request = service.load_source(owned_source())
    if status == "backend_changed":
        monkeypatch.setattr(BackendConfig, "capture", lambda: BackendConfig("torch"))
        status = "succeeded"
    jobs.finished.emit(JobResult(request, status, None, "", True))
    assert not service.source_ids and not service.busy


@pytest.mark.parametrize(
    "failure",
    [
        "failed",
        "cancelled",
        "wrong_type",
        "wrong_id",
        "wrong_stamp",
        "removed_stamp",
        "backend_changed",
    ],
)
def test_query_failures_and_stale_generations_are_not_presented(
    materials, monkeypatch, failure
):
    state, jobs, service = materials
    publish(jobs, service, owned_source())
    request = service.query(MaterialQuery())
    pending = service._query
    result = query_materials(
        pending.snapshot, MaterialQuery(), request_id=pending.serial
    )
    errors, invalidated = [], []
    service.query_failed.connect(errors.append)
    service.query_invalidated.connect(lambda: invalidated.append(True))
    if failure == "wrong_type":
        result = object()
    elif failure == "wrong_id":
        result = replace(result, request_id=-1)
    elif failure == "wrong_stamp":
        result = replace(result, stamps=())
    elif failure == "removed_stamp":
        service._sources.clear()  # defensive delivery-time stamp check
    elif failure == "backend_changed":
        monkeypatch.setattr(BackendConfig, "capture", lambda: BackendConfig("torch"))
    status = failure if failure in {"failed", "cancelled"} else "succeeded"
    jobs.finished.emit(JobResult(request, status, result, "Worker unavailable", True))
    assert service.last_query_result is None and not service.busy
    assert errors if failure == "failed" else invalidated


def test_queue_errors_are_reported_and_closed_service_ignores_delivery(
    materials, monkeypatch
):
    state, jobs, service = materials
    errors = []
    service.query_failed.connect(errors.append)

    def full(*args, **kwargs):
        raise RuntimeError("Queue full")

    monkeypatch.setattr(jobs, "submit", full)
    assert service.load_source(owned_source()) is None
    assert service.source_error("catalog:a") == "Queue full"
    assert service.query(MaterialQuery()) is None
    assert errors == ["Queue full"]
    service.close()
    jobs.finished.emit(JobResult(None, "succeeded"))
    assert not service.source_ids


@pytest.mark.parametrize("action", ["edit", "cancel", "new_query", "close", "remove"])
def test_query_ready_callback_cannot_publish_revoked_result(materials, action):
    state, jobs, service = materials
    source_id = f"design:{state.token.document_id}"
    publish(jobs, service, owned_source(source_id))
    service.query(MaterialQuery())
    delivered = []
    service.query_finished.connect(delivered.append)

    def ready(target, status):
        if target != "query" or status != "ready":
            return
        service.state_changed.disconnect(ready)
        if action == "edit":
            state.change()
        elif action == "cancel":
            service.cancel_query()
        elif action == "new_query":
            service.query(MaterialQuery(name="Flint"))
        elif action == "close":
            service.close()
        else:
            service.remove_source(source_id)

    service.state_changed.connect(ready)
    finish_query(jobs, service)
    assert delivered == []
    assert service.last_query_result is None
    if action == "new_query":
        expected = finish_query(jobs, service)
        assert delivered == [expected]
        assert len(expected.matches) == 1


@pytest.mark.parametrize("action", ["remove", "close", "edit"])
def test_source_ready_callback_cannot_publish_removed_source(materials, action):
    state, jobs, service = materials
    source_id = f"design:{state.token.document_id}"
    delivered = []
    service.source_changed.connect(delivered.append)

    def ready(target, status):
        if target == source_id and status == "ready":
            if action == "remove":
                service.remove_source(source_id)
            elif action == "close":
                service.close()
            else:
                state.change()

    service.state_changed.connect(ready)
    publish(jobs, service, owned_source(source_id))
    assert delivered == []
    assert service.source_result(source_id) is None


def test_source_publication_query_invalidation_callback_can_close_service(materials):
    state, jobs, service = materials
    publish(jobs, service, owned_source())
    service.query(MaterialQuery())
    finish_query(jobs, service)
    service.query_invalidated.connect(service.close)
    delivered = []
    service.source_changed.connect(delivered.append)
    publish(jobs, service, owned_source(names=("New",)))
    assert service._closed and delivered == []


@pytest.mark.parametrize("kind", ["source", "query"])
@pytest.mark.parametrize(
    "action", ["edit", "cancel", "close", "remove", "newer", "backend", "queue_cancel"]
)
def test_queued_callback_never_leaves_dead_or_superseded_request_owned(
    materials, monkeypatch, kind, action
):
    state, jobs, service = materials
    source_id = f"design:{state.token.document_id}"
    source = owned_source(source_id)
    if kind == "query":
        published, _ = publish(jobs, service, source)
        # The synthetic successful publication bypasses the real queue's dequeue.
        jobs.cancel_target(published.target)
    fired = []
    newer = []

    def queued(request, status):
        if status != "queued" or fired:
            return
        fired.append(True)
        if action == "edit":
            state.change()
        elif action == "cancel":
            if kind == "source":
                service.cancel_source(source_id)
            else:
                service.cancel_query()
        elif action == "close":
            service.close()
        elif action == "remove":
            service.remove_source(source_id)
        elif action == "newer":
            newer.append(
                service.load_source(owned_source(source_id, ("New",)))
                if kind == "source"
                else service.query(MaterialQuery(name="Flint"))
            )
        elif action == "backend":
            monkeypatch.setattr(
                BackendConfig, "capture", lambda: BackendConfig("torch")
            )
        else:
            jobs.cancel_target(request.target)

    jobs.state_changed.connect(queued)
    request = (
        service.load_source(source)
        if kind == "source"
        else service.query(MaterialQuery())
    )
    assert fired and request is None
    if action == "newer":
        assert service.busy and newer[0] is not None
        pending = service._loads[source_id] if kind == "source" else service._query
        assert pending.request is newer[0]
    else:
        assert not service.busy
        assert not jobs.running


@pytest.mark.parametrize("kind", ["source", "query"])
def test_catalog_request_queued_callback_document_edit_remains_valid(materials, kind):
    state, jobs, service = materials
    source = owned_source()
    if kind == "query":
        publish(jobs, service, source)
    jobs.state_changed.connect(
        lambda request, status: state.change() if status == "queued" else None
    )
    request = (
        service.load_source(source)
        if kind == "source"
        else service.query(MaterialQuery())
    )
    assert request is not None and service.busy
    if kind == "source":
        jobs.finished.emit(
            JobResult(request, "succeeded", load_source(source), current=False)
        )
        assert service.source_ids == (source.source_id,)
    else:
        finish_query(jobs, service, current=False)
        assert service.last_query_result is not None


@pytest.mark.parametrize("action", ["newer", "close", "cancel"])
def test_query_invalidation_callback_owns_newer_action(materials, action):
    state, jobs, service = materials
    publish(jobs, service, owned_source())
    service.query(MaterialQuery())
    finish_query(jobs, service)
    newer = []

    def invalidated():
        service.query_invalidated.disconnect(invalidated)
        if action == "newer":
            newer.append(service.query(MaterialQuery(name="Flint")))
        elif action == "cancel":
            service.cancel_query()
        else:
            service.close()

    service.query_invalidated.connect(invalidated)
    assert service.query(MaterialQuery(name="Crown")) is None
    if action == "newer":
        assert service._query.request is newer[0]
        assert len(finish_query(jobs, service).matches) == 1
    elif action == "close":
        assert service._closed and not service.busy
    else:
        assert not service.busy


def test_cancelled_queue_callback_can_start_new_source_without_stale_state(materials):
    state, jobs, service = materials
    old = service.load_source(owned_source())
    newer, statuses = [], []
    service.state_changed.connect(lambda source_id, status: statuses.append(status))

    def cancelled(result):
        if result.request is old and result.status == "cancelled":
            newer.append(service.load_source(owned_source(names=("New",))))

    jobs.finished.connect(cancelled)
    service.cancel_source("catalog:a")
    assert service._loads["catalog:a"].request is newer[0]
    assert service.busy and statuses == ["loading"]


@pytest.mark.parametrize("kind", ["source", "query"])
def test_submission_error_after_reentrant_close_does_not_emit_failure(
    materials, monkeypatch, kind
):
    state, jobs, service = materials
    errors = []
    service.source_failed.connect(lambda *args: errors.append(args))
    service.query_failed.connect(errors.append)

    def closing_submit(*args, **kwargs):
        service.close()
        raise RuntimeError("Submission ended after service closure")

    monkeypatch.setattr(jobs, "submit", closing_submit)
    request = (
        service.load_source(owned_source())
        if kind == "source"
        else service.query(MaterialQuery())
    )
    assert request is None and not service.busy and not errors
