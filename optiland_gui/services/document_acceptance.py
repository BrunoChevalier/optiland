"""Short, shared publication boundary for prepared GUI document replacements."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class AcceptanceCheck:
    """A bounded readiness decision for controls and actual submission."""

    allowed: bool
    code: str
    message: str


@dataclass(frozen=True)
class SurfaceReplacement:
    """Proven retained identities exposed after assignment, before UI rebuild."""

    old_optic: object
    new_optic: object
    mapping: tuple[tuple[object, object], ...]
    affected_rows: tuple[int, ...]
    reason: str


class DocumentAcceptance(QObject):
    """Publish one owned candidate without exposing a half-updated document.

    The guard covers synchronous notification callbacks, not background work.
    Ordinary edits during material preparation remain possible and revoke its
    captured token. Mutating services check this boundary before their first write.
    """

    model_replaced = Signal(object)
    state_changed = Signal()

    def __init__(self, connector):
        super().__init__(connector)
        self.connector = connector
        self._publishing = False

    @property
    def busy(self):
        return self._publishing

    def assert_mutation_allowed(self):
        if self._publishing:
            raise ValueError("A prepared document change is being applied.")

    def check_start(self, owner: str) -> AcceptanceCheck:
        connector = self.connector
        files = getattr(connector, "file_operations", None)
        if getattr(connector, "_calculation_shutdown_started", False) or getattr(
            files, "closing", False
        ):
            return AcceptanceCheck(False, "closed", "The document is closing.")
        if self._publishing:
            return AcceptanceCheck(
                False, "busy", "A prepared document change is being applied."
            )
        material = getattr(connector, "material_apply", None)
        if owner != "material" and material is not None and material.busy:
            return AcceptanceCheck(
                False, "busy", "Wait for material Apply or cancel it first."
            )
        if owner in {"material", "glass-expert"}:
            for attribute, label in (
                ("history", "Undo/Redo"),
                ("placement", "surface placement"),
                ("file_operations", "a file operation"),
            ):
                service = getattr(connector, attribute, None)
                if service is not None and service.busy:
                    return AcceptanceCheck(
                        False, "busy", f"Wait for {label} to finish."
                    )
            if getattr(files, "has_pending_load", False):
                return AcceptanceCheck(
                    False,
                    "busy",
                    "Resolve the pending file load before Apply.",
                )
            optimizer = getattr(connector, "_optimization_service", None)
            if optimizer is not None and (
                optimizer.is_running
                if owner == "material"
                else (
                    optimizer._request is not None
                    or (
                        optimizer._glass_expert is not None
                        and (
                            optimizer._glass_expert._pending is not None
                            or optimizer._glass_expert._delivering
                        )
                    )
                )
            ):
                return AcceptanceCheck(False, "busy", "Stop optimization before Apply.")
        return AcceptanceCheck(True, "ready", "Ready")

    def require_start(self, owner: str):
        check = self.check_start(owner)
        if not check.allowed:
            raise ValueError(check.message)

    def publish(
        self,
        candidate,
        *,
        owner,
        expected_optic,
        expected_edit_token,
        expected_history_revision,
        validate,
        undo=None,
        row_mapping=(),
        affected_rows=(),
        history_move=None,
    ) -> SurfaceReplacement:
        """Commit validated state, then expose identity mapping before UI rebuild.

        ``history_move`` is (direction, expected stack item, current native state).
        All validation precedes the silent stack/model writes; no computation or
        callback runs between final ownership checks and assignment.
        """
        self.require_start(owner)
        connector = self.connector
        manager = connector._undo_redo_manager
        if (
            connector.get_optic() is not expected_optic
            or connector.document_state.edit_token != expected_edit_token
            or manager.revision != expected_history_revision
        ):
            raise ValueError("The design or history changed before acceptance.")
        old_surfaces, new_surfaces = (
            tuple(expected_optic.surfaces),
            tuple(candidate.surfaces),
        )
        if any(
            not (0 <= a < len(old_surfaces) and 0 <= b < len(new_surfaces))
            for a, b in row_mapping
        ):
            raise ValueError("Prepared surface mapping is outside the document.")
        mapping = tuple((old_surfaces[a], new_surfaces[b]) for a, b in row_mapping)
        if len({id(a) for a, _ in mapping}) != len(mapping) or len(
            {id(b) for _, b in mapping}
        ) != len(mapping):
            raise ValueError("Prepared surface mapping is not one-to-one.")
        if (
            history_move is not None
            and manager.peek(history_move[0]) is not history_move[1]
        ):
            raise ValueError("The requested history state changed.")
        replacement = SurfaceReplacement(
            expected_optic, candidate, mapping, tuple(affected_rows), owner
        )
        self._publishing = True
        try:
            with connector.change_transaction(replacement=True):
                if not validate():
                    raise ValueError(
                        "Prepared result ownership changed before acceptance."
                    )
                if (
                    connector.get_optic() is not expected_optic
                    or connector.document_state.edit_token != expected_edit_token
                    or manager.revision != expected_history_revision
                    or (
                        history_move is not None
                        and manager.peek(history_move[0]) is not history_move[1]
                    )
                ):
                    raise ValueError("The design or history changed before acceptance.")
                if history_move is not None:
                    manager.move(history_move[0], history_move[2], notify=False)
                elif undo is not None:
                    manager.add_state(undo, notify=False)
                connector._optic = candidate
                connector.document_state.record("replacement")
                self.model_replaced.emit(replacement)
                connector.set_modified(True)
                connector.notify_change("replacement")
            manager.emit_availability()
        finally:
            self._publishing = False
            self.state_changed.emit()
        return replacement
