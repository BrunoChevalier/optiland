"""The OSLO service boundary preserves the complete current document on failure."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from optiland_gui.optiland_connector import OptilandConnector
from tests.test_fileio.test_oslo_catalogs import catalog_lens
from tests.test_fileio.test_oslo_glc import catalog, glc_record


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "missing",
        "matched",
        "conflicting",
        "malformed",
        "unsupported",
        "air",
        "direct",
    ],
)
def test_import_mode_matrix_is_transactional(qapp, tmp_path, strict, case):
    connector = OptilandConnector()
    connector.toast_manager = MagicMock()
    service = connector._file_service
    service._current_filepath = "existing.json"
    connector.set_modified(True)
    manager = connector._undo_redo_manager
    manager.add_state({"earlier": 1})
    manager.add_state({"earlier": 2})
    manager.undo({"current": 3})
    before = connector.get_optic()
    undo, redo = deepcopy(manager._undo_stack), deepcopy(manager._redo_stack)
    events = []
    connector.opticLoaded.connect(lambda: events.append("loaded"))
    changes = []
    token = connector.document_state.token
    connector.document_state.committed.connect(changes.append)
    path = catalog_lens(tmp_path, "GLA N-BK7")
    catalogs = []
    if case == "matched":
        catalogs = [catalog(tmp_path, [glc_record(name="N-BK7")])]
    elif case == "missing":
        catalogs = [catalog(tmp_path)]
    elif case == "conflicting":
        catalogs = [
            catalog(tmp_path, [glc_record(name="N-BK7")], name="a.glc"),
            catalog(
                tmp_path,
                [glc_record(name="N-BK7", dispersion="3 3 1.8 0 0")],
                name="b.glc",
            ),
        ]
    elif case in {"malformed", "unsupported"}:
        catalogs = [
            catalog(
                tmp_path,
                [
                    glc_record(
                        name="N-BK7",
                        dispersion="3 3 nan 0 0" if case == "malformed" else "9 0",
                    )
                ],
            )
        ]
    elif case == "air":
        path = catalog_lens(tmp_path, "AIR")
    elif case == "direct":
        path = catalog_lens(tmp_path, "GLA N-BK7 1.6")
    error = connector.import_oslo(
        str(path), strict=strict, glass_catalogs=[str(p) for p in catalogs]
    )
    failure = case in {"malformed", "unsupported", "conflicting"} or (
        strict and case in {"empty", "missing"}
    )
    if failure:
        assert error
        assert connector.get_optic() is before
        assert service.get_current_filepath() == "existing.json"
        assert connector.is_modified()
        assert manager._undo_stack == undo and manager._redo_stack == redo
        assert not events
        assert not changes
        assert connector.document_state.token == token
        assert connector.toast_manager.notify.call_args.args[1] == "error"
    else:
        assert error is None
        assert connector.get_optic() is not before
        assert service.get_current_filepath() is None
        assert connector.is_modified()
        assert not manager.can_undo() and not manager.can_redo()
        assert events == ["loaded"]
        assert len(changes) == 1
        assert changes[0].categories == frozenset({"replacement"})
        assert changes[0].token.document_id != token.document_id
        if case in {"empty", "missing"}:
            assert any(
                call.args[1] == "warning"
                for call in connector.toast_manager.notify.call_args_list
            )


@pytest.mark.parametrize("modified", [False, True])
def test_initialization_failure_preserves_document_and_history(
    qapp, tmp_path, modified
):
    connector = OptilandConnector()
    service = connector._file_service
    service._current_filepath = "saved.json"
    connector.set_modified(modified)
    connector._undo_redo_manager.add_state({"saved": 1})
    original = connector.get_optic()
    with patch.object(
        connector,
        "_initialize_optic_structure",
        side_effect=ValueError("validation failed"),
    ):
        error = connector.import_oslo(
            str(catalog_lens(tmp_path, "GLA 1.6")), strict=True
        )
    assert error == "validation failed"
    assert connector.get_optic() is original
    assert service.get_current_filepath() == "saved.json"
    assert connector.is_modified() == modified
    assert connector._undo_redo_manager._undo_stack == [{"saved": 1}]


def test_connector_forwards_exact_arguments(qapp):
    connector = OptilandConnector()
    with patch.object(
        connector._file_service, "import_oslo", return_value=None
    ) as load:
        assert (
            connector.import_oslo(
                "lens.len", strict=True, glass_catalogs=["B.GLC", "a.glc"]
            )
            is None
        )
    load.assert_called_once_with(
        "lens.len", strict=True, glass_catalogs=["B.GLC", "a.glc"], configuration=1
    )
