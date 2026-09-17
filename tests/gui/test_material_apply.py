"""Exact material Apply owns its surface, source generation, history and callbacks."""

from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from optiland.materials.portability import capture_material
from optiland.materials.properties import MaterialKey, SourceStamp
from optiland.materials.sources import OwnedEntry, OwnedSource, load_source
from optiland_gui.services.job_records import BackendConfig, JobResult
from optiland_gui.services.material_apply_tasks import prepare_material_change
from optiland_gui.services.prepared_optic import PreparedOptic
from tests.gui.test_calculation_jobs import wait_for
from tests.gui.test_material_apply_tasks import payload


def install(connector, *, source_id="test:glass", data=None):
    source = OwnedSource(
        source_id,
        (OwnedEntry("exact", "Selected", payload() if data is None else data, None),),
    )
    result = load_source(source)
    connector.materials._finish_source(
        source_id,
        SimpleNamespace(backend=BackendConfig.capture(), document=None),
        SimpleNamespace(status="succeeded", data=result),
    )
    return result.snapshot.materials[0].key, result.snapshot.stamps[0]


@pytest.fixture
def ready(highlighting_connector):
    connector = highlighting_connector
    key, stamp = install(connector)
    service = connector.material_apply
    assert service.set_target(connector.get_optic().surfaces[1]).allowed
    yield connector, service, key, stamp
    service.close()


def prepare_request(request):
    return prepare_material_change(
        request.snapshot, request.parameters, lambda *args: None, threading.Event()
    )


def complete(connector, service, key, stamp):
    request = service.submit(key, stamp)
    assert request is not None
    data = prepare_request(request)
    connector.calculation_jobs.finished.emit(
        JobResult(request, "succeeded", data, current=True)
    )
    return request, data


def test_apply_is_one_transaction_maps_after_assignment_and_retains_target(ready):
    connector, service, key, stamp = ready
    old = connector.get_optic()
    token = connector.document_state.edit_token
    history = connector._undo_redo_manager
    revision = history.revision
    notifications, outcomes, mappings = [], [], []
    connector.document_state.committed.connect(notifications.append)
    service.finished.connect(outcomes.append)

    def mapped(replacement):
        assert connector.get_optic() is replacement.new_optic
        assert connector.document_state.edit_token != token
        assert history.revision == revision + 1
        assert replacement.mapping[1] == (
            old.surfaces[1],
            connector.get_optic().surfaces[1],
        )
        with pytest.raises(ValueError, match="being applied"):
            connector.document_acceptance.assert_mutation_allowed()
        service.cancel()  # already assigned: no contradictory cancellation event
        mappings.append(replacement)

    connector.document_acceptance.model_replaced.connect(mapped)
    complete(connector, service, key, stamp)
    assert len(mappings) == len(notifications) == len(outcomes) == 1
    assert outcomes[0].status == "applied"
    assert connector.is_modified() and history.revision == revision + 1
    assert service.target.surface is connector.get_optic().surfaces[1]
    assert service.target.edit_token == connector.document_state.edit_token
    assert service.can_apply(key, stamp).allowed
    assert (
        capture_material(connector.get_optic().surfaces[1].material_post) == payload()
    )
    assert old.surfaces[1].material_post.to_dict()["type"] != "DataMaterial"


def test_real_worker_apply_then_async_undo_redo_restores_exact_native_data(ready, qapp):
    connector, service, key, stamp = ready
    original = connector.get_optic().surfaces[1].material_post.to_dict()
    outcomes = []
    service.finished.connect(outcomes.append)
    assert service.submit(key, stamp) is not None
    wait_for(qapp, lambda: outcomes, timeout=30)
    assert outcomes[-1].status == "applied"
    assert connector.request_undo() is not None
    wait_for(qapp, lambda: not connector.history.busy, timeout=30)
    assert connector.get_optic().surfaces[1].material_post.to_dict() == original
    assert service.target is None
    assert connector.request_redo() is not None
    wait_for(qapp, lambda: not connector.history.busy, timeout=30)
    assert (
        capture_material(connector.get_optic().surfaces[1].material_post) == payload()
    )


def test_file_to_owned_is_real_change_but_repeated_owned_apply_is_noop(ready):
    connector, service, _, _ = ready
    key, stamp = install(
        connector,
        data=capture_material(connector.get_optic().surfaces[1].material_post),
    )
    outcomes = []
    service.finished.connect(outcomes.append)
    complete(connector, service, key, stamp)
    assert outcomes[-1].status == "applied"
    optic, revision, token = (
        connector.get_optic(),
        connector._undo_redo_manager.revision,
        connector.document_state.edit_token,
    )
    complete(connector, service, key, stamp)
    assert outcomes[-1].status == "unchanged"
    assert connector.get_optic() is optic
    assert connector._undo_redo_manager.revision == revision
    assert connector.document_state.edit_token == token


@pytest.mark.parametrize(
    "action", ["edit", "source", "remove", "cancel", "close", "backend", "history"]
)
def test_changed_ownership_never_accepts_late_prepared_result(
    ready, monkeypatch, action
):
    connector, service, key, stamp = ready
    request = service.submit(key, stamp)
    data = prepare_request(request)
    original, revision = connector.get_optic(), connector._undo_redo_manager.revision
    outcomes = []
    service.finished.connect(outcomes.append)
    if action == "edit":
        connector.notify_change("metadata")
    elif action == "source":
        install(connector)  # even a same-generation accepted reload revokes ownership
    elif action == "remove":
        connector.materials.remove_source(key.source_id)
    elif action == "cancel":
        service.cancel()
    elif action == "close":
        service.close()
    elif action == "backend":
        monkeypatch.setattr(
            BackendConfig, "capture", lambda: BackendConfig("torch", "cpu", 32)
        )
    else:
        connector._undo_redo_manager.add_state(connector._capture_optic_state())
        revision += 1
    connector.calculation_jobs.finished.emit(
        JobResult(request, "succeeded", data, current=True)
    )
    assert connector.get_optic() is original
    assert connector._undo_redo_manager.revision == revision
    assert len(outcomes) == 1 and outcomes[0].status in {"cancelled", "stale"}
    assert not service.busy


@pytest.mark.parametrize("action", ["cancel", "source", "backend"])
def test_restore_callback_cannot_bypass_final_acceptance_check(
    ready, monkeypatch, action
):
    connector, service, key, stamp = ready
    request = service.submit(key, stamp)
    data = prepare_request(request)
    original, revision = connector.get_optic(), connector._undo_redo_manager.revision
    restore = PreparedOptic.restore
    outcomes = []
    service.finished.connect(outcomes.append)

    def reentrant(snapshot):
        candidate = restore(snapshot)
        if action == "cancel":
            service.cancel()
        elif action == "source":
            install(connector)
        else:
            monkeypatch.setattr(
                BackendConfig, "capture", lambda: BackendConfig("torch", "cpu", 32)
            )
        return candidate

    monkeypatch.setattr(PreparedOptic, "restore", reentrant)
    connector.calculation_jobs.finished.emit(
        JobResult(request, "succeeded", data, current=True)
    )
    assert (
        connector.get_optic() is original
        and connector._undo_redo_manager.revision == revision
    )
    assert len(outcomes) == 1 and outcomes[0].status in {"cancelled", "stale"}


@pytest.mark.parametrize("terminal", ["applied", "unchanged", "failed", "cancelled"])
def test_terminal_notification_keeps_request_owned_against_reentry(ready, terminal):
    connector, service, key, stamp = ready
    if terminal == "unchanged":
        complete(connector, service, key, stamp)
    attempts, statuses = [], []

    def finished(outcome):
        statuses.append(outcome.status)
        attempts.append(service.submit(key, stamp))
        service.cancel()

    service.finished.connect(finished)
    request = service.submit(key, stamp)
    if terminal == "cancelled":
        service.cancel()
    else:
        data = prepare_request(request) if terminal != "failed" else None
        connector.calculation_jobs.finished.emit(
            JobResult(
                request,
                "failed" if terminal == "failed" else "succeeded",
                data,
                "fixture failure",
                True,
            )
        )
    assert statuses == [terminal]
    assert attempts == [None] and not service.busy


def test_unmapped_new_document_revokes_target_and_closed_service_cannot_capture(ready):
    connector, service, _, _ = ready
    connector.new_system()
    assert service.target is None
    assert "replaced" in service.message
    service.close()
    assert not service.set_target(connector.get_optic().surfaces[1]).allowed
    assert service.target is None


@pytest.mark.parametrize(
    "owner", ["history", "placement", "file_operations", "_optimization_service"]
)
def test_competing_operation_prevents_preparation(ready, monkeypatch, owner):
    connector, service, key, stamp = ready
    with monkeypatch.context() as patch:
        patch.setattr(connector, owner, SimpleNamespace(busy=True, is_running=True))
        assert not service.can_apply(key, stamp).allowed
        assert service.submit(key, stamp) is None
    assert service.can_apply(key, stamp).allowed


def test_preparation_blocks_other_replacements_but_allows_staling_edits(ready):
    connector, service, key, stamp = ready
    service.submit(key, stamp)
    for owner in ("history", "placement", "file", "optimization", "scientific"):
        assert not connector.document_acceptance.check_start(owner).allowed
    connector.document_acceptance.assert_mutation_allowed()
    connector.notify_change("metadata")
    assert not service.busy
    assert service.can_apply(key, stamp).code == "stale"
    assert service.refresh_target().allowed


def test_missing_identity_payload_and_changed_stamp_are_ineligible(ready):
    connector, service, key, stamp = ready
    assert not service.can_apply(None, None).allowed
    assert (
        service.can_apply(key, SourceStamp(key.source_id, "other")).code
        == "source-stale"
    )
    assert not service.can_apply(
        MaterialKey(key.source_id, key.catalog, "missing"), stamp
    ).allowed
    service.set_target(connector.get_optic().surfaces[-1])
    assert service.can_apply(key, stamp).code == "unsupported"
    service.set_target(None)
    assert service.can_apply(key, stamp).code == "no-target"


@pytest.mark.parametrize("action", ["cancel", "source", "close"])
def test_queued_callback_cannot_resurrect_cancelled_preparation(ready, action):
    connector, service, key, stamp = ready
    outcomes = []
    service.finished.connect(outcomes.append)

    def queued(request, state):
        if request.target != service.job_target or state != "queued":
            return
        if action == "source":
            install(connector)
        elif action == "close":
            service.close()
        else:
            service.cancel()

    connector.calculation_jobs.state_changed.connect(queued)
    assert service.submit(key, stamp) is None
    assert not service.busy
    assert len(outcomes) == 1 and outcomes[0].status == "cancelled"


def test_submit_failure_keeps_ownership_through_failure_notification(
    ready, monkeypatch
):
    connector, service, key, stamp = ready
    attempts = []
    service.finished.connect(lambda _: attempts.append(service.submit(key, stamp)))

    def fail(*args, **kwargs):
        raise ValueError("queue fixture")

    monkeypatch.setattr(connector.calculation_jobs, "submit", fail)
    assert service.submit(key, stamp) is None
    assert attempts == [None] and not service.busy


def test_capture_failure_does_not_reenter_before_pending_context_exists(
    ready, monkeypatch
):
    _, service, key, stamp = ready
    attempts, captures, outcomes = [], [], []

    def fail(_):
        captures.append(True)
        raise ValueError("snapshot fixture")

    def finished(outcome):
        outcomes.append(outcome)
        attempts.append(service.submit(key, stamp))

    monkeypatch.setattr(PreparedOptic, "capture", fail)
    service.finished.connect(finished)
    assert service.submit(key, stamp) is None
    assert captures == [True] and attempts == [None] and len(outcomes) == 1


@pytest.mark.parametrize("kind", ["type", "outcome", "cancelled"])
def test_invalid_or_cancelled_worker_delivery_does_not_mutate(ready, kind):
    connector, service, key, stamp = ready
    request = service.submit(key, stamp)
    data = prepare_request(request)
    original = connector.get_optic()
    revision = connector._undo_redo_manager.revision
    outcomes = []
    service.finished.connect(outcomes.append)
    if kind == "type":
        data = object()
    elif kind == "outcome":
        data = replace(data, outcome="invalid")
    connector.calculation_jobs.finished.emit(
        JobResult(
            request,
            "cancelled" if kind == "cancelled" else "succeeded",
            data,
            current=True,
        )
    )
    assert (
        connector.get_optic() is original
        and connector._undo_redo_manager.revision == revision
    )
    assert len(outcomes) == 1 and outcomes[0].status == (
        "cancelled" if kind == "cancelled" else "failed"
    )


def test_design_source_apply_ignores_own_source_eviction(ready):
    connector, service, _, _ = ready
    key, stamp = install(
        connector, source_id=f"design:{connector.document_state.token.document_id}"
    )
    outcomes = []
    service.finished.connect(outcomes.append)
    complete(connector, service, key, stamp)
    assert [outcome.status for outcome in outcomes] == ["applied"]
    assert connector.materials.source_result(key.source_id) is None
    assert service.target.surface is connector.get_optic().surfaces[1]


def test_retained_target_follows_inserted_rows_and_rejects_deleted_surface(ready):
    connector, service, key, stamp = ready
    surface = service.target.surface
    connector.add_surface(1)
    assert service.can_apply(key, stamp).code == "stale"
    assert service.refresh_target().allowed
    assert (
        service.target.surface is surface
        and service.target.label == "Medium after surface 2"
    )
    connector.remove_surface(2)
    assert not service.refresh_target().allowed
    assert service.target is None
    assert not service.refresh_target().allowed


def test_queued_backend_change_revokes_submit_before_loading_notification(
    ready, monkeypatch
):
    connector, service, key, stamp = ready

    def queued(request, state):
        if request.target == service.job_target and state == "queued":
            monkeypatch.setattr(
                BackendConfig, "capture", lambda: BackendConfig("torch", "cpu", 32)
            )

    connector.calculation_jobs.state_changed.connect(queued)
    assert service.submit(key, stamp) is None and not service.busy


@pytest.mark.parametrize("terminal", ["unchanged", "failed"])
def test_reentrant_worker_terminal_delivery_emits_one_outcome(ready, terminal):
    connector, service, key, stamp = ready
    if terminal == "unchanged":
        complete(connector, service, key, stamp)
    request = service.submit(key, stamp)
    result = JobResult(
        request,
        "failed" if terminal == "failed" else "succeeded",
        None if terminal == "failed" else prepare_request(request),
        "fixture failure",
        True,
    )
    outcomes = []

    def delivered(outcome):
        outcomes.append(outcome)
        if len(outcomes) == 1:
            connector.calculation_jobs.finished.emit(result)

    service.finished.connect(delivered)
    connector.calculation_jobs.finished.emit(result)
    assert len(outcomes) == 1 and outcomes[0].status == terminal
    assert not service.busy


def test_peer_operation_notifications_refresh_apply_availability(ready):
    connector, service, key, stamp = ready
    decisions = []
    service.state_changed.connect(
        lambda: decisions.append(service.can_apply(key, stamp).allowed)
    )
    connector.history.busy = True
    connector.history.state_changed.emit(True, "Preparing undo")
    connector.history.busy = False
    connector.history.state_changed.emit(False, "Cancelled")
    assert decisions == [False, True]


@pytest.mark.parametrize("direction", ["undo", "redo"])
def test_history_actions_reject_pending_apply_without_touching_document(
    ready, monkeypatch, direction
):
    connector, service, key, stamp = ready
    manager = connector._undo_redo_manager
    manager.add_state(connector._capture_optic_state())
    if direction == "redo":
        manager.move("undo", connector._capture_optic_state())
    expected = manager.peek(direction)
    assert expected is not None
    original, revision, token = (
        connector.get_optic(),
        manager.revision,
        connector.document_state.edit_token,
    )
    request = service.submit(key, stamp)
    assert request is not None and service.busy
    outcomes, submitted = [], []
    connector.history.finished.connect(lambda *outcome: outcomes.append(outcome))
    monkeypatch.setattr(
        connector.calculation_jobs,
        "submit",
        lambda *args, **kwargs: submitted.append(args),
    )

    assert getattr(connector, f"request_{direction}")() is None

    assert outcomes == [("busy", "Wait for material Apply or cancel it first.")]
    assert not submitted and not connector.history.busy
    assert connector.history.request is None
    assert connector.get_optic() is original and manager.revision == revision
    assert manager.peek(direction) is expected
    assert connector.document_state.edit_token == token
    assert service.busy and connector.calculation_jobs.is_current(request)


def test_optimizer_run_rejects_pending_apply_with_actionable_feedback(
    ready, monkeypatch
):
    from optiland.optimization.optimizer.scipy import LeastSquares

    connector, service, key, stamp = ready
    optimizer = connector._optimization_service
    original, revision, token = (
        connector.get_optic(),
        connector._undo_redo_manager.revision,
        connector.document_state.edit_token,
    )
    request = service.submit(key, stamp)
    assert request is not None and service.busy
    failed, callbacks, submitted, states = [], [], [], []
    optimizer.failed.connect(failed.append)
    optimizer.stateChanged.connect(states.append)
    monkeypatch.setattr(
        connector.calculation_jobs,
        "submit",
        lambda *args, **kwargs: submitted.append(args),
    )

    optimizer.run(LeastSquares, {"maxiter": 1}, on_error=callbacks.append)

    assert failed == callbacks == ["Wait for material Apply or cancel it first."]
    assert not submitted and not states and not optimizer.is_running
    assert connector.get_optic() is original
    assert connector._undo_redo_manager.revision == revision
    assert connector.document_state.edit_token == token
    assert service.busy and connector.calculation_jobs.is_current(request)


def test_pending_apply_keeps_target_and_footer_until_explicit_cancel(ready):
    from PySide6.QtCore import QCoreApplication, QEvent

    from optiland_gui.widgets.material_apply_footer import MaterialApplyFooter

    connector, service, key, stamp = ready
    footer = MaterialApplyFooter(service)
    try:
        footer.set_selection(key, stamp)
        target = service.target
        assert footer.apply_button.isEnabled()
        request = service.submit(key, stamp)
        assert request is not None

        check = service.set_target(connector.get_optic().surfaces[2])

        assert not check.allowed and check.code == "busy"
        assert "cancel" in check.message
        assert service.target is target
        assert not footer.apply_button.isEnabled()
        assert not footer.refresh_button.isEnabled()
        assert not footer.cancel_button.isHidden()
        assert footer.cancel_button.isEnabled()
        assert target.label in footer.target_label.text()
        assert footer.reason_label.text() == service.can_apply(key, stamp).message
        footer.cancel_button.click()
        assert not service.busy and service.target is target
        assert footer.apply_button.isEnabled() and footer.refresh_button.isEnabled()
        assert footer.cancel_button.isHidden()
    finally:
        footer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_history_submission_failure_releases_apply_availability(ready, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    from optiland_gui.widgets.material_apply_footer import MaterialApplyFooter

    connector, service, key, stamp = ready
    manager = connector._undo_redo_manager
    manager.add_state(connector._capture_optic_state())
    original, expected, revision, token = (
        connector.get_optic(),
        manager.peek("undo"),
        manager.revision,
        connector.document_state.edit_token,
    )
    outcomes, states = [], []
    connector.history.finished.connect(lambda *outcome: outcomes.append(outcome))
    connector.history.state_changed.connect(lambda *state: states.append(state))
    footer = MaterialApplyFooter(service)
    try:
        footer.set_selection(key, stamp)

        def unavailable_worker(*args, **kwargs):
            assert connector.history.busy
            service.state_changed.emit()
            assert not footer.apply_button.isEnabled()
            raise RuntimeError("Calculation worker is unavailable; retry Undo.")

        monkeypatch.setattr(connector.calculation_jobs, "submit", unavailable_worker)
        assert connector.request_undo() is None

        assert outcomes == [
            ("failed", "Calculation worker is unavailable; retry Undo.")
        ]
        assert states == [(False, "Calculation worker is unavailable; retry Undo.")]
        assert not connector.history.busy and connector.history.request is None
        assert connector.get_optic() is original and manager.peek("undo") is expected
        assert manager.revision == revision
        assert connector.document_state.edit_token == token
        assert footer.apply_button.isEnabled() and service.can_apply(key, stamp).allowed
    finally:
        footer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
