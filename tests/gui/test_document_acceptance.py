"""Prepared replacement publication checks ownership before any history/model write."""

from __future__ import annotations

import pytest

from optiland_gui.services.prepared_optic import PreparedOptic


def arguments(connector):
    return dict(
        owner="material",
        expected_optic=connector.get_optic(),
        expected_edit_token=connector.document_state.edit_token,
        expected_history_revision=connector._undo_redo_manager.revision,
        undo=connector._capture_optic_state(),
        validate=lambda: True,
    )


@pytest.mark.parametrize(
    "failure", ["predicate", "optic", "token", "history", "range", "duplicate"]
)
def test_refused_acceptance_preserves_document_and_history(
    highlighting_connector, failure
):
    connector = highlighting_connector
    original = connector.get_optic()
    candidate = PreparedOptic.capture(original).restore()
    kwargs = arguments(connector)
    revision = connector._undo_redo_manager.revision
    if failure == "predicate":

        def refuse():
            assert connector.document_acceptance.busy
            return False

        kwargs["validate"] = refuse
    elif failure == "optic":
        kwargs["expected_optic"] = candidate
    elif failure == "token":
        kwargs["expected_edit_token"] = None
    elif failure == "history":
        kwargs["expected_history_revision"] = -1
    elif failure == "range":
        kwargs["row_mapping"] = ((-1, 0),)
    else:
        kwargs["row_mapping"] = ((1, 1), (1, 2))
    with pytest.raises(ValueError):
        connector.document_acceptance.publish(candidate, **kwargs)
    assert connector.get_optic() is original
    assert connector._undo_redo_manager.revision == revision
    assert not connector.document_acceptance.busy


def test_availability_observers_see_committed_model_once(highlighting_connector):
    connector = highlighting_connector
    candidate = PreparedOptic.capture(connector.get_optic()).restore()
    undo, redo = [], []
    connector.undoStackAvailabilityChanged.connect(
        lambda available: undo.append((available, connector.get_optic()))
    )
    connector.redoStackAvailabilityChanged.connect(
        lambda available: redo.append((available, connector.get_optic()))
    )
    connector.document_acceptance.publish(candidate, **arguments(connector))
    assert undo == [(True, candidate)]
    assert redo == [(False, candidate)]


@pytest.mark.parametrize("closing,pending", [(True, False), (False, True)])
def test_pending_open_or_close_blocks_material_acceptance(
    highlighting_connector, monkeypatch, closing, pending
):
    from types import SimpleNamespace

    connector = highlighting_connector
    monkeypatch.setattr(
        connector,
        "file_operations",
        SimpleNamespace(busy=False, closing=closing, has_pending_load=pending),
    )
    assert not connector.document_acceptance.check_start("material").allowed
    if closing:
        with pytest.raises(ValueError, match="closing"):
            connector.document_acceptance.require_start("file")


def test_nested_acceptance_is_rejected_before_any_second_assignment(
    highlighting_connector,
):
    connector = highlighting_connector
    candidate = PreparedOptic.capture(connector.get_optic()).restore()
    kwargs = arguments(connector)

    def validate():
        with pytest.raises(ValueError, match="being applied"):
            connector.document_acceptance.publish(candidate, **kwargs)
        return True

    connector.document_acceptance.publish(
        candidate, **(kwargs | {"validate": validate})
    )
    assert connector.get_optic() is candidate


def test_silent_history_does_not_dispatch_reentrant_logging(
    highlighting_connector, monkeypatch
):
    import optiland_gui.undo_redo_manager as module

    connector = highlighting_connector
    candidate = PreparedOptic.capture(connector.get_optic()).restore()
    records = []
    monkeypatch.setattr(module.logger, "debug", lambda *args: records.append(args))
    connector.document_acceptance.publish(candidate, **arguments(connector))
    assert records == []
    manager = connector._undo_redo_manager
    state = connector._capture_optic_state()
    manager.move("undo", state, notify=False)
    assert records == []
    manager.add_state(state)
    assert len(records) == 1  # ordinary history keeps its existing diagnostics
