"""Immutable, bounded-value reports from the public GlassExpert search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from optiland.materials.properties import MaterialKey

Phase = Literal["seed", "global", "local", "final"]
Outcome = Literal[
    "completed",
    "no_improvement",
    "already_reached",
    "no_candidate",
    "cancelled",
    "failed",
]


@dataclass(frozen=True)
class ContinuousValue:
    """An unscaled physical value associated with its original descriptor."""

    variable_id: str
    value: float


@dataclass(frozen=True)
class MaterialChoice:
    """An exact assigned identity; ``None`` denotes a reference-only baseline."""

    variable_id: str
    key: MaterialKey | None
    fingerprint: str


@dataclass(frozen=True)
class GlassExpertState:
    """A complete variable assignment and its actual sum-squared merit."""

    continuous: tuple[ContinuousValue, ...]
    materials: tuple[MaterialChoice, ...]
    merit: float
    eligible: bool


@dataclass(frozen=True)
class GlassExpertTrial:
    """A bounded trial summary; no optical model or payload is duplicated here."""

    index: int
    phase: Phase
    variable_id: str | None
    candidate_key: MaterialKey | None
    status: Literal["evaluated", "failed", "nonfinite", "cancelled"]
    merit: float | None
    evaluations: int
    converged: bool
    improved_best: bool
    message: str


@dataclass(frozen=True)
class GlassExpertProgress:
    """Current phase and total work counts; merit always means sum_squared."""

    phase: Phase
    trials: int
    evaluations: int
    baseline_merit: float
    best_merit: float | None
    current_variable_id: str | None
    current_key: MaterialKey | None


@dataclass(frozen=True)
class GlassExpertReport:
    """Terminal status, reference/candidate comparison and bounded trial history."""

    outcome: Outcome
    baseline: GlassExpertState | None
    candidate: GlassExpertState | None
    improved: bool
    improvement_percent: float | None
    trials: tuple[GlassExpertTrial, ...]
    total_trials: int
    evaluations: int
    omitted_trials: int
    warnings: tuple[str, ...]

    @property
    def candidate_eligible(self) -> bool:
        """Eligibility is independent from convergence and improvement."""
        return (
            self.outcome in {"completed", "no_improvement"}
            and self.candidate is not None
            and self.candidate.eligible
        )


class GlassExpertCancelled(Exception):
    """Cooperative cancellation with a completed bounded diagnostic report."""

    def __init__(self, message="GlassExpert was cancelled", report=None):
        super().__init__(message)
        self.report = report
