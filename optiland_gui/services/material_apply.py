"""Exact selected-material and retained-surface ownership for asynchronous Apply."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from .document_acceptance import AcceptanceCheck
from .job_records import BackendConfig
from .material_apply_tasks import PreparedMaterialChange, validate_target
from .prepared_optic import PreparedOptic


@dataclass(frozen=True)
class MaterialTarget:
    """An explicit live surface handle with whole-document edit authority."""

    optic: object
    surface: object
    edit_token: object
    structure: tuple[object, ...]
    label: str


@dataclass(frozen=True)
class ApplyOutcome:
    """One terminal result with user-visible spectral qualifications."""

    status: str
    message: str
    warnings: tuple[str, ...] = ()


@dataclass
class _Preparation:
    target: MaterialTarget
    key: object
    stamp: object
    digest: bytes
    backend: BackendConfig
    history_revision: int
    undo: dict
    request: object = None


class MaterialApplyService(QObject):
    """Prepare on the shared worker; accept only the exact still-owned request."""

    state_changed = Signal()
    finished = Signal(object)
    job_target = "material-apply"

    def __init__(self, connector):
        super().__init__(connector)
        self.connector = connector
        self.jobs = connector.calculation_jobs
        self.target: MaterialTarget | None = None
        self.message = "Choose a medium from the Lens Data Editor."
        self._pending: _Preparation | None = None
        self._applying = False
        self._cancelling = False
        self._delivering = False
        self._closed = False
        self.jobs.finished.connect(self._finished)
        self.jobs.stopped.connect(self.close)
        connector.document_state.committed.connect(self._document_changed)
        connector.document_acceptance.model_replaced.connect(self._model_replaced)
        connector.materials.source_changed.connect(self._source_changed)
        connector.materials.source_removed.connect(self._source_changed)
        for signal in (
            connector.history.state_changed,
            connector.placement.state_changed,
            connector.file_operations.state_changed,
            connector._optimization_service.stateChanged,
            connector._optimization_service.completed,
            connector.document_acceptance.state_changed,
        ):
            signal.connect(self._availability_changed)

    def _availability_changed(self, *args):
        self.state_changed.emit()

    @property
    def busy(self):
        return self._pending is not None

    def set_target(self, surface):
        if self._closed:
            return AcceptanceCheck(False, "closed", "Material Apply is closed.")
        if self.busy or self._delivering:
            return AcceptanceCheck(
                False,
                "busy",
                "Wait for Apply or cancel it before choosing another target.",
            )
        if surface is None:
            self.target = None
            check = AcceptanceCheck(
                False, "no-target", "Choose a medium from the Lens Data Editor."
            )
        else:
            optic = self.connector.get_optic()
            surfaces = tuple(optic.surfaces)
            row = next((i for i, value in enumerate(surfaces) if value is surface), -1)
            if row < 0:
                self.target = None
                self.message = (
                    "The selected surface is no longer available. "
                    "Choose another medium."
                )
                self.state_changed.emit()
                return AcceptanceCheck(False, "unavailable", self.message)
            self.target = MaterialTarget(
                optic,
                surface,
                self.connector.document_state.edit_token,
                surfaces,
                "Object-space medium" if row == 0 else f"Medium after surface {row}",
            )
            check = self._target_check(self.target)
        self.message = check.message
        self.state_changed.emit()
        return check

    def refresh_target(self):
        if self.target is None:
            return self.set_target(None)
        return self.set_target(self.target.surface)

    def _target_check(self, target):
        if self._closed:
            return AcceptanceCheck(False, "closed", "Material Apply is closed.")
        if target is None:
            return AcceptanceCheck(
                False, "no-target", "Choose a medium from the Lens Data Editor."
            )
        connector = self.connector
        surfaces = tuple(connector.get_optic().surfaces)
        if (
            target.optic is not connector.get_optic()
            or target.edit_token != connector.document_state.edit_token
            or len(target.structure) != len(surfaces)
            or any(a is not b for a, b in zip(target.structure, surfaces, strict=True))
        ):
            return AcceptanceCheck(
                False,
                "stale",
                "The design changed. Refresh the retained target before Apply.",
            )
        row = next(
            (i for i, value in enumerate(surfaces) if value is target.surface), -1
        )
        try:
            validate_target(connector.get_optic(), row)
        except ValueError as error:
            return AcceptanceCheck(False, "unsupported", str(error))
        return AcceptanceCheck(
            True, "ready", f"Ready to apply to {target.label.lower()}."
        )

    def _source_check(self, key, stamp):
        if key is None or stamp is None:
            return AcceptanceCheck(False, "no-material", "Select a material to apply.")
        source = self.connector.materials.source_result(key.source_id)
        if source is None or source.snapshot.stamps != (stamp,):
            return AcceptanceCheck(
                False,
                "source-stale",
                "The selected source changed. "
                "Inspect its current material before Apply.",
            )
        material = self.connector.materials.material(key)
        if material is None or material.owned_data is None:
            return AcceptanceCheck(
                False,
                "unsupported",
                "This record has no supported owned material definition.",
            )
        return AcceptanceCheck(True, "ready", "Ready")

    def can_apply(self, key, stamp):
        if self.busy or self._delivering:
            return AcceptanceCheck(False, "busy", "Material Apply is being prepared.")
        for check in (
            self._target_check(self.target),
            self.connector.document_acceptance.check_start("material"),
            self._source_check(key, stamp),
        ):
            if not check.allowed:
                return check
        return AcceptanceCheck(True, "ready", f"Apply to {self.target.label.lower()}.")

    def submit(self, key, stamp):
        check = self.can_apply(key, stamp)
        if not check.allowed:
            self.message = check.message
            self.state_changed.emit()
            return None
        pending = None
        try:
            target = self.target
            material = self.connector.materials.material(key)
            payload = material.owned_data
            snapshot = PreparedOptic.capture(target.optic)
            pending = _Preparation(
                target,
                key,
                stamp,
                hashlib.sha256(payload).digest(),
                snapshot.backend,
                self.connector._undo_redo_manager.revision,
                self.connector._capture_optic_state(),
            )
            self._pending = pending
            row = next(
                i for i, value in enumerate(target.structure) if value is target.surface
            )
            request = self.jobs.submit(
                self.job_target,
                "optiland_gui.services.material_apply_tasks:prepare_material_change",
                snapshot,
                {"row": row, "payload": payload, "key": key},
                context=pending,
            )
            pending.request = request
            if self._pending is not pending:
                return None
            if not self._current(pending):
                self.cancel()
                return None
            self.message = "Preparing exact material Apply…"
            self.state_changed.emit()
            return request if self._pending is pending else None
        except Exception as error:
            if pending is None or self._pending is pending:
                self.message = f"Material Apply failed: {error}"
                self._emit_outcome(ApplyOutcome("failed", self.message))
                if self._pending is pending:
                    self._pending = None
                self.state_changed.emit()
            return None

    def _current(self, pending):
        if (
            self._closed
            or pending.backend != BackendConfig.capture()
            or not self._target_check(pending.target).allowed
            or pending.history_revision != self.connector._undo_redo_manager.revision
            or not self._source_check(pending.key, pending.stamp).allowed
        ):
            return False
        record = self.connector.materials.material(pending.key)
        return hashlib.sha256(record.owned_data).digest() == pending.digest

    def cancel(self):
        if self._applying or self._cancelling or self._delivering:
            return
        pending = self._pending
        self._cancelling = True
        try:
            self.jobs.cancel_target(self.job_target)
            if pending is not None:
                self.message = "Material Apply cancelled. The design was preserved."
                self._emit_outcome(ApplyOutcome("cancelled", self.message))
        finally:
            self._cancelling = False
            if self._pending is pending:
                self._pending = None
            self.state_changed.emit()

    def close(self):
        self._closed = True
        self.cancel()
        self.target = None
        self.state_changed.emit()

    def _emit_outcome(self, outcome):
        self._delivering = True
        try:
            self.finished.emit(outcome)
        finally:
            self._delivering = False

    def _source_changed(self, source_id):
        if self._applying:
            return
        if self._pending is not None and self._pending.key.source_id == source_id:
            self.cancel()
        self.state_changed.emit()

    def _document_changed(self, change):
        if self._applying:
            return
        if self._pending is not None and not self._current(self._pending):
            self.cancel()
        if (
            self.target is not None
            and self.target.optic is not self.connector.get_optic()
        ):
            self.target = None
            self.message = (
                "The target document was replaced. "
                "Choose a medium from the Lens Data Editor."
            )
        if self.target is not None:
            self.message = self._target_check(self.target).message
        self.state_changed.emit()

    def _model_replaced(self, replacement):
        if self.target is None or self.target.optic is not replacement.old_optic:
            return
        mapped = next(
            (new for old, new in replacement.mapping if old is self.target.surface),
            None,
        )
        if mapped is None:
            self.target = None
        else:
            self.target = MaterialTarget(
                replacement.new_optic,
                mapped,
                self.connector.document_state.edit_token
                if self._applying
                else self.target.edit_token,
                tuple(replacement.new_optic.surfaces),
                self.target.label,
            )

    def _finished(self, result):
        pending = self._pending
        if (
            pending is None
            or self._cancelling
            or self._delivering
            or self._applying
            or result.request.context is not pending
        ):
            return
        outcome = ApplyOutcome(
            "cancelled", "Material Apply cancelled. The design was preserved."
        )
        try:
            if result.status == "failed":
                outcome = ApplyOutcome(
                    "failed", f"Material Apply failed: {result.error[-1500:]}"
                )
            elif result.status == "succeeded":
                if (
                    not result.current
                    or not self.jobs.is_current(result.request)
                    or not self._current(pending)
                ):
                    outcome = ApplyOutcome(
                        "stale",
                        "The design or selected source changed. Apply was discarded.",
                    )
                else:
                    data = result.data
                    if not isinstance(data, PreparedMaterialChange):
                        raise ValueError("Invalid material preparation result.")
                    if data.outcome == "unchanged":
                        outcome = ApplyOutcome(
                            "unchanged",
                            "The exact owned material is already assigned.",
                            data.warnings,
                        )
                    elif data.outcome == "prepared" and isinstance(
                        data.candidate, PreparedOptic
                    ):
                        candidate = data.candidate.restore()
                        if self._pending is not pending:
                            return
                        if not self.jobs.is_current(
                            result.request
                        ) or not self._current(pending):
                            self.message = (
                                "Prepared result ownership changed before acceptance."
                            )
                            self._emit_outcome(ApplyOutcome("stale", self.message))
                            return
                        self._applying = True
                        self.connector.document_acceptance.publish(
                            candidate,
                            owner="material",
                            expected_optic=pending.target.optic,
                            expected_edit_token=pending.target.edit_token,
                            expected_history_revision=pending.history_revision,
                            validate=lambda: (
                                self._pending is pending
                                and self.jobs.is_current(result.request)
                                and self._current(pending)
                            ),
                            undo=pending.undo,
                            row_mapping=data.row_mapping,
                            affected_rows=data.affected_rows,
                        )
                        outcome = ApplyOutcome(
                            "applied",
                            "Material applied. Undo is available.",
                            data.warnings,
                        )
                    else:
                        raise ValueError("Invalid material preparation outcome.")
            self.message = outcome.message
            self._emit_outcome(outcome)
        except Exception as error:
            if self._pending is not pending:
                return
            self.message = f"Material Apply failed: {error}"
            self._emit_outcome(ApplyOutcome("failed", self.message))
        finally:
            self._applying = False
            if self._pending is pending:
                self._pending = None
            self.state_changed.emit()
