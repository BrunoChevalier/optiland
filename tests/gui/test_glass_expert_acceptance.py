"""Whole-candidate worker proof, exact freshness and atomic explicit acceptance."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest

from optiland.materials.portability import capture_material
from optiland.materials.properties import SourceStamp
from optiland.optimization.optimizer.scipy import LeastSquares
from optiland_gui.services import glass_expert_jobs
from optiland_gui.services.glass_expert_proof import native_fingerprint, verify_proof
from optiland_gui.services.job_records import BackendConfig
from optiland_gui.services.prepared_optic import PreparedOptic
from tests.gui.test_calculation_jobs import wait_for
from tests.gui.test_glass_expert_service import complete, document
from tests.gui.test_glass_expert_service import frozen as frozen


def finish_actual(fixture):
    request = fixture.service.prepare_glass_expert(fixture.setup)
    prepared = glass_expert_jobs.prepare_setup(
        request.snapshot, request.parameters, lambda *a, **k: None, threading.Event()
    )
    complete(fixture, request, prepared)
    request = fixture.service.run_glass_expert(prepared)
    result = glass_expert_jobs.run(
        request.snapshot, request.parameters, lambda *a, **k: None, threading.Event()
    )
    complete(fixture, request, result)
    assert result.report.candidate_eligible
    return result, prepared, request


def test_actual_worker_candidate_applies_one_whole_transaction(
    frozen, set_test_backend
):
    before = document(frozen)
    result, prepared, _request = finish_actual(frozen)
    verify_proof(result, prepared)
    assert document(frozen) == before
    service, connector = frozen.service, frozen.connector
    events, mappings = [], []
    connector.document_state.committed.connect(events.append)

    def mapped(value):
        assert connector.get_optic() is value.new_optic
        assert value.old_optic is before[0]
        assert value.mapping == tuple(
            zip(before[0].surfaces, value.new_optic.surfaces, strict=True)
        )
        assert connector.document_acceptance.busy
        mappings.append(value)

    connector.document_acceptance.model_replaced.connect(mapped)
    assert service.glass_expert_acceptance().allowed
    assert service.apply_glass_expert_candidate()
    assert len(events) == len(mappings) == 1
    assert connector._undo_redo_manager.revision == before[4] + 1
    assert connector.is_modified()
    assert native_fingerprint(connector.get_optic()) == result.proof.native_fingerprint
    assert native_fingerprint(before[0]) == result.proof.baseline_fingerprint
    choice = result.report.candidate.materials[0]
    assert (
        capture_material(connector.get_optic().surfaces[1].material_post)
        == frozen.target.pool.resolve(choice.key).owned_data
    )
    assert service.glass_expert_status()[0] == "applied"
    assert service.glass_expert_acceptance().code == "accepted"
    assert not service.apply_glass_expert_candidate()
    assert len(events) == 1


@pytest.mark.parametrize(
    "change",
    [
        "metadata",
        "optical",
        "definition",
        "backend",
        "source",
        "payload",
        "generation",
        "history",
        "order",
    ],
)
def test_completed_candidate_changes_revoke_apply(frozen, monkeypatch, change):
    result, _prepared, request = finish_actual(frozen)
    connector, service = frozen.connector, frozen.service
    assert service.glass_expert_acceptance().allowed
    if change in {"metadata", "optical"}:
        connector.notify_change(change)
    elif change == "definition":
        service._variables[0]["max_val"] = 14.0
    elif change == "backend":
        monkeypatch.setattr(BackendConfig, "capture", lambda: BackendConfig("torch"))
    elif change in {"source", "payload"}:
        source_id = frozen.setup.source_stamps[0].source_id
        source = connector.materials.source_result(source_id)
        if change == "source":
            source = replace(
                source,
                snapshot=replace(
                    source.snapshot, stamps=(SourceStamp(source_id, "new-generation"),)
                ),
            )
        else:
            rows = source.snapshot.materials
            changed = replace(rows[0], owned_data=rows[1].owned_data)
            source = replace(
                source,
                snapshot=replace(source.snapshot, materials=(changed, *rows[1:])),
            )
        connector.materials._sources[source_id] = source
    elif change == "generation":
        frozen.jobs._generations[request.target] += 1
    elif change == "history":
        connector._undo_redo_manager.add_state(connector._capture_optic_state())
    else:
        connector.get_optic().surfaces._surfaces[1:3] = reversed(
            connector.get_optic().surfaces._surfaces[1:3]
        )
    before = document(frozen)
    assert not service.glass_expert_acceptance().allowed
    assert not service.apply_glass_expert_candidate()
    assert document(frozen) == before
    assert service.glass_expert_result() is result


@pytest.mark.parametrize("change", ["cancel", "metadata", "payload", "close"])
def test_restore_callback_cannot_reuse_prior_permission(frozen, monkeypatch, change):
    finish_actual(frozen)
    connector, service = frozen.connector, frozen.service
    restore = PreparedOptic.restore
    before = document(frozen)

    def revoke(snapshot):
        candidate = restore(snapshot)
        if change == "cancel":
            frozen.jobs.cancel_target("optimization")
        elif change == "metadata":
            connector.notify_change("metadata")
        elif change == "close":
            service._close_glass_expert()
        else:
            source_id = frozen.setup.source_stamps[0].source_id
            source = connector.materials.source_result(source_id)
            connector.materials._sources[source_id] = replace(
                source, snapshot=replace(source.snapshot, materials=())
            )
        return candidate

    monkeypatch.setattr(PreparedOptic, "restore", revoke)
    assert not service.apply_glass_expert_candidate()
    assert connector.get_optic() is before[0]
    assert connector._undo_redo_manager.revision == before[4]


def test_restored_native_substitution_rejected_without_optical_work(
    frozen, monkeypatch
):
    finish_actual(frozen)
    before = document(frozen)
    restore = PreparedOptic.restore

    def substitute(snapshot):
        optic = restore(snapshot)
        optic.name = "different candidate"
        return optic

    monkeypatch.setattr(PreparedOptic, "restore", substitute)
    assert not frozen.service.apply_glass_expert_candidate()
    assert "native worker proof" in frozen.service.glass_expert_status()[1]
    assert document(frozen) == before


def test_publication_notifications_cannot_apply_twice_or_start_optimizer(frozen):
    finish_actual(frozen)
    service, connector = frozen.service, frozen.connector
    calls = []

    def attempt(_replacement):
        assert service.is_running
        assert not service.glass_expert_acceptance().allowed
        calls.append(service.apply_glass_expert_candidate())
        service.run(LeastSquares, {})
        assert service._request is None
        with pytest.raises(ValueError, match="finish"):
            service.discard_glass_expert_candidate()
        with pytest.raises(ValueError, match="Wait for optimization"):
            service.capture_glass_expert_setup((frozen.target,))

    connector.document_acceptance.model_replaced.connect(attempt)
    assert service.apply_glass_expert_candidate()
    assert calls == [False]


def test_real_queue_apply_and_asynchronous_undo_redo(frozen, monkeypatch, qapp):
    monkeypatch.setattr(frozen.jobs, "_dispatch", frozen.dispatch)
    service, connector = frozen.service, frozen.connector
    original = native_fingerprint(connector.get_optic())
    service.prepare_glass_expert(frozen.setup)
    wait_for(qapp, lambda: not service.is_running)
    assert service.glass_expert_status()[0] == "ready", service.glass_expert_status()
    service.run_glass_expert(service.glass_expert_prepared())
    wait_for(qapp, lambda: not service.is_running)
    assert service.glass_expert_acceptance().allowed
    result = service.glass_expert_result()
    assert service.apply_glass_expert_candidate()
    final = result.proof.native_fingerprint
    assert native_fingerprint(connector.get_optic()) == final
    assert connector.history.submit("undo")
    wait_for(qapp, lambda: not connector.history.busy)
    assert native_fingerprint(connector.get_optic()) == original
    assert connector.history.submit("redo")
    wait_for(qapp, lambda: not connector.history.busy)
    assert native_fingerprint(connector.get_optic()) == final


@pytest.mark.parametrize("stage", ["mapping", "applied"])
def test_close_during_accepted_notifications_is_never_revived(frozen, stage):
    result, _prepared, _request = finish_actual(frozen)
    service, connector = frozen.service, frozen.connector
    changes = []
    service.glassExpertChanged.connect(
        lambda: changes.append(service.glass_expert_status()[0])
    )
    if stage == "mapping":
        connector.document_acceptance.model_replaced.connect(
            lambda _: service._close_glass_expert()
        )
    else:
        service.stateChanged.connect(
            lambda state: service._close_glass_expert() if state == "applied" else None
        )
    assert service.apply_glass_expert_candidate()
    assert native_fingerprint(connector.get_optic()) == result.proof.native_fingerprint
    assert service.glass_expert_status()[0] == "closed"
    assert changes[-1] == "closed"
    assert changes.count("closed") == 1
    assert not service.apply_glass_expert_candidate()
    assert changes.count("closed") == 1


@pytest.mark.parametrize(
    "change",
    [
        "bytes",
        "backend",
        "report",
        "value",
        "singleton",
        "operand",
        "mapping",
        "warning",
        "proof",
    ],
)
def test_changed_worker_result_cannot_keep_an_old_proof(frozen, change):
    result, _prepared, _request = finish_actual(frozen)
    assert frozen.service.glass_expert_acceptance().allowed
    if change == "bytes":
        result = replace(
            result, candidate=replace(result.candidate, data=frozen.setup.baseline.data)
        )
    elif change == "backend":
        result = replace(
            result, candidate=replace(result.candidate, backend=BackendConfig("torch"))
        )
    elif change == "report":
        result = replace(
            result, report=replace(result.report, improvement_percent=-999.0)
        )
    elif change == "value":
        state = replace(
            result.report.candidate,
            continuous=(replace(result.report.candidate.continuous[0], value=99.0),),
        )
        result = replace(result, report=replace(result.report, candidate=state))
    elif change == "singleton":
        result = replace(
            result,
            report=replace(
                result.report, candidate=replace(result.report.candidate, materials=())
            ),
        )
    elif change == "operand":
        result = replace(result, candidate_operands=())
    elif change == "mapping":
        result = replace(result, row_mapping=((1, 1),))
    elif change == "warning":
        result = replace(result, warnings=("A different review warning",))
    else:
        result = replace(result, proof=None)
    frozen.service._glass_expert.result = result
    before = document(frozen)
    assert frozen.service.glass_expert_acceptance().code == "proof"
    assert not frozen.service.apply_glass_expert_candidate()
    assert document(frozen) == before


def test_apply_contains_no_gui_optical_evaluation(frozen, monkeypatch):
    from optiland.materials import DataMaterial
    from optiland.optic.optic_updater import OpticUpdater
    from optiland.optimization import OptimizationProblem

    finish_actual(frozen)

    def forbidden(*args, **kwargs):
        pytest.fail("Optical evaluation reached the GUI Apply path")

    monkeypatch.setattr(DataMaterial, "n", forbidden)
    monkeypatch.setattr(DataMaterial, "k", forbidden)
    monkeypatch.setattr(OpticUpdater, "update", forbidden)
    monkeypatch.setattr(OptimizationProblem, "sum_squared", forbidden)
    monkeypatch.setattr(OptimizationProblem, "add_variable", forbidden)
    assert frozen.service.glass_expert_acceptance().allowed
    assert frozen.service.apply_glass_expert_candidate()


@pytest.mark.parametrize(
    "busy",
    [
        "history",
        "placement",
        "file",
        "load-conflict",
        "manual",
        "ordinary",
        "close",
        "publication",
    ],
)
def test_apply_obeys_shared_conflict_matrix(frozen, monkeypatch, busy):
    finish_actual(frozen)
    connector, service = frozen.connector, frozen.service
    if busy in {"history", "placement"}:
        monkeypatch.setattr(getattr(connector, busy), "busy", True)
    elif busy == "file":
        connector.file_operations._operations["other"] = object()
    elif busy == "load-conflict":
        connector.file_operations._candidate = object()
    elif busy == "manual":
        candidate = frozen.target.pool.candidates[0]
        assert connector.material_apply.set_target(
            connector.get_optic().surfaces[1]
        ).allowed
        assert (
            connector.material_apply.submit(candidate.key, candidate.stamp) is not None
        )
    elif busy == "ordinary":
        service._request = object()
    elif busy == "close":
        connector.file_operations._closing = True
    else:
        connector.document_acceptance._publishing = True
    before = document(frozen)
    try:
        assert not service.glass_expert_acceptance().allowed
        assert not service.apply_glass_expert_candidate()
        assert document(frozen) == before
    finally:
        connector.file_operations._operations.clear()
        connector.file_operations._candidate = None
        connector.file_operations._closing = False
        connector.document_acceptance._publishing = False
        service._request = None


def test_native_noop_consumes_acceptance_without_history_or_dirty_change(frozen):
    connector, service = frozen.connector, frozen.service
    optic = connector.get_optic()
    optic.updater.set_material(frozen.target.pool.candidates[0].restore(), 1)
    optic.updater.update()
    connector.notify_change("optical")
    service._variables = [
        {"type": "radius", "surface_number": 1, "min_val": 20.0, "max_val": 150.0}
    ]
    frozen.setup = service.capture_glass_expert_setup((frozen.target,))
    result, _prepared, _request = finish_actual(frozen)
    assert result.proof.native_fingerprint == result.proof.baseline_fingerprint
    before = document(frozen)
    was_modified = connector.is_modified()
    assert service.glass_expert_acceptance().allowed
    assert not service.apply_glass_expert_candidate()  # no replacement occurred
    assert service.glass_expert_status()[0] == "unchanged"
    assert document(frozen) == before
    assert connector.is_modified() == was_modified
    assert service.glass_expert_acceptance().code == "accepted"


def test_complete_worse_candidate_remains_explicitly_applyable(frozen):
    connector, service = frozen.connector, frozen.service
    optic = connector.get_optic()
    thickness = float(optic.surfaces[1].thickness)
    service._variables = [
        {
            "type": "thickness",
            "surface_number": 1,
            "min_val": thickness - 0.001,
            "max_val": thickness + 0.001,
        }
    ]
    service._operands = [
        {"type": "f2", "target": float(optic.paraxial.f2()) + 0.001, "weight": 1.0}
    ]
    frozen.setup = service.capture_glass_expert_setup((frozen.target,))
    result, _prepared, _request = finish_actual(frozen)
    assert result.outcome == "no_improvement"
    assert result.report.candidate.merit > result.report.baseline.merit
    assert service.glass_expert_acceptance().allowed
    assert service.apply_glass_expert_candidate()


def test_already_reached_has_no_acceptance_proof_or_history_change(frozen):
    service = frozen.service
    service._operands = [{"type": "f2", "weight": 1.0}]
    frozen.setup = service.capture_glass_expert_setup((frozen.target,))
    request = service.prepare_glass_expert(frozen.setup)
    prepared = glass_expert_jobs.prepare_setup(
        request.snapshot, request.parameters, lambda *a, **k: None, threading.Event()
    )
    complete(frozen, request, prepared)
    request = service.run_glass_expert(prepared)
    result = glass_expert_jobs.run(
        request.snapshot, request.parameters, lambda *a, **k: None, threading.Event()
    )
    complete(frozen, request, result)
    assert result.outcome == "already_reached"
    assert result.candidate is result.proof is None
    before = document(frozen)
    assert not service.glass_expert_acceptance().allowed
    assert not service.apply_glass_expert_candidate()
    assert document(frozen) == before


def test_generation_is_rechecked_inside_atomic_publication(frozen, monkeypatch):
    _result, _prepared, request = finish_actual(frozen)
    connector = frozen.connector
    transaction = connector.change_transaction
    before = document(frozen)

    @contextmanager
    def supersede(**kwargs):
        with transaction(**kwargs):
            frozen.jobs._generations[request.target] += 1
            yield

    monkeypatch.setattr(connector, "change_transaction", supersede)
    assert not frozen.service.apply_glass_expert_candidate()
    assert "ownership changed" in frozen.service.glass_expert_status()[1]
    assert document(frozen) == before


@pytest.mark.parametrize("stage", ["applying", "failed"])
def test_close_before_assignment_stops_all_later_notifications(
    frozen, monkeypatch, stage
):
    finish_actual(frozen)
    service = frozen.service
    before = document(frozen)
    changes, states = [], []
    service.glassExpertChanged.connect(
        lambda: changes.append(service.glass_expert_status()[0])
    )
    service.stateChanged.connect(states.append)
    if stage == "applying":
        service.stateChanged.connect(
            lambda state: service._close_glass_expert() if state == "applying" else None
        )
    else:
        monkeypatch.setattr(
            PreparedOptic,
            "restore",
            lambda _: (_ for _ in ()).throw(ValueError("Restore failed")),
        )
        service.failed.connect(lambda _: service._close_glass_expert())
    assert not service.apply_glass_expert_candidate()
    assert service.glass_expert_status()[0] == "closed"
    assert changes.count("closed") == 1 and changes[-1] == "closed"
    assert states == ["applying"]
    assert document(frozen) == before


def test_rejected_apply_notification_cannot_reenter(frozen):
    service = frozen.service
    calls = []
    service.glassExpertChanged.connect(
        lambda: calls.append(service.apply_glass_expert_candidate())
    )
    assert not service.apply_glass_expert_candidate()
    assert calls == [False]


def test_applying_notification_can_revoke_before_restore(frozen, monkeypatch):
    finish_actual(frozen)
    service, connector = frozen.service, frozen.connector
    before = document(frozen)
    changed = []

    def edit():
        if service.glass_expert_status()[0] == "applying" and not changed:
            changed.append(True)
            connector.notify_change("metadata")

    service.glassExpertChanged.connect(edit)
    monkeypatch.setattr(
        PreparedOptic, "restore", lambda _: pytest.fail("Stale candidate was restored")
    )
    assert not service.apply_glass_expert_candidate()
    assert changed
    assert connector.get_optic() is before[0]
    assert connector._undo_redo_manager.revision == before[4]
