"""Comparison presentation belongs to exact jobs, inputs and source generations."""

from __future__ import annotations

import importlib
import pickle
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from optiland.materials.comparison import SpectralRequest
from optiland.materials.properties import MaterialKey, MaterialQuery
from optiland_gui.services.calculation_jobs import CalculationJobs, DocumentState
from optiland_gui.services.job_records import BackendConfig, JobResult
from optiland_gui.services.material_comparison import MaterialComparisonService
from optiland_gui.services.materials_service import MaterialsService
from tests.gui.test_calculation_jobs import wait_for
from tests.gui.test_material_jobs import owned_source


@pytest.fixture
def comparison(qapp, monkeypatch):
    document = DocumentState()
    jobs = CalculationJobs(document, cancel_grace_ms=50)
    dispatch = jobs._dispatch
    monkeypatch.setattr(jobs, "_dispatch", lambda: None)
    materials = MaterialsService(jobs, document)
    service = MaterialComparisonService(
        SimpleNamespace(
            materials=materials, calculation_jobs=jobs, document_state=document
        )
    )
    events = SimpleNamespace(finished=[], failed=[], invalidated=[], states=[])
    service.finished.connect(lambda *args: events.finished.append(args))
    service.failed.connect(lambda *args: events.failed.append(args))
    service.invalidated.connect(events.invalidated.append)
    service.state_changed.connect(lambda *args: events.states.append(args))
    yield SimpleNamespace(
        document=document,
        jobs=jobs,
        materials=materials,
        service=service,
        events=events,
        dispatch=dispatch,
    )
    service.close()
    materials.close()
    jobs.shutdown()
    wait_for(qapp, lambda: jobs._process is None)


def computed(request):
    module, name = request.handler.split(":")
    return getattr(importlib.import_module(module), name)(
        request.snapshot, request.parameters, lambda *args: None, threading.Event()
    )


_UNSET = object()


def finish(fixture, request, *, data=_UNSET, status="succeeded", error=""):
    if request in fixture.jobs._pending:
        fixture.jobs._pending.remove(request)
    if data is _UNSET:
        data = computed(request) if status == "succeeded" else None
    fixture.jobs._terminal(request, status, data, error)
    return data


def publish(fixture, source_id="catalog:a", names=("Crown", "Flint")):
    request = fixture.materials.load_source(owned_source(source_id, names))
    return finish(fixture, request)


def first_key(fixture, source_id="catalog:a"):
    return fixture.materials.source_result(source_id).snapshot.materials[0].key


def request_kind(fixture, kind):
    if kind == "view":
        return fixture.service.request_view("partial-gf")
    if kind == "spectral":
        return fixture.service.request_comparison(
            SpectralRequest((first_key(fixture),), sample_count=5)
        )
    return fixture.service.request_nearby(first_key(fixture), 0.02, 5)


def test_map_and_nearby_borrow_exact_lightweight_cache_without_optical_payload(
    comparison,
):
    source = publish(comparison)
    stamp = source.snapshot.stamps[0]
    blob = comparison.materials._query_summaries[stamp]
    for kind in ("view", "nearby"):
        request = request_kind(comparison, kind)
        assert request.snapshot is None and not request.cancel_on_document_change
        assert request.parameters["stamps"] == (stamp,)
        assert request.parameters["material_blobs"] == (blob,)
        assert all(record.owned_data is None for record in pickle.loads(blob))
        result = finish(comparison, request)
        assert result.stamps == (stamp,)
        assert comparison.service.result(kind) is result
    assert [kind for kind, _ in comparison.events.finished] == ["view", "nearby"]


def test_spectral_transport_keeps_only_four_ordered_owned_records_and_missing_key(
    comparison,
):
    source = publish(comparison, names=tuple(f"Glass {i}" for i in range(6)))
    keys = tuple(record.key for record in source.snapshot.materials)
    requested = (keys[4], keys[0], keys[5], keys[2])
    request = comparison.service.request_comparison(
        SpectralRequest(requested, sample_count=5)
    )
    snapshot = request.parameters["material_snapshot"]
    assert tuple(record.key for record in snapshot.materials) == requested
    assert all(record.owned_data for record in snapshot.materials)
    assert not hasattr(snapshot, "connector")
    result = finish(comparison, request)
    assert tuple(series.key for series in result.series) == requested
    assert all(len(series.values) == 5 for series in result.series)
    missing = MaterialKey("missing", "catalog", "record")
    request = comparison.service.request_comparison(
        SpectralRequest((missing, keys[0]), sample_count=5)
    )
    assert len(request.parameters["material_snapshot"].materials) == 1
    result = finish(comparison, request)
    assert result.series[0].key == missing
    assert all(value.value is None for value in result.series[0].values)
    with pytest.raises(ValueError, match="at most four"):
        SpectralRequest(keys)
    with pytest.raises(ValueError, match="spectral request"):
        comparison.service.request_comparison(object())


def test_each_kind_supersedes_only_itself_and_duplicate_delivery_is_ignored(comparison):
    publish(comparison)
    old = {kind: request_kind(comparison, kind) for kind in comparison.service.KINDS}
    replacement = request_kind(comparison, "view")
    assert old["view"] not in comparison.jobs._pending
    assert old["spectral"] in comparison.jobs._pending
    assert old["nearby"] in comparison.jobs._pending
    finish(comparison, old["view"])
    assert comparison.service.result("view") is None
    for request in (replacement, old["spectral"], old["nearby"]):
        data = finish(comparison, request)
        comparison.jobs.finished.emit(
            JobResult(request, "succeeded", data, current=True)
        )
    assert len(comparison.events.finished) == 3
    with pytest.raises(ValueError, match="Unknown"):
        comparison.service.cancel("invalid")


@pytest.mark.parametrize("state", ["pending", "accepted"])
@pytest.mark.parametrize("action", ["add", "reload", "remove"])
def test_source_set_changes_invalidate_whole_map_and_nearby_but_only_related_spectra(
    comparison, state, action
):
    publish(comparison)
    publish(comparison, "catalog:b")
    requests = {
        kind: request_kind(comparison, kind) for kind in comparison.service.KINDS
    }
    if state == "accepted":
        for request in requests.values():
            finish(comparison, request)
    if action == "add":
        publish(comparison, "catalog:c")
    elif action == "reload":
        publish(comparison, "catalog:b", ("Changed",))
    else:
        comparison.materials.remove_source("catalog:b")
    assert comparison.service.result("view") is None
    assert comparison.service.result("nearby") is None
    assert {"view", "nearby"}.issubset(comparison.events.invalidated)
    assert "spectral" not in comparison.events.invalidated
    if state == "pending":
        finish(comparison, requests["spectral"])
    assert comparison.service.result("spectral") is not None
    comparison.materials.remove_source("catalog:a")
    assert comparison.service.result("spectral") is None
    assert comparison.events.invalidated[-1] == "spectral"


def test_failed_source_reload_keeps_results_and_missing_source_restoration_invalidates(
    comparison,
):
    publish(comparison)
    result = finish(comparison, request_kind(comparison, "view"))
    reload_request = comparison.materials.load_source(owned_source())
    finish(comparison, reload_request, status="failed", error="catalog unavailable")
    assert comparison.service.result("view") is result
    missing = MaterialKey("catalog:b", "", "0")
    request = comparison.service.request_comparison(
        SpectralRequest((missing,), sample_count=5)
    )
    finish(comparison, request)
    assert comparison.service.result("spectral") is not None
    publish(comparison, "catalog:b")
    assert comparison.service.result("spectral") is None


@pytest.mark.parametrize("kind", MaterialComparisonService.KINDS)
def test_catalog_computations_survive_unrelated_document_edits(comparison, kind):
    publish(comparison)
    assert not comparison.service.is_pending(kind)
    request = request_kind(comparison, kind)
    assert comparison.service.is_pending(kind)
    comparison.document.change()
    assert not comparison.jobs.is_current(request)
    assert comparison.service.is_pending(kind)
    result = finish(comparison, request)
    assert not comparison.service.is_pending(kind)
    assert comparison.service.result(kind) is result
    comparison.document.change()
    assert comparison.service.result(kind) is result


def test_design_and_backend_changes_revoke_only_their_actual_inputs(
    comparison, monkeypatch
):
    publish(comparison)
    catalog = request_kind(comparison, "spectral")
    design_id = f"design:{comparison.document.token.document_id}"
    publish(comparison, design_id)
    view = request_kind(comparison, "view")
    assert view.cancel_on_document_change
    comparison.document.change()
    assert view not in comparison.jobs._pending and catalog in comparison.jobs._pending
    result = finish(comparison, catalog)
    assert comparison.service.result("spectral") is result
    alternate = replace(BackendConfig.capture(), precision=32)
    monkeypatch.setattr(BackendConfig, "capture", classmethod(lambda cls: alternate))
    assert comparison.service.result("spectral") is None
    comparison.document.change()
    assert "spectral" in comparison.events.invalidated


@pytest.mark.parametrize("kind", MaterialComparisonService.KINDS)
def test_queue_cancellation_before_submit_returns_never_restores_pending_owner(
    comparison, kind
):
    publish(comparison)

    def stop(request, state):
        if state == "queued" and request.handler.startswith(
            "optiland_gui.services.material_comparison_jobs:"
        ):
            comparison.service.cancel(kind)

    comparison.jobs.state_changed.connect(stop)
    assert request_kind(comparison, kind) is None
    assert kind not in comparison.service._pending
    assert comparison.service.result(kind) is None
    assert (kind, "Calculating…") not in comparison.events.states


def test_ready_callback_replaces_result_without_delivering_old_finished(comparison):
    publish(comparison)
    original = request_kind(comparison, "view")
    replacements = []

    def change_view(kind, state):
        if kind == "view" and state == "Ready" and not replacements:
            replacements.append(comparison.service.request_view("partial-dc"))

    comparison.service.state_changed.connect(change_view)
    finish(comparison, original)
    assert not comparison.events.finished
    fresh = finish(comparison, replacements[0])
    assert comparison.events.finished == [("view", fresh)]


@pytest.mark.parametrize("action", ["replace", "close"])
def test_invalidated_callback_owns_its_replacement_before_outer_submit(
    comparison, action
):
    publish(comparison)
    finish(comparison, request_kind(comparison, "view"))
    replacements = []

    def invalidated(kind):
        if kind == "view" and not replacements:
            if action == "close":
                comparison.service.close()
                replacements.append(None)
            else:
                replacements.append(comparison.service.request_view("partial-dc"))

    comparison.service.invalidated.connect(invalidated)
    assert comparison.service.request_view("nd-dispersion") is None
    if action == "replace":
        assert comparison.service._pending["view"].request is replacements[0]
        assert finish(comparison, replacements[0]).view == "partial-dc"
    else:
        assert comparison.service.result("view") is None


def test_lens_edit_in_queued_callback_keeps_catalog_job_without_placeholder_request(
    comparison,
):
    publish(comparison)

    def edit(request, state):
        if state == "queued" and request.handler.endswith("compute_map_view"):
            assert request.context.request is None
            comparison.document.change()

    comparison.jobs.state_changed.connect(edit)
    request = request_kind(comparison, "view")
    assert request is not None and request in comparison.jobs._pending
    result = finish(comparison, request)
    assert comparison.service.result("view") is result


def test_design_spectrum_is_revoked_on_document_edit(comparison):
    source_id = f"design:{comparison.document.token.document_id}"
    publish(comparison, source_id)
    request = comparison.service.request_comparison(
        SpectralRequest((first_key(comparison, source_id),), sample_count=5)
    )
    assert request.cancel_on_document_change
    comparison.document.change()
    finish(comparison, request)
    assert comparison.service.result("spectral") is None
    assert not comparison.events.finished


@pytest.mark.parametrize("action", ["cancel", "hide", "close"])
def test_terminal_state_callback_cannot_publish_revoked_worker_result(
    comparison, action
):
    publish(comparison)
    request = request_kind(comparison, "view")

    def revoke(done, status):
        if done is request and status == "succeeded":
            if action == "cancel":
                comparison.jobs.cancel_target(done.target)
            elif action == "hide":
                comparison.jobs.set_target_visible(done.target, False)
            else:
                comparison.jobs.shutdown()

    comparison.jobs.state_changed.connect(revoke)
    finish(comparison, request)
    assert not comparison.events.finished
    assert comparison.service.result("view") is None


@pytest.mark.parametrize("kind", ["view", "spectral"])
def test_source_published_during_supersession_cannot_admit_old_source_capture(
    comparison, kind
):
    publish(comparison)
    if kind == "view":
        request = request_kind(comparison, kind)

        def resubmit():
            return request_kind(comparison, kind)
    else:
        missing = MaterialKey("catalog:b", "", "0")
        spec = SpectralRequest((missing,), sample_count=5)
        request = comparison.service.request_comparison(spec)

        def resubmit():
            return comparison.service.request_comparison(spec)

    finish(comparison, request)
    restored = []

    def restore_source(invalidated):
        if invalidated == kind and not restored:
            restored.append(True)
            publish(comparison, "catalog:b")

    comparison.service.invalidated.connect(restore_source)
    assert resubmit() is None
    assert kind not in comparison.service._pending
    assert comparison.service.result(kind) is None


def test_source_invalidation_does_not_cancel_fresh_sibling_request_from_callback(
    comparison,
):
    publish(comparison)
    for kind in ("view", "nearby"):
        finish(comparison, request_kind(comparison, kind))
    new = []

    def refresh_nearby(kind):
        if kind == "view":
            new.append(request_kind(comparison, "nearby"))

    comparison.service.invalidated.connect(refresh_nearby)
    publish(comparison, "catalog:b")
    assert new[0] is not None and new[0] in comparison.jobs._pending
    result = finish(comparison, new[0])
    assert comparison.service.result("nearby") is result


@pytest.mark.parametrize("kind", MaterialComparisonService.KINDS)
@pytest.mark.parametrize("mismatch", ["type", "stamps", "spec"])
def test_mismatched_results_fail_without_presentation(comparison, kind, mismatch):
    publish(comparison)
    request = request_kind(comparison, kind)
    result = computed(request)
    if mismatch == "type":
        result = object()
    elif mismatch == "stamps":
        result = replace(result, stamps=())
    elif kind == "view":
        result = replace(result, view="partial-dc")
    elif kind == "spectral":
        result = replace(result, request=replace(result.request, sample_count=7))
    else:
        result = replace(result, nd_scale=2)
    finish(comparison, request, data=result)
    assert comparison.service.result(kind) is None
    assert comparison.events.failed[-1] == (
        kind,
        "The calculation returned different material inputs.",
    )
    assert not comparison.events.finished


def test_submission_failure_terminal_failure_and_reentrant_retry_are_owned(
    comparison, monkeypatch
):
    publish(comparison)
    submit = comparison.jobs.submit
    monkeypatch.setattr(
        comparison.jobs,
        "submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue full")),
    )
    assert request_kind(comparison, "view") is None
    assert comparison.events.failed[-1] == ("view", "queue full")
    monkeypatch.setattr(comparison.jobs, "submit", submit)
    old = request_kind(comparison, "view")
    retries = []
    comparison.service.failed.connect(
        lambda *args: retries.append(request_kind(comparison, "view"))
    )
    finish(comparison, old, status="failed", error="x" * 3000)
    assert len(comparison.events.failed[-1][1]) == 2000
    assert retries[0] is not None
    assert comparison.service._pending["view"].request is retries[0]
    finish(comparison, retries[0])
    assert comparison.service.result("view") is not None


def test_close_cancels_only_comparison_jobs_and_rejects_reentry(comparison):
    publish(comparison)
    unrelated = comparison.materials.query(MaterialQuery())
    requests = [request_kind(comparison, kind) for kind in comparison.service.KINDS]
    attempts = []
    comparison.service.invalidated.connect(
        lambda kind: attempts.append(request_kind(comparison, kind))
    )
    comparison.service.close()
    comparison.service.close()
    assert attempts == [None, None, None]
    assert unrelated in comparison.jobs._pending
    assert all(request not in comparison.jobs._pending for request in requests)
    assert not comparison.jobs._closed
    for request in requests:
        finish(comparison, request)
    assert not comparison.events.finished


def test_real_shared_worker_runs_all_service_kinds_with_owned_sources(
    comparison, qapp, monkeypatch
):
    monkeypatch.setattr(comparison.jobs, "_dispatch", comparison.dispatch)
    comparison.materials.load_source(owned_source())
    wait_for(
        qapp,
        lambda: comparison.materials.source_result("catalog:a") is not None,
        timeout=30,
    )
    initial_token = comparison.document.token
    for kind in comparison.service.KINDS:
        request_kind(comparison, kind)
    wait_for(qapp, lambda: len(comparison.events.finished) == 3, timeout=30)
    assert not comparison.events.failed
    assert comparison.document.token == initial_token
    assert all(
        comparison.service.result(kind) is not None for kind in comparison.service.KINDS
    )
