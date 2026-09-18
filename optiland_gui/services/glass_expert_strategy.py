"""Explicit-review strategy owned by the GUI's existing OptimizationService.

This helper owns no QObject slots or executor. Its parent service routes shared
queue events here, retaining complete frozen results without publishing them.
"""

from __future__ import annotations

import json
import pickle
import uuid
from dataclasses import dataclass, field

from .document_acceptance import AcceptanceCheck
from .glass_expert_proof import native_fingerprint, verify_proof
from .glass_expert_records import (
    FrozenDefinition,
    GlassExpertJobResult,
    GlassExpertProgress,
    GlassExpertSettings,
    GlassExpertSetup,
    PreparedGlassExpertSetup,
    canonical_json,
)
from .job_records import BackendConfig
from .prepared_optic import PreparedOptic


@dataclass
class _Owner:
    setup: GlassExpertSetup
    optic: object = None
    surfaces: tuple = ()
    optical_token: object = None
    edit_token: object = None
    history_revision: int = -1
    definitions: bytes = b""
    undo: dict | None = None
    request: object = None
    checked_sources: dict = field(default_factory=dict)
    prepared: object = None


@dataclass
class _Stage:
    owner: _Owner
    kind: str
    request: object = None

    @property
    def handler(self):
        name = "prepare_setup" if self.kind == "prepare" else "run"
        return f"optiland_gui.services.glass_expert_jobs:{name}"


class GlassExpertStrategy:
    """Keep detached execution ownership separate from document acceptance."""

    def __init__(self, service):
        self.service = service
        self.connector = service._connector
        self.jobs = self.connector.calculation_jobs
        self._captured = None
        self._owner = None
        self._pending = None
        self._delivering = False
        self._closed = False
        self._stopping = False
        self._cancelled = False
        self._applying = False
        self._accepted = False
        self._verified_result = None
        self.prepared = None
        self.result = None
        self.message = "Configure material pools and prepare the frozen problem."
        self.state = "empty"

    @property
    def busy(self):
        return self._pending is not None or self._delivering or self._applying

    def _definitions(self):
        return pickle.dumps(
            (self.service._variables, self.service._operands), protocol=5
        )

    def capture(self, targets, settings=None):
        """Capture a current-design setup without evaluating optics on Qt."""
        self._admit()
        connector = self.connector
        optic = connector.get_optic()
        if optic is None:
            raise ValueError("Open an optical system before preparing GlassExpert.")
        targets = tuple(targets)
        owner = _Owner(None)
        owner.optic = optic
        owner.surfaces = tuple(optic.surfaces)
        owner.optical_token = connector.document_state.token
        owner.edit_token = connector.document_state.edit_token
        owner.history_revision = connector._undo_redo_manager.revision
        owner.definitions = self._definitions()
        variables, operands = pickle.loads(owner.definitions)
        frozen_variables = tuple(
            FrozenDefinition.from_dict(f"variable:{i}", "variable", value)
            for i, value in enumerate(variables)
        ) + tuple(
            FrozenDefinition.from_dict(
                target.variable_id,
                "variable",
                {"type": "material", "surface_number": target.surface_number},
            )
            for target in targets
        )
        setup = GlassExpertSetup(
            uuid.uuid4().hex,
            PreparedOptic.capture(optic),
            frozen_variables,
            tuple(
                FrozenDefinition.from_dict(f"operand:{i}", "operand", value)
                for i, value in enumerate(operands)
            ),
            targets,
            GlassExpertSettings() if settings is None else settings,
            canonical_json(self.service.OPERAND_METADATA),
        )
        owner.setup = setup
        owner.undo = connector._capture_optic_state()
        check = self._freshness(owner)
        if not check.allowed:
            raise ValueError(check.message)
        self._captured = owner
        return setup

    def _admit(self):
        if self._closed:
            raise ValueError("GlassExpert is closed.")
        if (
            self._delivering
            or self._applying
            or self._stopping
            or self.service._request is not None
            or (self._pending is not None and self._pending.kind == "run")
        ):
            raise ValueError(
                "Wait for optimization or stop it before changing the run."
            )
        check = self.connector.document_acceptance.check_start("optimization")
        if not check.allowed:
            raise ValueError(check.message)

    def prepare(self, setup):
        """Preflight frozen inputs; only locally captured setups carry authority."""
        self._admit()
        if not isinstance(setup, GlassExpertSetup):
            raise ValueError("Expected a frozen GlassExpert setup.")
        owner = (
            self._captured
            if self._captured is not None and self._captured.setup is setup
            else self._owner
            if self._owner is not None and self._owner.setup is setup
            else _Owner(setup)
        )
        self._captured = None
        self._owner = owner
        self.prepared = self.result = None
        self._accepted = False
        self._verified_result = None
        self._cancelled = False
        return self._submit(owner, "prepare", setup)

    def run(self, prepared):
        self._admit()
        if (
            self._pending is not None
            or prepared is None
            or prepared is not self.prepared
            or self._owner is None
        ):
            raise ValueError(
                "Prepare this exact frozen setup before running GlassExpert."
            )
        self.result = None
        self._owner.prepared = prepared
        self._accepted = False
        self._verified_result = None
        self._cancelled = False
        return self._submit(self._owner, "run", prepared)

    def _submit(self, owner, kind, value):
        pending = _Stage(owner, kind)
        self._pending = pending
        self._stopping = False
        self.state = "preparing" if kind == "prepare" else "queued"
        self.message = (
            "Preparing frozen GlassExpert inputs…"
            if kind == "prepare"
            else "GlassExpert queued. The design changes only after explicit Apply."
        )
        try:
            request = self.jobs.submit(
                "optimization",
                pending.handler,
                owner.setup.baseline,
                {"setup": value},
                cancel_on_document_change=False,
                progress_policy="detached",
                context=pending,
            )
            if self._pending is not pending:
                return None
            pending.request = request
            owner.request = request if kind == "run" else None
            self.service.glassExpertChanged.emit()
            return request if self._pending is pending else None
        except Exception as error:
            if self._pending is pending:
                self._pending = None
                self.state, self.message = "failed", str(error)[:2048]
                self.service.failed.emit(self.message)
                self.service.glassExpertChanged.emit()
            return None

    def _owns(self, request):
        pending = self._pending
        if (
            pending is None
            or request.context is not pending
            or request.target != "optimization"
            or request.handler != pending.handler
        ):
            return False
        if pending.request is None:
            pending.request = request
            if pending.kind == "run":
                pending.owner.request = request
        return pending.request is request

    def on_state(self, request, state):
        if not self._owns(request):
            return False
        self.state = "stopping" if self._stopping else state
        self.service.stateChanged.emit(self.state)
        self.service.glassExpertChanged.emit()
        return True

    def on_progress(self, request, message):
        if not self._owns(request):
            return False
        owner = self._pending.owner
        progress = (message.get("details") or {}).get("glass_expert")
        if (
            self._stopping
            or not isinstance(progress, GlassExpertProgress)
            or progress.run_id != owner.setup.run_id
            or progress.input_digest != owner.setup.input_digest
            or owner.setup.baseline.backend != BackendConfig.capture()
        ):
            return True
        self.service.glassExpertProgress.emit(progress)
        return True

    def on_finished(self, result):
        if not self._owns(result.request):
            return False
        pending = self._pending
        self._delivering = True
        try:
            self._pending = None
            self._cancelled = result.status == "cancelled" or self._stopping
            self._stopping = False
            data = result.data
            if self._cancelled:
                self.state = "cancelled"
                self.message = "GlassExpert cancelled. The design was not changed."
                self.prepared = None if pending.kind == "prepare" else self.prepared
            elif result.status != "succeeded":
                self.state, self.message = "failed", str(result.error)[:2048]
                self.service.failed.emit(self.message)
            elif pending.kind == "prepare":
                if not isinstance(data, PreparedGlassExpertSetup) or (
                    data.setup.run_id != pending.owner.setup.run_id
                    or data.setup.input_digest != pending.owner.setup.input_digest
                ):
                    raise ValueError(
                        "Prepared GlassExpert input identity does not match."
                    )
                self.prepared = data
                self.state = "ready"
                self.message = "Frozen problem prepared. Review the inputs before Run."
            else:
                if not isinstance(data, GlassExpertJobResult) or (
                    data.run_id != pending.owner.setup.run_id
                    or data.input_digest != pending.owner.setup.input_digest
                ):
                    raise ValueError(
                        "GlassExpert result identity does not match its run."
                    )
                self.result = data
                self.state = data.outcome
                self.message = (
                    "GlassExpert finished; review the complete candidate before Apply."
                    if data.report.candidate_eligible
                    else "GlassExpert finished without an eligible complete candidate."
                )
        except Exception as error:
            self.state, self.message = "failed", str(error)[:2048]
            self.service.failed.emit(self.message)
        finally:
            self._delivering = False
        self.service.stateChanged.emit(self.state)
        self.service.glassExpertChanged.emit()
        return True

    def stop(self):
        pending = self._pending
        if pending is None or self._stopping:
            return
        self._stopping = True
        self.state, self.message = "stopping", "Stopping GlassExpert…"
        self.jobs.cancel_target("optimization")
        # Cancelling a queued stage can finish synchronously and a terminal
        # listener may start another setup. Do not notify/cancel that new owner.
        if self._pending is pending:
            self.service.stateChanged.emit(self.state)
            if self._pending is pending:
                self.service.glassExpertChanged.emit()

    def discard(self):
        if self.busy:
            raise ValueError("Wait for the current GlassExpert operation to finish.")
        self._owner = self._captured = self.prepared = self.result = None
        self._verified_result = None
        self.state, self.message = "empty", "GlassExpert setup and result discarded."
        self.service.glassExpertChanged.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._captured = self._owner = self.prepared = self.result = None
        self._verified_result = None
        self.state, self.message = "closed", "GlassExpert is closed."
        self.service.glassExpertChanged.emit()

    def _freshness(self, owner):
        if self._closed:
            return AcceptanceCheck(False, "closed", "GlassExpert is closed.")
        if owner is None or owner.optic is None:
            return AcceptanceCheck(
                False, "detached", "This frozen setup is for separate inspection only."
            )
        connector = self.connector
        if owner.setup.baseline.backend != BackendConfig.capture():
            return AcceptanceCheck(False, "backend", "The calculation backend changed.")
        definitions = self._definitions()
        current_optic = connector.get_optic()
        surfaces = () if current_optic is None else tuple(current_optic.surfaces)
        if (
            current_optic is not owner.optic
            or len(surfaces) != len(owner.surfaces)
            or any(a is not b for a, b in zip(surfaces, owner.surfaces, strict=True))
            or connector.document_state.token != owner.optical_token
            or connector.document_state.edit_token != owner.edit_token
            or connector._undo_redo_manager.revision != owner.history_revision
        ):
            return AcceptanceCheck(False, "stale", "The design or history changed.")
        if definitions != owner.definitions:
            return AcceptanceCheck(
                False, "definitions", "Optimization definitions changed."
            )
        for stamp in owner.setup.source_stamps:
            source = connector.materials.source_result(stamp.source_id)
            if source is None or source.snapshot.stamps != (stamp,):
                return AcceptanceCheck(
                    False, "source", "A frozen material source changed."
                )
            if owner.checked_sources.get(stamp.source_id) is source:
                continue
            # SourceResult and its records are immutable. Cache only by that
            # exact object; a same-stamp reload must prove its payloads again.
            records = {record.key: record for record in source.snapshot.materials}
            try:
                for target in owner.setup.targets:
                    for candidate in target.pool.candidates:
                        if candidate.key.source_id != stamp.source_id:
                            continue
                        record = records.get(candidate.key)
                        if (
                            record is None
                            or record.owned_data is None
                            or (
                                canonical_json(json.loads(record.owned_data))
                                != candidate.owned_data
                            )
                        ):
                            raise ValueError("Exact source payload changed")
            except (TypeError, ValueError, RecursionError):
                return AcceptanceCheck(
                    False, "source", "A frozen exact material payload changed."
                )
            owner.checked_sources[stamp.source_id] = source
        return AcceptanceCheck(True, "current", "Frozen inputs still match the design.")

    def freshness(self):
        """Presentation diagnostic, never an instruction to publish a candidate."""
        return self._freshness(self._owner)

    def _acceptance(self, owner, result, *, applying=False, verify=False):
        if self._closed:
            return AcceptanceCheck(False, "closed", "GlassExpert is closed.")
        if self._accepted:
            return AcceptanceCheck(
                False, "accepted", "This candidate was already accepted."
            )
        if (
            self._pending is not None
            or self._delivering
            or self._stopping
            or (self._applying and not applying)
        ):
            return AcceptanceCheck(
                False, "busy", "Wait for the current GlassExpert operation."
            )
        if (
            owner is None
            or owner is not self._owner
            or result is None
            or result is not self.result
            or self.prepared is None
            or self.prepared is not owner.prepared
            or self._cancelled
        ):
            return AcceptanceCheck(
                False,
                "no-candidate",
                "Run GlassExpert and review a complete candidate first.",
            )
        check = self._freshness(owner)
        if not check.allowed:
            return check
        if owner.request is None or not self.jobs.is_current(owner.request):
            return AcceptanceCheck(
                False,
                "generation",
                "A newer optimization or cancellation superseded this result.",
            )
        if not applying:
            check = self.connector.document_acceptance.check_start("glass-expert")
            if not check.allowed:
                return check
        if (
            verify
            or self._verified_result is None
            or (
                self._verified_result[0] is not result
                or self._verified_result[1] is not self.prepared
            )
        ):
            try:
                verify_proof(result, self.prepared)
                expected_mapping = tuple((i, i) for i in range(len(owner.surfaces)))
                if result.row_mapping != expected_mapping:
                    raise ValueError(
                        "Candidate surface mapping is incomplete or changed order."
                    )
                if native_fingerprint(owner.undo) != result.proof.baseline_fingerprint:
                    raise ValueError(
                        "Candidate proof differs from the captured native baseline."
                    )
            except (TypeError, ValueError, AttributeError, KeyError) as error:
                return AcceptanceCheck(False, "proof", str(error)[:2048])
            self._verified_result = (result, self.prepared)
        return AcceptanceCheck(
            True, "ready", "Apply the reviewed whole candidate to this design."
        )

    def acceptance(self):
        return self._acceptance(self._owner, self.result)

    def apply(self):
        if self._applying or self._delivering or self._closed:
            return False
        owner, result = self._owner, self.result
        check = self._acceptance(owner, result, verify=True)
        if not check.allowed:
            self.message = check.message
            self._delivering = True
            try:
                self.service.glassExpertChanged.emit()
            finally:
                self._delivering = False
            return False
        self._applying = True
        self.state, self.message = "applying", "Applying the reviewed whole candidate…"
        accepted = False
        try:
            self.service.stateChanged.emit(self.state)
            if self._closed:
                return False
            self.service.glassExpertChanged.emit()
            if not self._acceptance(owner, result, applying=True).allowed:
                raise ValueError("Candidate ownership changed before restoration.")
            candidate = result.candidate.restore()
            if native_fingerprint(candidate) != result.proof.native_fingerprint:
                raise ValueError(
                    "Restored candidate differs from its native worker proof."
                )
            check = self._acceptance(owner, result, applying=True, verify=True)
            if not check.allowed:
                raise ValueError(check.message)
            # This admission includes operations started by restore callbacks;
            # the strategy's own short Apply flag is explicitly exempted.
            self.connector.document_acceptance.require_start("glass-expert")
            if result.proof.native_fingerprint == result.proof.baseline_fingerprint:
                self._accepted = True
                self.state, self.message = (
                    "unchanged",
                    "The exact native prescription is already assigned.",
                )
            else:
                self.connector.document_acceptance.publish(
                    candidate,
                    owner="glass-expert",
                    expected_optic=owner.optic,
                    expected_edit_token=owner.edit_token,
                    expected_history_revision=owner.history_revision,
                    validate=lambda: (
                        self._acceptance(owner, result, applying=True).allowed
                    ),
                    undo=owner.undo,
                    row_mapping=result.row_mapping,
                    affected_rows=tuple(range(len(owner.surfaces))),
                )
                accepted = True
                if (
                    self._closed
                    or self._owner is not owner
                    or self.result is not result
                ):
                    return accepted
                self._accepted = True
                self.state, self.message = (
                    "applied",
                    "The complete GlassExpert candidate was applied.",
                )
            self.service.stateChanged.emit(self.state)
        except Exception as error:
            if not self._closed:
                self.state, self.message = "acceptance-failed", str(error)[:2048]
                self.service.failed.emit(self.message)
                if not self._closed:
                    self.service.stateChanged.emit(self.state)
        finally:
            self._applying = False
            if not self._closed:
                self.service.glassExpertChanged.emit()
        return accepted
