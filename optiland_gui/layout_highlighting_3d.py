"""Presentation-only 3D styling of retained, explicitly owned scene actors."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QTimer, Slot
from PySide6.QtGui import QColor


@dataclass
class ActorBinding:
    """One retained actor and its GUI-local surface ownership."""

    actor: object
    surfaces: tuple
    role: str
    normal: object
    visible: bool


class LayoutHighlightController3D(QObject):
    """Keep VTK mutation on Qt and defer hidden-view changes until activation."""

    def __init__(self, viewer, state):
        super().__init__(viewer)
        self.viewer, self.state = viewer, state
        self.document = None
        self.bindings = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.apply)
        state.changed.connect(self.schedule)
        viewer.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            self.schedule()
        elif event.type() == QEvent.Type.Hide:
            self._timer.stop()
        return super().eventFilter(watched, event)

    @Slot()
    def schedule(self):
        if self.viewer.isVisible():
            self._timer.start(0)

    def clear(self):
        self._timer.stop()
        for binding in self.bindings:
            binding.actor.GetProperty().DeepCopy(binding.normal)
            binding.actor.SetVisibility(binding.visible)
        self.bindings.clear()
        self.document = None

    def install(self, actors, identities):
        import vtk

        self.clear()
        self.document = self.state.document
        for actor, mesh in actors:
            if mesh["role"] == "ray" or not mesh["surfaces"]:
                continue
            normal = vtk.vtkProperty()
            normal.DeepCopy(actor.GetProperty())
            self.bindings.append(
                ActorBinding(
                    actor,
                    tuple(identities[index] for index in mesh["surfaces"]),
                    mesh["role"],
                    normal,
                    bool(actor.GetVisibility()),
                )
            )
        self.apply(render=False)

    @Slot()
    def apply(self, *, render=True):
        if not self.viewer.isVisible():
            return
        selected = QColor(
            "#007ACC" if self.viewer.current_theme == "dark" else "#6C757D"
        )
        hovered = selected.lighter(140)
        for binding in self.bindings:
            kind = (
                self.state.state_for(binding.surfaces)
                if self.document is self.state.document
                else "normal"
            )
            prop = binding.actor.GetProperty()
            prop.DeepCopy(binding.normal)
            binding.actor.SetVisibility(binding.visible)
            if kind == "normal":
                continue
            color = selected if kind == "selected" else hovered
            prop.SetColor(color.redF(), color.greenF(), color.blueF())
            prop.SetAmbient(0.6)
            prop.SetDiffuse(0.4)
            if binding.role in ("body_edge", "surface_edge"):
                exact = binding.role == "surface_edge"
                width = (2.6 if kind == "selected" else 2.2) if exact else 1.3
                prop.SetLineWidth(width)
                prop.SetOpacity(1)
                prop.SetAmbient(1)
                prop.SetDiffuse(0)
            elif binding.role == "face_highlight":
                prop.SetOpacity(0.5 if kind == "selected" else 0.35)
            elif binding.role == "surface":
                # A selection must not turn an opaque mirror into an x-ray view.
                prop.SetOpacity(binding.normal.GetOpacity())
            else:
                prop.SetOpacity(0.28 if kind == "selected" else 0.18)
            binding.actor.SetVisibility(True)
        if render and self.bindings:
            self.viewer.vtkWidget.GetRenderWindow().Render()
