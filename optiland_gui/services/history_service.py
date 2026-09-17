"""Guarded asynchronous Undo/Redo; history advances only with model acceptance."""

from __future__ import annotations

import pickle

from PySide6.QtCore import QObject, Signal, Slot

from optiland_gui.services.job_records import OpticSnapshot
from optiland_gui.services.prepared_optic import PreparedOptic


class HistoryService(QObject):
    """Keep the current design usable while a detached history model is prepared."""

    state_changed = Signal(bool, str)
    finished = Signal(str, str)
    target = "document-history"

    def __init__(self, connector):
        super().__init__(connector)
        self.connector = connector
        self.jobs = connector.calculation_jobs
        self.request = None
        self.busy = False
        self.jobs.finished.connect(self._finished)

    def submit(self, direction):
        check = self.connector.document_acceptance.check_start("history")
        if not check.allowed:
            self.finished.emit("busy", check.message)
            return None
        manager = self.connector._undo_redo_manager
        state = manager.peek(direction)
        if state is None:
            return None
        try:
            current = OpticSnapshot.capture(self.connector.get_optic())
            snapshot = OpticSnapshot(pickle.dumps(state, protocol=5), current.backend)
            context = (
                self.connector.document_state.edit_token,
                manager.revision,
                state,
                direction,
                pickle.loads(current.data),
                self.connector.get_optic(),
            )
            self.busy = True
            self.request = self.jobs.submit(
                self.target,
                "optiland_gui.services.history_tasks:prepare_history",
                snapshot,
                {},
                context=context,
            )
        except Exception as exc:
            self.busy = False
            self.finished.emit("failed", str(exc))
            self.state_changed.emit(self.busy, str(exc))
            return None
        self.busy = True
        self.state_changed.emit(True, f"Preparing {direction}…")
        return self.request

    @Slot()
    def cancel(self):
        self.jobs.cancel_target(self.target)

    @Slot(object)
    def _finished(self, result):
        if self.request is None or result.request.job_id != self.request.job_id:
            return
        self.request = None
        status, message = result.status, result.error
        if status == "succeeded":
            token, revision, expected, direction, current, optic = (
                result.request.context
            )
            manager = self.connector._undo_redo_manager
            if (
                not result.current
                or not self.jobs.is_current(result.request)
                or token != self.connector.document_state.edit_token
                or revision != manager.revision
                or manager.peek(direction) is not expected
            ):
                status = "stale"
                message = "History was not applied because the design changed."
            else:
                try:
                    if not isinstance(result.data, PreparedOptic):
                        raise ValueError("Invalid prepared history result.")
                    candidate = result.data.restore()
                    # No signal dispatch or event processing occurs between the
                    # ownership check, stack move and owned model assignment.
                    self.connector.document_acceptance.publish(
                        candidate,
                        owner="history",
                        expected_optic=optic,
                        expected_edit_token=token,
                        expected_history_revision=revision,
                        validate=lambda: (
                            self.jobs.is_current(result.request)
                            and token == self.connector.document_state.edit_token
                        ),
                        history_move=(direction, expected, current),
                    )
                    message = f"{direction.title()} applied"
                except Exception as exc:
                    status, message = "failed", str(exc)
        elif status == "cancelled":
            message = "History preparation cancelled"
        self.busy = False
        self.state_changed.emit(False, message)
        self.finished.emit(status, message)
