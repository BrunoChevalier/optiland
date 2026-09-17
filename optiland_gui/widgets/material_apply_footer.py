"""Explicit material application controls for a captured optical medium."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from optiland.materials.properties import MaterialKey, SourceStamp
    from optiland_gui.services.material_apply import MaterialApplyService


class MaterialApplyFooter(QWidget):
    """Keep target authority distinct from inspected material and keyboard focus.

    The supplied service performs all document eligibility and ownership checks.
    This widget displays its decision and submits only through an explicit click.
    No optical evaluation, snapshot restoration or text-name resolution runs here.
    """

    def __init__(self, service: MaterialApplyService, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self._key: MaterialKey | None = None
        self._stamp: SourceStamp | None = None
        self._target = None
        self._closed = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        self.target_label = self._label("Material application target")
        self.reason_label = self._label("Material application availability")
        self.outcome_label = self._label("Material application result")
        text.addWidget(self.target_label)
        text.addWidget(self.reason_label)
        text.addWidget(self.outcome_label)
        layout.addLayout(text, 1)
        actions = QVBoxLayout()
        self.refresh_button = QPushButton("Refresh target", self)
        self.apply_button = QPushButton("Apply material", self)
        self.cancel_button = QPushButton("Cancel Apply", self)
        for button in (self.refresh_button, self.apply_button, self.cancel_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.refresh_button.clicked.connect(service.refresh_target)
        self.apply_button.clicked.connect(self._submit)
        self.cancel_button.clicked.connect(service.cancel)
        service.state_changed.connect(self.refresh)
        service.finished.connect(self._finished)
        self.refresh()

    def _label(self, name: str) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setAccessibleName(name)
        return label

    def set_selection(self, key: MaterialKey | None, stamp: SourceStamp | None) -> None:
        """Use the exact identity and generation currently shown by the inspector."""
        # A source reload can cancel an Apply. Keep that outcome visible while
        # the same inspected identity advances to its new source generation.
        if key != self._key:
            self.outcome_label.clear()
        self._key, self._stamp = key, stamp
        self.refresh()

    def refresh(self) -> None:
        """Display current service eligibility without renewing target authority."""
        target = self.service.target
        if target is not self._target:
            self._target = target
            self.outcome_label.clear()
        self.target_label.setText(
            f"Target: {target.label}"
            if target is not None
            else "No target — use Browse materials from a Lens Data Editor "
            "material cell."
        )
        check = self.service.can_apply(self._key, self._stamp)
        self.reason_label.setText(check.message)
        self.apply_button.setEnabled(check.allowed and not self._closed)
        self.apply_button.setToolTip("<qt>" + html.escape(check.message) + "</qt>")
        self.refresh_button.setEnabled(
            target is not None and not self.service.busy and not self._closed
        )
        self.cancel_button.setVisible(self.service.busy)
        self.cancel_button.setEnabled(self.service.busy and not self._closed)
        self.outcome_label.setVisible(bool(self.outcome_label.text()))

    def _submit(self) -> None:
        if not self._closed:
            self.outcome_label.clear()
            self.service.submit(self._key, self._stamp)

    def _finished(self, outcome: object) -> None:
        self.refresh()
        self.outcome_label.setText("\n".join((outcome.message, *outcome.warnings)))
        self.outcome_label.setVisible(True)

    def close_service(self) -> None:
        """Disable every mutation control when the shared worker has stopped."""
        self._closed = True
        self.refresh()
