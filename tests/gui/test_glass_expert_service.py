"""Frozen run ownership and review policy through the shared optimization owner."""

from __future__ import annotations

import pickle
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from optiland.optimization.glass_expert_records import (
    GlassExpertReport,
    GlassExpertState,
)
from optiland.optimization.material_candidates import MaterialCandidate, MaterialPool
from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.services.glass_expert_records import (
    GlassExpertJobResult,
    GlassExpertProgress,
    GlassExpertSettings,
    MaterialTarget,
    PreparedGlassExpertSetup,
)
from optiland_gui.services.job_records import BackendConfig
from tests.gui.test_calculation_jobs import wait_for
from tests.gui.test_material_comparison_service import computed
from tests.gui.test_material_jobs import owned_source
from tests.gui.test_optimization_jobs import definitions


@pytest.fixture
def frozen(qapp, minimal_optic, monkeypatch):
    connector = OptilandConnector()
    connector.load_optic_from_object(minimal_optic)
    jobs = connector.calculation_jobs
    dispatch = jobs._dispatch
    monkeypatch.setattr(jobs, "_dispatch", lambda: None)
    request = connector.materials.load_source(owned_source())
    source = computed(request)
    jobs._pending.remove(request)
    jobs._terminal(request, "succeeded", source)
    stamp = source.snapshot.stamps[0]
    pool = MaterialPool(
        tuple(
            MaterialCandidate(
                item.key,
                stamp,
                item.owned_data,
                item.nd.value,
                item.vd.value,
                item.display_name,
                item.conditions,
            )
            for item in source.snapshot.materials
        )
    )
    service = connector._optimization_service
    service._variables, service._operands = definitions()
    target = MaterialTarget("material:1", 1, pool)
    setup = service.capture_glass_expert_setup(
        (target,), GlassExpertSettings(maxiter=5, num_neighbours=1)
    )
    yield SimpleNamespace(
        connector=connector,
        jobs=jobs,
        service=service,
        setup=setup,
        target=target,
        dispatch=dispatch,
    )
    connector._backend_timer.stop()
    jobs.shutdown()
    wait_for(qapp, lambda: jobs._process is None)
    connector.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def complete(fixture, request, data=None, status="succeeded", error=""):
    if request in fixture.jobs._pending:
        fixture.jobs._pending.remove(request)
    fixture.jobs._terminal(request, status, data, error)


def prepare(fixture):
    request = fixture.service.prepare_glass_expert(fixture.setup)
    prepared = PreparedGlassExpertSetup(
        fixture.setup,
        fixture.setup.variables,
        fixture.setup.operands,
        9.0,
        (),
        None,
        "fixture",
        (),
        (0.55,),
    )
    complete(fixture, request, prepared)
    return prepared


def candidate(fixture):
    # The coordinator does not infer acceptance from solver success. This typed
    # report is intentionally independent from worker numerical validation.
    baseline = GlassExpertState((), (), 9.0, False)
    final = GlassExpertState((), (), 1.0, True)
    report = GlassExpertReport(
        "completed",
        baseline,
        final,
        True,
        88.0,
        (),
        2,
        4,
        0,
        (),
    )
    return GlassExpertJobResult(
        fixture.setup.run_id,
        fixture.setup.input_digest,
        report,
        fixture.setup.baseline,
        None,
        tuple((i, i) for i in range(4)),
        (),
        (),
        (),
        "fixture",
        0.1,
    )


def document(fixture):
    connector = fixture.connector
    return (
        connector.get_optic(),
        pickle.dumps(connector.get_optic().to_dict()),
        connector.document_state.token,
        connector.document_state.edit_token,
        connector._undo_redo_manager.revision,
        pickle.dumps((fixture.service._variables, fixture.service._operands)),
    )


def test_capture_and_completion_preserve_entire_live_problem(frozen):
    service = frozen.service
    before = document(frozen)
    assert [row.identifier for row in frozen.setup.variables] == [
        "variable:0",
        "material:1",
    ]
    assert "identifier" not in frozen.setup.variables[-1].as_dict()
    prepared = prepare(frozen)
    assert service.glass_expert_prepared() is prepared
    assert service.glass_expert_freshness().allowed
    request = service.run_glass_expert(prepared)
    assert request.progress_policy == "detached"
    assert not request.cancel_on_document_change
    assert service.is_running
    result = candidate(frozen)
    complete(frozen, request, result)
    assert not service.is_running
    assert service.glass_expert_result() is result
    assert service.glass_expert_status()[0] == "completed"
    assert document(frozen) == before


def test_existing_optimization_panel_shares_busy_state_without_typed_preview(frozen):
    from optiland_gui.optimization_panel import OptimizationPanel

    panel = OptimizationPanel(frozen.connector)
    panel._refresh_variables_table()
    panel.chkLiveVars.setChecked(True)
    try:
        original = panel.tblVariables.item(0, 2).text()
        prepared = prepare(frozen)
        assert panel.btnRun.isEnabled() and not panel.btnStop.isEnabled()
        request = frozen.service.run_glass_expert(prepared)
        assert not panel.btnRun.isEnabled() and panel.btnStop.isEnabled()
        progress = GlassExpertProgress(
            frozen.setup.run_id,
            frozen.setup.input_digest,
            "global",
            1,
            2,
            9.0,
            2.0,
            0.1,
            continuous=(("variable:0", 999.0),),
        )
        frozen.jobs.progress.emit(request, {"details": {"glass_expert": progress}})
        assert panel.tblVariables.horizontalHeaderItem(2).text() == "Current Value"
        assert panel.tblVariables.item(0, 2).text() == original
        assert panel._preview is None
        complete(frozen, request, candidate(frozen))
        assert panel.btnRun.isEnabled() and not panel.btnStop.isEnabled()
    finally:
        panel.close()
        panel.deleteLater()


@pytest.mark.parametrize(
    "change,code",
    [
        ("metadata", "stale"),
        ("optical", "stale"),
        ("replacement", "stale"),
        ("definition", "definitions"),
        ("source", "source"),
        ("backend", "backend"),
    ],
)
def test_frozen_result_stays_inspectable_after_inputs_change(
    frozen, monkeypatch, change, code
):
    service = frozen.service
    request = service.run_glass_expert(prepare(frozen))
    complete(frozen, request, candidate(frozen))
    result = service.glass_expert_result()
    if change in {"metadata", "optical", "replacement"}:
        frozen.connector.document_state.record(change)
    elif change == "definition":
        service.get_operands()[0]["target"] = 999.0
    elif change == "source":
        frozen.connector.materials.remove_source(
            frozen.setup.source_stamps[0].source_id
        )
    else:
        monkeypatch.setattr(
            BackendConfig, "capture", classmethod(lambda cls: cls("changed"))
        )
    assert service.glass_expert_result() is result
    assert service.glass_expert_freshness().code == code


def test_progress_uses_exact_frozen_run_even_when_document_changes(frozen):
    service = frozen.service
    request = service.run_glass_expert(prepare(frozen))
    frozen.connector.document_state.change()
    progress = GlassExpertProgress(
        frozen.setup.run_id, frozen.setup.input_digest, "global", 1, 2, 9.0, 2.0, 0.1
    )
    observed = []
    service.glassExpertProgress.connect(observed.append)
    frozen.jobs.progress.emit(
        request, {"stage": "global", "details": {"glass_expert": progress}}
    )
    assert observed[-1] is progress
    assert not frozen.jobs.is_current(request)
    for wrong in (
        replace(progress, run_id="other"),
        replace(progress, input_digest="other"),
        None,
    ):
        frozen.jobs.progress.emit(
            request, {"stage": "global", "details": {"glass_expert": wrong}}
        )
    assert len(observed) == 1
    service.stop()
    frozen.jobs.progress.emit(
        request, {"stage": "global", "details": {"glass_expert": progress}}
    )
    assert len(observed) == 1
    assert service.glass_expert_status()[0] == "cancelled"


def test_queued_stop_cannot_resurrect_completed_owner(frozen):
    service = frozen.service
    frozen.jobs.state_changed.connect(
        lambda request, state: service.stop() if state == "queued" else None
    )
    assert service.prepare_glass_expert(frozen.setup) is None
    assert not service.is_running
    assert service.glass_expert_status()[0] == "cancelled"
    assert service.glass_expert_prepared() is None


def test_queued_stop_does_not_cancel_setup_started_by_terminal_listener(frozen):
    service = frozen.service
    first = service.prepare_glass_expert(frozen.setup)
    replacements = []

    def replace_cancelled(state):
        if state == "cancelled" and not replacements:
            replacements.append(service.prepare_glass_expert(frozen.setup))

    service.stateChanged.connect(replace_cancelled)
    service.stop()
    service.stateChanged.disconnect(replace_cancelled)
    assert len(replacements) == 1
    assert replacements[0] in frozen.jobs._pending
    assert first not in frozen.jobs._pending
    assert service.is_running
    assert service.glass_expert_status()[0] == "queued"


def test_running_stop_is_nonrecursive_and_rejects_replacement_until_terminal(
    frozen, qapp, monkeypatch
):
    service = frozen.service
    monkeypatch.setattr(frozen.jobs, "_dispatch", frozen.dispatch)
    monkeypatch.setattr(
        frozen.jobs,
        "_command",
        [
            sys.executable,
            "-u",
            str(Path(__file__).with_name("calculation_worker_fixture.py")),
        ],
    )
    rejected = []

    def state_changed(state):
        if state == "running":
            service.stop()
        elif state == "stopping" and not rejected:
            service.stop()
            try:
                service.prepare_glass_expert(frozen.setup)
            except ValueError as error:
                rejected.append(str(error))

    service.stateChanged.connect(state_changed)
    service.prepare_glass_expert(frozen.setup)
    wait_for(qapp, lambda: service.glass_expert_status()[0] == "cancelled")
    assert len(rejected) == 1 and "Wait for optimization" in rejected[0]
    assert not service.is_running
    assert not frozen.jobs._pending


def test_old_preflight_cannot_publish_into_replacement(frozen):
    service = frozen.service
    old = service.prepare_glass_expert(frozen.setup)
    newer = service.prepare_glass_expert(frozen.setup)
    prepared = PreparedGlassExpertSetup(
        frozen.setup, (), (), 9.0, (), None, None, (), (0.55,)
    )
    complete(frozen, old, prepared)
    assert service.is_running and service.glass_expert_prepared() is None
    complete(frozen, newer, prepared)
    assert service.glass_expert_freshness().allowed
    assert service.glass_expert_prepared() is prepared


def test_ready_callback_can_start_run_without_old_submission_overwriting_it(frozen):
    service = frozen.service
    requests = []

    def ready():
        if service.glass_expert_status()[0] == "ready":
            requests.append(service.run_glass_expert(service.glass_expert_prepared()))

    service.glassExpertChanged.connect(ready)
    prepare(frozen)
    assert len(requests) == 1 and requests[0] is not None
    assert service.is_running
    complete(frozen, requests[0], candidate(frozen))
    assert service.glass_expert_status()[0] == "completed"


@pytest.mark.parametrize("kind", ["prepare", "run"])
def test_wrong_worker_identity_fails_without_mutation(frozen, kind):
    service = frozen.service
    if kind == "prepare":
        request = service.prepare_glass_expert(frozen.setup)
        bad = PreparedGlassExpertSetup(
            replace(frozen.setup, run_id="wrong"),
            (),
            (),
            1,
            (),
            None,
            None,
            (),
            (0.55,),
        )
    else:
        request = service.run_glass_expert(prepare(frozen))
        bad = replace(candidate(frozen), input_digest="wrong")
    before = document(frozen)
    complete(frozen, request, bad)
    assert service.glass_expert_status()[0] == "failed"
    assert service.glass_expert_result() is None
    assert document(frozen) == before


def test_uncaptured_setup_is_inspection_only(frozen):
    service = frozen.service
    setup = replace(frozen.setup, run_id="loaded-setup")
    service.prepare_glass_expert(setup)
    assert service.glass_expert_freshness().code == "detached"
    service.stop()


def test_setup_admission_reports_invalid_or_unavailable_inputs(frozen, monkeypatch):
    service = frozen.service
    before = document(frozen)
    with pytest.raises(ValueError, match="frozen GlassExpert setup"):
        service.prepare_glass_expert({})
    monkeypatch.setattr(frozen.connector.file_operations, "_closing", True)
    with pytest.raises(ValueError, match="closing"):
        service.prepare_glass_expert(frozen.setup)
    monkeypatch.setattr(frozen.connector.file_operations, "_closing", False)
    assert document(frozen) == before
    frozen.connector.materials.remove_source(frozen.setup.source_stamps[0].source_id)
    with pytest.raises(ValueError, match="source changed"):
        service.capture_glass_expert_setup((frozen.target,))
    monkeypatch.setattr(frozen.connector, "_optic", None)
    with pytest.raises(ValueError, match="Open an optical system"):
        service.capture_glass_expert_setup((frozen.target,))


def test_full_queue_failure_releases_optimization_owner(frozen, monkeypatch):
    before = document(frozen)
    monkeypatch.setattr(frozen.jobs, "max_pending", 0)
    assert frozen.service.prepare_glass_expert(frozen.setup) is None
    state, message = frozen.service.glass_expert_status()
    assert state == "failed" and "queue is full" in message
    assert not frozen.service.is_running
    assert document(frozen) == before
    monkeypatch.setattr(frozen.jobs, "max_pending", 16)
    assert frozen.service.prepare_glass_expert(frozen.setup) is not None


@pytest.mark.parametrize("operation", ["add", "remove", "set", "clear"])
@pytest.mark.parametrize("section", ["variable", "operand"])
def test_definition_editor_actions_mark_frozen_setup_stale(frozen, operation, section):
    service = frozen.service
    prepared = prepare(frozen)
    notifications = []
    service.glassExpertChanged.connect(lambda: notifications.append(True))
    row = (
        {"surface_number": 2, "type": "radius"}
        if section == "variable"
        else {"type": "total_track", "target": 42.0}
    )
    method = getattr(
        service, f"{operation}_{section}{'s' if operation == 'clear' else ''}"
    )
    if operation == "set":
        method(0, row)
    elif operation == "add":
        method(row)
    elif operation == "remove":
        method(0)
    else:
        method()
    assert notifications == [True]
    assert service.glass_expert_prepared() is prepared
    assert service.glass_expert_freshness().code == "definitions"


def test_reject_unprepared_and_busy_runs_without_live_changes(frozen):
    service = frozen.service
    with pytest.raises(ValueError, match="Prepare this exact"):
        service.run_glass_expert(None)
    prepared = prepare(frozen)
    with pytest.raises(ValueError, match="exact frozen"):
        service.run_glass_expert(replace(prepared))
    request = service.run_glass_expert(prepared)
    with pytest.raises(ValueError, match="Wait for optimization"):
        service.capture_glass_expert_setup((frozen.target,))
    with pytest.raises(ValueError, match="finish"):
        service.discard_glass_expert_candidate()
    complete(frozen, request, status="failed", error="numerical failure")
    assert service.glass_expert_status() == ("failed", "numerical failure")
    service.discard_glass_expert_candidate()
    assert service.glass_expert_prepared() is None
    assert service.glass_expert_result() is None


def test_closed_service_revokes_results_and_prevents_new_work(frozen):
    service = frozen.service
    prepare(frozen)
    service._close_glass_expert()
    assert service.glass_expert_status()[0] == "closed"
    assert service.glass_expert_prepared() is None
    assert service.glass_expert_freshness().code == "closed"
    with pytest.raises(ValueError, match="closed"):
        service.prepare_glass_expert(frozen.setup)


def test_real_worker_preflight_and_run_retain_complete_candidate_without_applying(
    frozen, qapp, monkeypatch
):
    service = frozen.service
    before = document(frozen)
    monkeypatch.setattr(frozen.jobs, "_dispatch", frozen.dispatch)
    service.prepare_glass_expert(frozen.setup)
    wait_for(qapp, lambda: not service.is_running, timeout=60)
    prepared = service.glass_expert_prepared()
    assert prepared is not None, service.glass_expert_status()
    assert prepared.baseline_merit == pytest.approx(9.0)
    assert prepared.portable_baseline is not None
    assert document(frozen) == before
    messages = []
    service.glassExpertProgress.connect(messages.append)
    service.run_glass_expert(prepared)
    wait_for(qapp, lambda: not service.is_running, timeout=60)
    result = service.glass_expert_result()
    assert result is not None, service.glass_expert_status()
    assert result.report.candidate_eligible
    assert result.report.candidate.merit < 1e-7
    candidate_optic = result.candidate.restore()
    assert candidate_optic.surfaces[1].thickness == pytest.approx(8.0, abs=1e-4)
    choice = result.report.candidate.materials[0]
    assert choice.key in {c.key for c in frozen.target.pool.candidates}
    assert result.native_candidate is not None
    assert result.row_mapping == tuple((i, i) for i in range(4))
    assert document(frozen) == before
    assert service.glass_expert_freshness().allowed
    assert any(isinstance(message, GlassExpertProgress) for message in messages)
    frozen.connector.document_state.record("metadata")
    assert service.glass_expert_result() is result
    assert not service.glass_expert_freshness().allowed
