"""Native frozen-pool setup and explicit complete-candidate review workflows."""

from __future__ import annotations

import pickle
import threading
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QLocale, Qt
from PySide6.QtTest import QTest

from optiland_gui.services.glass_expert_pool_jobs import prepare_pool
from optiland_gui.services.job_records import BackendConfig, CalculationCancelled
from optiland_gui.widgets.glass_expert_panel import GlassExpertPanel
from tests.gui import test_glass_expert_service
from tests.gui.test_calculation_jobs import wait_for

frozen = test_glass_expert_service.frozen


def entries(fixture):
    source = fixture.connector.materials.source_result("catalog:a")
    return tuple((r, source.snapshot.stamps[0]) for r in source.snapshot.materials)


@pytest.fixture
def expert(frozen, qapp):
    panel = GlassExpertPanel(frozen.connector, lambda mode: entries(frozen))
    panel.resize(680, 760)
    panel.show()
    qapp.processEvents()
    yield panel
    panel.close_service()
    panel.close()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pool_parameters(fixture):
    return {
        "entries": entries(fixture),
        "wavelengths": (0.55,),
        "backend": BackendConfig.capture(),
    }


def finish_pool(panel, fixture):
    request = fixture.jobs._pending[-1]
    result = prepare_pool(
        None, request.parameters, lambda *args: None, threading.Event()
    )
    test_glass_expert_service.complete(fixture, request, result)
    return result


def test_explicit_pool_creation_retains_exact_entries_and_exclusions(frozen, expert):
    before = test_glass_expert_service.document(frozen)
    expert.add_target()
    data = entries(frozen)
    expert.entries = lambda mode: (
        data[0],
        (replace(data[1][0], owned_data=None), data[1][1]),
    )
    expert.set_pool("pins")
    assert expert.pools.busy and not expert.prepare_button.isEnabled()
    result = finish_pool(expert, frozen)
    assert len(result.candidates) == len(result.exclusions) == 1
    assert expert.target_table.item(0, 1).text() == "1 (fixed)"
    assert expert.pool_table.rowCount() == 2
    assert "Excluded" in expert.pool_table.item(1, 3).text()
    exact = expert._targets[1]
    expert.entries = lambda mode: ()
    expert.refresh()
    assert expert._targets[1] is exact
    assert test_glass_expert_service.document(frozen) == before


def test_pool_cancellation_and_removed_target_do_not_install_late_result(
    frozen, expert
):
    expert.add_target()
    expert.set_pool("selection")
    request = frozen.jobs._pending[-1]
    data = prepare_pool(None, request.parameters, lambda *args: None, threading.Event())
    expert.remove_target()
    test_glass_expert_service.complete(frozen, request, data)
    assert not expert._targets
    expert.add_target()
    expert.set_pool("selection")
    request = frozen.jobs._pending[-1]
    expert.stop()
    test_glass_expert_service.complete(frozen, request, data)
    assert expert._targets[1] is None


def test_source_change_revokes_pool_result(frozen, expert):
    expert.add_target()
    expert.set_pool("selection")
    request = frozen.jobs._pending[-1]
    data = prepare_pool(None, request.parameters, lambda *args: None, threading.Event())
    frozen.connector.materials.remove_source("catalog:a")
    test_glass_expert_service.complete(frozen, request, data)
    assert expert._targets[1] is None
    assert "source changed" in expert.notice.text()


def test_locale_scientific_settings_and_full_seed_range(expert):
    locale = QLocale(QLocale.Language.German)
    for name in ("tol", "nd_scale", "vd_scale"):
        control = expert.settings_controls[name]
        control.setLocale(locale)
        control.validator().setLocale(locale)
    expert.settings_controls["tol"].setText("1,5e-20")
    expert.settings_controls["nd_scale"].setText("1,2e30")
    expert.settings_controls["vd_scale"].setText("0,125")
    expert.settings_controls["seed"].setValue(2**32 - 1)
    settings = expert._settings()
    assert settings.seed == 2**32 - 1
    assert settings.tol == 1.5e-20 and settings.nd_scale == 1.2e30
    assert settings.vd_scale == 0.125
    expert.settings_controls["tol"].setText("1e-")
    with pytest.raises(ValueError, match="numeric"):
        expert._settings()
    expert.settings_controls["tol"].setText("0")
    with pytest.raises(ValueError, match="positive"):
        expert._settings()


def test_missing_pool_and_invalid_draft_never_prepare(frozen, expert):
    expert.set_pool("selection")
    assert "target first" in expert.notice.text()
    expert.add_target()
    expert.prepare_current()
    assert "eligible pool" in expert.notice.text()
    expert.set_pool("results")
    finish_pool(expert, frozen)
    expert.settings_controls["tol"].setText("invalid")
    expert.prepare_current()
    assert "numeric" in expert.notice.text()
    assert not frozen.service.is_running


def test_real_widget_pool_prepare_run_review_apply_and_async_history(
    frozen, expert, qapp, monkeypatch
):
    monkeypatch.setattr(frozen.jobs, "_dispatch", frozen.dispatch)
    original = frozen.connector.get_optic()
    before = pickle.dumps(original.to_dict())
    QTest.mouseClick(expert.add_target_button, Qt.MouseButton.LeftButton)
    expert.pool_mode.setCurrentIndex(2)
    QTest.mouseClick(expert.pool_button, Qt.MouseButton.LeftButton)
    wait_for(qapp, lambda: not expert.pools.busy, timeout=40)
    assert expert._targets[1] is not None, expert.notice.text()
    expert.settings_controls["maxiter"].setValue(5)
    expert.settings_controls["num_neighbours"].setValue(1)
    QTest.mouseClick(expert.prepare_button, Qt.MouseButton.LeftButton)
    wait_for(qapp, lambda: not frozen.service.is_running, timeout=40)
    assert expert.run_button.isEnabled(), expert.status.text()
    assert not expert.notice.text()
    assert "baseline 50" in expert.problem_summary.toPlainText()
    QTest.mouseClick(expert.run_button, Qt.MouseButton.LeftButton)
    wait_for(qapp, lambda: not frozen.service.is_running, timeout=40)
    assert frozen.service.glass_expert_result() is not None, expert.status.text()
    assert expert.apply_button.isEnabled(), expert.freshness.text()
    assert expert.changes_table.rowCount() == 2
    assert expert.operands_table.rowCount() == 1
    assert expert.trials_table.rowCount() > 0
    assert expert.pages.currentIndex() == 1
    assert frozen.connector.get_optic() is original
    assert pickle.dumps(original.to_dict()) == before
    revision = frozen.connector._undo_redo_manager.revision
    QTest.mouseClick(expert.apply_button, Qt.MouseButton.LeftButton)
    assert frozen.connector._undo_redo_manager.revision == revision + 1
    assert frozen.connector.get_optic().surfaces[1].thickness == pytest.approx(
        8, abs=1e-4
    )
    assert not expert.apply_button.isEnabled()
    frozen.connector.undo()
    wait_for(qapp, lambda: not frozen.connector.history.busy, timeout=40)
    assert frozen.connector.get_optic().surfaces[1].thickness == pytest.approx(5)
    frozen.connector.redo()
    wait_for(qapp, lambda: not frozen.connector.history.busy, timeout=40)
    assert frozen.connector.get_optic().surfaces[1].thickness == pytest.approx(
        8, abs=1e-4
    )


def test_pool_worker_cancellation_and_identity_admission(frozen):
    parameters = pool_parameters(frozen)
    cancel = threading.Event()
    with pytest.raises(CalculationCancelled):
        prepare_pool(None, parameters, lambda *args: cancel.set(), cancel)
    with pytest.raises(ValueError, match="distinct"):
        prepare_pool(
            None,
            {**parameters, "entries": (parameters["entries"][0],) * 2},
            lambda *args: None,
            threading.Event(),
        )
    with pytest.raises(ValueError, match="between"):
        prepare_pool(
            None, {**parameters, "entries": ()}, lambda *args: None, threading.Event()
        )


def test_native_export_load_and_detached_replay(
    frozen, expert, qapp, monkeypatch, tmp_path
):
    from PySide6.QtWidgets import QFileDialog

    from tests.gui.test_glass_expert_acceptance import finish_actual

    before = test_glass_expert_service.document(frozen)
    result, prepared, _ = finish_actual(frozen)
    monkeypatch.setattr(frozen.jobs, "_dispatch", frozen.dispatch)
    path = tmp_path / "frozen-run.json"
    outputs = []

    def choose(artifact, extension, name):
        outputs.append((extension, name))
        assert frozen.connector.file_operations.request_artifact(path, artifact)

    monkeypatch.setattr(expert.export_dialog, "choose", choose)
    QTest.mouseClick(expert.export_button, Qt.MouseButton.LeftButton)
    wait_for(
        qapp,
        lambda: path.exists() and not frozen.connector.file_operations.busy,
        timeout=40,
    )
    assert outputs == [("json", "glass-expert-setup")]
    assert test_glass_expert_service.document(frozen) == before
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    expert.choose_setup()
    expert._load_dialog.selectFile(str(path))
    expert._load_dialog.accept()
    wait_for(
        qapp,
        lambda: not expert.files.busy and not frozen.service.is_running,
        timeout=40,
    )
    loaded = frozen.service.glass_expert_prepared()
    assert loaded.setup.settings == prepared.setup.settings
    assert loaded.setup.targets == prepared.setup.targets
    assert loaded.setup.run_id != prepared.setup.run_id
    assert "detached" in expert.notice.text().lower()
    assert expert.run_button.isEnabled()
    assert not expert.apply_button.isEnabled()
    QTest.mouseClick(expert.run_button, Qt.MouseButton.LeftButton)
    wait_for(qapp, lambda: not frozen.service.is_running, timeout=40)
    replayed = frozen.service.glass_expert_result()
    assert replayed.report.candidate.merit == pytest.approx(
        result.report.candidate.merit, abs=1e-10
    )
    assert not expert.apply_button.isEnabled()
    assert frozen.service.glass_expert_acceptance().code == "detached"
    assert "separate inspection" in expert.freshness.text().lower()
    assert test_glass_expert_service.document(frozen) == before
    expert.settings_controls["tol"].setText("0.005")
    assert not expert.run_button.isEnabled()
    assert "controls changed" in expert.freshness.text().lower()


def test_native_save_and_load_chooser_cancel_reopen_and_close(
    frozen, expert, monkeypatch, tmp_path
):
    from PySide6.QtWidgets import QFileDialog

    from optiland_gui.services.glass_expert_file_jobs import ManifestExport
    from tests.gui.test_glass_expert_acceptance import finish_actual

    finish_actual(frozen)
    monkeypatch.setattr(QFileDialog, "open", lambda self: None)
    expert._exported(ManifestExport("export", "script", b"# script", None, ()))
    dialog = expert.export_dialog._dialog
    assert dialog.defaultSuffix() == "py"
    assert not expert.export_button.isEnabled()
    expert.export_run()
    assert expert.export_dialog._dialog is dialog
    dialog.reject()
    assert expert.export_button.isEnabled()
    expert._exported(ManifestExport("export", "candidate", b"{}", None, ()))
    assert expert.export_dialog._dialog is not dialog
    expert.export_dialog._dialog.reject()
    expert.choose_setup()
    dialog = expert._load_dialog
    assert dialog.fileMode() == QFileDialog.FileMode.ExistingFile
    assert not expert.load_button.isEnabled()
    expert.choose_setup()
    assert expert._load_dialog is dialog
    dialog.reject()
    assert expert.load_button.isEnabled()
    expert.choose_setup()
    expert._load_dialog.selectFile(str(tmp_path / "setup.json"))
    expert.close_service()
    assert expert._load_dialog is None
    assert expert.export_dialog._dialog is None
    assert not expert.isEnabled()
    expert._exported(ManifestExport("late", "manifest", b"{}", None, ()))
    assert expert.export_dialog._dialog is None


def test_complete_portable_candidate_opens_separately_without_editing_live_design(
    frozen, expert, monkeypatch, qapp
):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QWidget

    from optiland_gui.services.glass_expert_file_jobs import open_candidate
    from tests.gui.test_glass_expert_acceptance import finish_actual

    before = test_glass_expert_service.document(frozen)
    result, _, _ = finish_actual(frozen)
    publications = []

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.connector = SimpleNamespace(
                _file_service=SimpleNamespace(
                    _publish_candidate=lambda *args, **kwargs: publications.append(
                        (args, kwargs)
                    )
                )
            )

    monkeypatch.setattr("optiland_gui.main_window.MainWindow", Window)
    QTest.mouseClick(expert.open_button, Qt.MouseButton.LeftButton)
    assert expert.files.busy and not expert.apply_button.isEnabled()
    request = frozen.jobs._pending[-1]
    data = open_candidate(
        request.snapshot, request.parameters, lambda *a: None, threading.Event()
    )
    test_glass_expert_service.complete(frozen, request, data)
    assert len(publications) == len(expert._candidate_windows) == 1
    (candidate, path), options = publications[0]
    assert candidate is not frozen.connector.get_optic() and path is None
    assert options == {"modified": True, "validated": True}
    assert candidate.surfaces[1].thickness == pytest.approx(8, abs=1e-4)
    assert test_glass_expert_service.document(frozen) == before
    expert._candidate_windows[0].close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not expert._candidate_windows
    expert.open_candidate()
    request = frozen.jobs._pending[-1]
    frozen.service.discard_glass_expert_candidate()
    test_glass_expert_service.complete(frozen, request, data)
    assert not expert._candidate_windows and len(publications) == 1
    assert result.native_candidate is not None


@pytest.mark.parametrize(
    "kind", ["invalid entry", "wrong type", "generation", "budget"]
)
def test_pool_worker_rejects_inconsistent_or_excessive_input(frozen, monkeypatch, kind):
    from optiland.materials.properties import SourceStamp

    parameters = pool_parameters(frozen)
    if kind == "invalid entry":
        parameters["entries"] = ((),)
    elif kind == "wrong type":
        parameters["entries"] = (("not material", "not stamp"),)
    elif kind == "generation":
        first, second = parameters["entries"]
        parameters["entries"] = (
            first,
            (second[0], SourceStamp(second[1].source_id, "new")),
        )
    else:
        monkeypatch.setattr(
            "optiland_gui.services.glass_expert_pool_jobs.MAX_RUN_BYTES", 1
        )
    with pytest.raises(ValueError):
        prepare_pool(None, parameters, lambda *a: None, threading.Event())


@pytest.mark.parametrize(
    "status", ["cancelled", "failed", "bad result", "backend", "queue closed"]
)
def test_pool_service_errors_release_controls_without_installing_pool(
    frozen, expert, monkeypatch, status
):
    expert.add_target()
    if status == "queue closed":

        def reject(*a, **kw):
            raise ValueError("Queue is closing")

        monkeypatch.setattr(frozen.jobs, "submit", reject)
        expert.set_pool("results")
    else:
        expert.set_pool("results")
        request = frozen.jobs._pending[-1]
        frozen.jobs.progress.emit(request, {"completed": 1, "total": 2})
        assert "1 / 2" in expert.notice.text()
        data = finish_pool_data = prepare_pool(
            None, request.parameters, lambda *a: None, threading.Event()
        )
        if status == "bad result":
            data = None
        if status == "backend":
            monkeypatch.setattr(
                BackendConfig, "capture", lambda: BackendConfig("torch")
            )
        test_glass_expert_service.complete(
            frozen,
            request,
            data,
            status=status if status in {"cancelled", "failed"} else "succeeded",
            error="Cannot evaluate selected materials",
        )
        assert finish_pool_data.candidates
    assert not expert.pools.busy
    assert expert._targets[1] is None
    assert expert.prepare_button.isEnabled()
    assert expert.notice.text()


def test_pool_source_status_and_empty_selection_are_explicit(frozen, expert):
    expert.set_pool()
    assert "target first" in expert.notice.text()
    expert.add_target()
    expert.add_target()
    assert len(expert._targets) == 1
    expert.entries = lambda mode: ()
    expert.set_pool("selection")
    assert "No current entries" in expert.notice.text()
    expert.entries = lambda mode: entries(frozen)
    expert.set_pool("results")
    finish_pool(expert, frozen)
    pool = expert._targets[1]
    frozen.connector.materials.remove_source("catalog:a")
    assert expert._targets[1] is pool
    assert "Missing source" in expert.target_table.item(0, 2).text()
    assert expert.pool_table.rowCount() == 2


def test_cancel_new_run_clears_candidate_review_and_settings_require_prepare(
    frozen, expert
):
    from tests.gui.test_glass_expert_acceptance import finish_actual

    _, prepared, _ = finish_actual(frozen)
    expert.settings_controls["seed"].setValue(87)
    expert.run_prepared()
    assert "controls changed" in expert.notice.text()
    assert not frozen.service.is_running
    request = frozen.service.run_glass_expert(prepared)
    assert expert.changes_table.rowCount() == 0
    assert not expert.apply_button.isEnabled()
    assert not expert.prepare_button.isEnabled()
    expert.stop()
    test_glass_expert_service.complete(frozen, request, status="cancelled")
    assert frozen.service.glass_expert_result() is None
    assert "No completed candidate" in expert.result_summary.text()
    assert not expert.apply_button.isEnabled()


def test_late_run_activation_preserves_the_already_running_operation(frozen, expert):
    from tests.gui.test_glass_expert_acceptance import finish_actual

    _, prepared, _ = finish_actual(frozen)
    expert._prepared_draft = expert._draft
    request = frozen.service.run_glass_expert(prepared)
    before = test_glass_expert_service.document(frozen)
    expert.run_prepared()
    assert "Wait for optimization" in expert.notice.text()
    assert frozen.service.is_running
    assert list(frozen.jobs._pending) == [request]
    assert not expert.run_button.isEnabled()
    assert test_glass_expert_service.document(frozen) == before


def test_review_failure_has_no_percentage_or_candidate_changes(frozen, expert):
    from tests.gui.test_glass_expert_acceptance import finish_actual

    result, prepared, _ = finish_actual(frozen)
    failed = replace(
        result,
        report=replace(
            result.report,
            candidate=None,
            improved=False,
            improvement_percent=None,
            outcome="failed",
        ),
        candidate=None,
        candidate_operands=(),
        proof=None,
        warnings=("Final objective could not be evaluated.",),
    )
    expert._show_result(failed, prepared)
    assert "not defined" in expert.result_summary.text()
    assert "eligible candidate: no" in expert.result_summary.text()
    assert expert.changes_table.rowCount() == 0
    assert expert.operands_table.item(0, 6).text() == "No candidate"
    assert expert.warnings.toPlainText() == failed.warnings[0]


@pytest.mark.parametrize("reason", ["backend", "window failure"])
def test_open_candidate_unavailable_environment_preserves_live_design(
    frozen, expert, monkeypatch, reason
):
    from optiland_gui.services.glass_expert_file_jobs import open_candidate
    from tests.gui.test_glass_expert_acceptance import finish_actual

    before = test_glass_expert_service.document(frozen)
    finish_actual(frozen)
    expert.open_candidate()
    request = frozen.jobs._pending[-1]
    data = open_candidate(
        request.snapshot, request.parameters, lambda *a: None, threading.Event()
    )
    if reason == "backend":
        monkeypatch.setattr(BackendConfig, "capture", lambda: BackendConfig("torch"))
    else:

        def cannot_open():
            raise RuntimeError("Window initialization failed")

        monkeypatch.setattr("optiland_gui.main_window.MainWindow", cannot_open)
    test_glass_expert_service.complete(frozen, request, data)
    assert not expert._candidate_windows
    assert test_glass_expert_service.document(frozen) == before
    if reason == "backend":
        assert expert.files.state == "cancelled"
    else:
        assert "Could not open" in expert.notice.text()


def test_pool_close_from_own_notification_never_revives_owner(frozen, expert):
    expert.pools.changed.connect(expert.pools.close)
    request = expert.pools.build(entries(frozen), (0.55,))
    assert request is None
    assert expert.pools._closed and not expert.pools.busy
    assert not any(r.target == expert.pools.target for r in frozen.jobs._pending)
    with pytest.raises(ValueError, match="closed"):
        expert.pools.build(entries(frozen), (0.55,))


@pytest.mark.parametrize("action", ["close", "new pool"])
def test_pool_cancellation_callback_cannot_submit_superseded_owner(
    frozen, expert, monkeypatch, action
):
    original = frozen.jobs.cancel_target
    replacements = []

    def cancel(target):
        monkeypatch.setattr(frozen.jobs, "cancel_target", original)
        original(target)
        if action == "close":
            expert.pools.close()
        else:
            replacements.append(expert.pools.build(entries(frozen)[:1], (0.55,)))

    monkeypatch.setattr(frozen.jobs, "cancel_target", cancel)
    assert expert.pools.build(entries(frozen), (0.55,)) is None
    pending = [r for r in frozen.jobs._pending if r.target == expert.pools.target]
    if action == "close":
        assert not pending and not expert.pools.busy
    else:
        assert pending == replacements and expert.pools.busy
        assert len(pending[0].parameters["entries"]) == 1


def test_review_labels_distinguish_axes_indices_and_frozen_operand_inputs(expert):
    from optiland_gui.services.glass_expert_records import FrozenDefinition

    descriptions = []
    for index, qualifiers in enumerate(
        ({"axis": "x"}, {"axis": "y"}, {"coeff_index": 3})
    ):
        record = FrozenDefinition.from_dict(
            f"variable:{index}",
            "variable",
            {"type": "radius", "surface_number": 1, **qualifiers},
        )
        descriptions.append(expert._description(record))
    assert len(set(descriptions)) == 3
    assert 'axis="x"' in descriptions[0] and 'axis="y"' in descriptions[1]
    assert "coeff_index=3" in descriptions[2]
    for index, wavelength in enumerate((0.4861, 0.6563)):
        record = FrozenDefinition.from_dict(
            f"operand:{index}",
            "operand",
            {
                "type": "real_y_intercept",
                "input_data": {"surface_number": 3, "wavelength": wavelength},
            },
        )
        text = expert._description(record)
        assert record.identifier in text and str(wavelength) in text
        assert '"surface_number": 3' in text
    large = FrozenDefinition.from_dict(
        "operand:large",
        "operand",
        {"type": "real_y_intercept", **{f"input_{n}": "x" * 1000 for n in range(8)}},
    )
    text = expert._description(large)
    assert len(text) <= 2048 and "complete inputs in saved setup" in text


def test_failed_preflight_keeps_error_instead_of_preparing_notice(frozen, expert):
    expert.add_target()
    expert.set_pool("results")
    finish_pool(expert, frozen)
    expert.prepare_current()
    request = frozen.jobs._pending[-1]
    test_glass_expert_service.complete(
        frozen, request, status="failed", error="A frozen operand cannot be evaluated"
    )
    assert "cannot be evaluated" in expert.notice.text()
    assert not expert._preparing_notice


def test_pool_progress_is_rate_limited_and_native_validation_can_cancel(
    frozen, monkeypatch
):
    parameters = pool_parameters(frozen)
    record, stamp = parameters["entries"][0]
    third = (replace(record, key=replace(record.key, record_id="third")), stamp)
    parameters["entries"] += (third,)
    monkeypatch.setattr(
        "optiland_gui.services.glass_expert_pool_jobs.time.monotonic", lambda: 1.0
    )
    progress = []
    result = prepare_pool(
        None, parameters, lambda *args: progress.append(args), threading.Event()
    )
    assert len(result.candidates) == 3
    assert [item[1] for item in progress] == [1, 3]

    class CancelDuringMaterialValidation:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls >= 3

    with pytest.raises(CalculationCancelled):
        prepare_pool(
            None,
            parameters,
            lambda *a: pytest.fail("No completed candidate"),
            CancelDuringMaterialValidation(),
        )


def test_close_during_pool_source_validation_suppresses_late_publication(
    frozen, expert, monkeypatch
):
    expert.add_target()
    expert.set_pool("results")
    request = frozen.jobs._pending[-1]
    data = prepare_pool(None, request.parameters, lambda *a: None, threading.Event())
    original = frozen.connector.materials.source_result

    def closing_source(source):
        expert.pools.close()
        return original(source)

    monkeypatch.setattr(frozen.connector.materials, "source_result", closing_source)
    test_glass_expert_service.complete(frozen, request, data)
    assert expert._targets[1] is None
    assert not expert.pools.busy


def test_unavailable_portable_export_explains_dependency_without_chooser(expert):
    from optiland_gui.services.glass_expert_file_jobs import ManifestExport

    expert._exported(
        ManifestExport(
            "export",
            "script",
            None,
            "A custom coating callback has no portable serializer.",
            (),
        )
    )
    assert "custom coating callback" in expert.notice.text()
    assert expert.export_dialog._dialog is None


def test_refreshed_source_marks_frozen_pool_changed_without_replacing_it(
    frozen, expert
):
    from optiland.materials.sources import load_source
    from tests.gui.test_material_jobs import owned_source

    expert.add_target()
    expert.set_pool("results")
    finish_pool(expert, frozen)
    original = expert._targets[1]
    source = owned_source()
    source = replace(source, entries=source.entries[:1])
    request = frozen.connector.materials.load_source(source)
    test_glass_expert_service.complete(frozen, request, load_source(source))
    assert expert._targets[1] is original
    assert "Source changed; recreate pool" in expert.target_table.item(0, 2).text()
    assert expert.pool_table.rowCount() == 2
