"""Owned calculation inputs and revision-tagged results (no Qt objects)."""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from optiland.optic import Optic


@dataclass(frozen=True)
class DocumentToken:
    """Identity and optical revision of a GUI-owned document."""

    document_id: str
    revision: int


@dataclass(frozen=True)
class BackendConfig:
    """Backend configuration explicitly installed in the calculation process."""

    name: str = "numpy"
    device: str = "cpu"
    precision: int = 64

    @classmethod
    def capture(cls) -> BackendConfig:
        import optiland.backend as be

        name = be.get_backend()
        if name == "torch":
            return cls(name, be.get_device(), be.get_precision())
        return cls(name)

    def apply(self) -> None:
        import optiland.backend as be

        be.set_backend(self.name)
        if self.name == "torch":
            be.set_device(self.device)
            be.set_precision(f"float{self.precision}")


@dataclass(frozen=True)
class OpticSnapshot:
    """Detached prescription bytes; capturing does not run the optic updater.

    The internal pickle transports our own serializer output, including existing
    polarization records and arrays. It is never loaded from an external file.
    The public Optiland data format remains unchanged.
    """

    data: bytes
    backend: BackendConfig

    @classmethod
    def capture(cls, optic: Optic) -> OpticSnapshot:
        return cls(pickle.dumps(optic.to_dict(), protocol=5), BackendConfig.capture())

    def restore(self) -> Optic:
        from optiland.optic import Optic

        self.backend.apply()
        return Optic.from_dict(pickle.loads(self.data))


@dataclass(frozen=True)
class JobRequest:
    """One calculation target generation and its detached inputs.

    ``context`` belongs to the GUI only (for example captured surface identities).
    The process transport deliberately excludes it.
    """

    job_id: int
    document: DocumentToken
    target: str
    generation: int
    handler: str
    snapshot: OpticSnapshot | None
    parameters: dict[str, Any]
    context: Any = field(default=None, compare=False, repr=False)

    def worker_message(self) -> dict:
        return {
            "command": "run",
            "job_id": self.job_id,
            "handler": self.handler,
            "snapshot": self.snapshot,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class JobResult:
    """Terminal calculation outcome, with GUI-side freshness determined once."""

    request: JobRequest
    status: str
    data: Any = None
    error: str = ""
    current: bool = False


class CalculationCancelled(Exception):
    """Raised at a safe numerical checkpoint after cancellation."""


def check_cancelled(cancelled: Any) -> None:
    """Stop numerical work at a safe boundary if cancellation was requested."""
    if cancelled.is_set():
        raise CalculationCancelled()
