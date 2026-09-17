"""Bounded immutable GlassExpert inputs and review data, without Qt ownership."""

from __future__ import annotations

import hashlib
import json
import math
import pickle
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

from .prepared_optic import PreparedOptic

if TYPE_CHECKING:
    from optiland.materials.properties import MaterialKey, SourceStamp
    from optiland.optimization.glass_expert_records import GlassExpertReport
    from optiland.optimization.material_candidates import MaterialPool

MAX_RUN_BYTES = 64 * 1024 * 1024
MAX_VARIABLES = 256
MAX_OPERANDS = 10_000
MAX_TARGETS = 32
MAX_CANDIDATES = 10_000
MAX_DIAGNOSTIC = 2048


def canonical_json(value) -> bytes:
    """Copy plain finite JSON without accepting executable/custom descriptors."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError(
            "GlassExpert definitions must contain finite plain JSON."
        ) from error


def _object(data):
    if not isinstance(data, bytes) or len(data) > MAX_RUN_BYTES:
        raise ValueError("Expected bounded JSON object bytes.")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid GlassExpert JSON definition.") from error
    if not isinstance(value, dict):
        raise ValueError("GlassExpert definitions must be JSON objects.")
    canonical_json(value)
    return value


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("GlassExpert identifiers must contain 1–200 characters.")


def _finite(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label} must be a finite real number.")


def _freeze_sequences(record, names):
    for name in names:
        object.__setattr__(record, name, tuple(getattr(record, name)))


def _freeze_diagnostics(record):
    values = tuple(record.warnings)
    if any(not isinstance(value, str) for value in values):
        raise ValueError("GlassExpert diagnostic text must be strings.")
    values = tuple(dict.fromkeys(values))
    messages = tuple(
        value
        if len(value) <= MAX_DIAGNOSTIC
        else (
            value[: MAX_DIAGNOSTIC - 60]
            + f" [truncated {len(value) - MAX_DIAGNOSTIC + 60} characters]"
        )
        for value in values[:1000]
    )
    if len(values) > 1000:
        messages += (f"{len(values) - 1000} additional diagnostics omitted.",)
    object.__setattr__(record, "warnings", messages)


@dataclass(frozen=True)
class FrozenDefinition:
    """A stable row identity beside a detached, canonical public definition."""

    identifier: str
    kind: Literal["variable", "operand"]
    data: bytes

    def __post_init__(self):
        _identifier(self.identifier)
        if self.kind not in {"variable", "operand"}:
            raise ValueError("Unknown GlassExpert definition kind.")
        object.__setattr__(self, "data", canonical_json(_object(self.data)))

    @classmethod
    def from_dict(cls, identifier, kind, definition):
        return cls(identifier, kind, canonical_json(definition))

    def as_dict(self):
        return json.loads(self.data)


@dataclass(frozen=True)
class GlassExpertSettings:
    """Finite reproducible solver settings; no plotting or executable callbacks."""

    global_samples: int = 2
    num_neighbours: int = 7
    maxiter: int = 1000
    tol: float = 1e-3
    seed: int = 1234
    nd_scale: float = 1.0
    vd_scale: float = 1.0
    report_limit: int = 1000

    def __post_init__(self):
        for name, low, high in (
            ("global_samples", 2, MAX_CANDIDATES),
            ("num_neighbours", 1, MAX_CANDIDATES),
            ("maxiter", 1, 1_000_000),
            ("seed", 0, 2**32 - 1),
            ("report_limit", 1, 1000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer between {low} and {high}.")
        for name in ("tol", "nd_scale", "vd_scale"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError(f"{name} must be finite and positive.")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive.")
            object.__setattr__(self, name, float(value))

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class MaterialTarget:
    """One material-variable descriptor bound to an exact frozen public pool."""

    variable_id: str
    surface_number: int
    pool: MaterialPool

    def __post_init__(self):
        from optiland.optimization.material_candidates import MaterialPool

        _identifier(self.variable_id)
        if type(self.surface_number) is not int or self.surface_number < 0:
            raise ValueError("A material target needs a nonnegative surface row.")
        if not isinstance(self.pool, MaterialPool):
            raise ValueError("A material target needs an exact MaterialPool.")


@dataclass(frozen=True)
class GlassExpertSetup:
    """Frozen model/definitions/pools; contains no live document authority."""

    run_id: str
    baseline: PreparedOptic
    variables: tuple[FrozenDefinition, ...]
    operands: tuple[FrozenDefinition, ...]
    targets: tuple[MaterialTarget, ...]
    settings: GlassExpertSettings = field(default_factory=GlassExpertSettings)
    operand_metadata: bytes = b"{}"
    input_digest: str = field(init=False)

    def __post_init__(self):
        _identifier(self.run_id)
        if not isinstance(self.baseline, PreparedOptic):
            raise ValueError("GlassExpert requires an owned prepared baseline.")
        if not isinstance(self.settings, GlassExpertSettings):
            raise ValueError("Invalid GlassExpert settings.")
        for name, cap, cls in (
            ("variables", MAX_VARIABLES, FrozenDefinition),
            ("operands", MAX_OPERANDS, FrozenDefinition),
            ("targets", MAX_TARGETS, MaterialTarget),
        ):
            values = tuple(getattr(self, name))
            if not 1 <= len(values) <= cap or any(
                not isinstance(v, cls) for v in values
            ):
                raise ValueError(f"GlassExpert {name} must contain 1–{cap} valid rows.")
            object.__setattr__(self, name, values)
        identifiers = [row.identifier for row in (*self.variables, *self.operands)]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("GlassExpert row identifiers must be distinct.")
        if any(row.kind != "variable" for row in self.variables) or any(
            row.kind != "operand" for row in self.operands
        ):
            raise ValueError("GlassExpert definition kind does not match its section.")
        if len({t.variable_id for t in self.targets}) != len(self.targets) or len(
            {t.surface_number for t in self.targets}
        ) != len(self.targets):
            raise ValueError("Material target IDs and surface rows must be distinct.")
        if any(
            t.variable_id not in {v.identifier for v in self.variables}
            for t in self.targets
        ):
            raise ValueError(
                "Every material target must name an original variable row."
            )
        metadata = canonical_json(_object(self.operand_metadata))
        object.__setattr__(self, "operand_metadata", metadata)
        candidates, stamps = {}, {}
        for target in self.targets:
            for candidate in target.pool.candidates:
                old = candidates.setdefault(candidate.key, candidate)
                if old != candidate:
                    raise ValueError(
                        "One exact material key has conflicting pool records."
                    )
                stamp = stamps.setdefault(candidate.stamp.source_id, candidate.stamp)
                if stamp != candidate.stamp:
                    raise ValueError("One source has conflicting captured generations.")
        from optiland.optimization.material_candidates import MaterialPool

        # Equal native records captured independently must still share one payload
        # in the worker frame, not multiply the bytes by the number of target pools.
        object.__setattr__(
            self,
            "targets",
            tuple(
                MaterialTarget(
                    target.variable_id,
                    target.surface_number,
                    MaterialPool(
                        tuple(candidates[c.key] for c in target.pool.candidates)
                    ),
                )
                for target in self.targets
            ),
        )
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError("GlassExpert accepts at most 10,000 unique candidates.")
        byte_count = (
            len(self.baseline.data)
            + len(metadata)
            + sum(len(v.data) for v in (*self.variables, *self.operands))
            + sum(len(c.owned_data) for c in candidates.values())
        )
        if byte_count > MAX_RUN_BYTES:
            raise ValueError("GlassExpert input exceeds the 64 MiB run budget.")
        # Internal pickle is only for transport accounting, never an
        # external manifest. Memoization shares repeated baseline/pool payloads.
        body = pickle.dumps(
            (
                self.baseline,
                self.variables,
                self.operands,
                self.targets,
                self.settings,
                metadata,
            ),
            protocol=5,
        )
        if len(body) > MAX_RUN_BYTES:
            raise ValueError("GlassExpert input exceeds the 64 MiB run budget.")
        identity = {
            "baseline": hashlib.sha256(self.baseline.data).hexdigest(),
            "backend": asdict(self.baseline.backend),
            "components": self.baseline.component_types,
            "variables": [
                asdict(row) | {"data": row.as_dict()} for row in self.variables
            ],
            "operands": [
                asdict(row) | {"data": row.as_dict()} for row in self.operands
            ],
            "targets": [
                {
                    "variable_id": target.variable_id,
                    "surface_number": target.surface_number,
                    "candidates": [
                        asdict(c) | {"owned_data": c.fingerprint}
                        for c in target.pool.candidates
                    ],
                }
                for target in self.targets
            ],
            "settings": self.settings.as_dict(),
            "operand_metadata": json.loads(metadata),
        }
        object.__setattr__(
            self, "input_digest", hashlib.sha256(canonical_json(identity)).hexdigest()
        )

    @property
    def source_stamps(self) -> tuple[SourceStamp, ...]:
        stamps = {
            c.stamp.source_id: c.stamp for t in self.targets for c in t.pool.candidates
        }
        return tuple(stamps[key] for key in sorted(stamps))

    @property
    def definitions_key(self):
        return (
            tuple(v.data for v in self.variables),
            tuple(o.data for o in self.operands),
        )


@dataclass(frozen=True)
class OperandValue:
    """An actual value for one resolved frozen operand, not the current GUI list."""

    identifier: str
    value: float
    target: float | None
    min_val: float | None
    max_val: float | None
    effective_weight: float
    delta: float
    within_bounds: bool

    def __post_init__(self):
        _identifier(self.identifier)
        for name in ("value", "effective_weight", "delta"):
            _finite(getattr(self, name), name)
        for name in ("target", "min_val", "max_val"):
            if getattr(self, name) is not None:
                _finite(getattr(self, name), name)
        if type(self.within_bounds) is not bool:
            raise ValueError("Operand constraint status must be a boolean.")


@dataclass(frozen=True)
class PreparedGlassExpertSetup:
    """Verified baseline and replay inputs, without document acceptance rights."""

    setup: GlassExpertSetup
    resolved_variables: tuple[FrozenDefinition, ...]
    resolved_operands: tuple[FrozenDefinition, ...]
    baseline_merit: float
    baseline_operands: tuple[OperandValue, ...]
    portable_baseline: bytes | None
    replay_unavailable: str | None
    warnings: tuple[str, ...]

    def __post_init__(self):
        _freeze_sequences(
            self, ("resolved_variables", "resolved_operands", "baseline_operands")
        )
        _finite(self.baseline_merit, "Baseline merit")
        _freeze_diagnostics(self)


@dataclass(frozen=True)
class GlassExpertProgress:
    """One bounded run-owned presentation update; merit is sum_squared."""

    run_id: str
    input_digest: str
    phase: str
    trials: int
    evaluations: int
    baseline_merit: float | None
    best_merit: float | None
    elapsed: float
    continuous: tuple[tuple[str, float], ...] = ()
    materials: tuple[tuple[str, MaterialKey | None], ...] = ()
    merit_quantity: str = "sum_squared"

    def __post_init__(self):
        for name in ("continuous", "materials"):
            object.__setattr__(
                self, name, tuple(tuple(value) for value in getattr(self, name))
            )
        _finite(self.elapsed, "Elapsed time")


@dataclass(frozen=True)
class GlassExpertJobResult:
    """A fully owned terminal candidate/report, never permission to publish it."""

    run_id: str
    input_digest: str
    report: GlassExpertReport
    candidate: PreparedOptic | None
    native_candidate: bytes | None
    row_mapping: tuple[tuple[int, int], ...]
    baseline_operands: tuple[OperandValue, ...]
    candidate_operands: tuple[OperandValue, ...]
    warnings: tuple[str, ...]
    replay_unavailable: str | None
    elapsed: float

    def __post_init__(self):
        _freeze_sequences(self, ("baseline_operands", "candidate_operands"))
        object.__setattr__(
            self, "row_mapping", tuple(tuple(row) for row in self.row_mapping)
        )
        _finite(self.elapsed, "Elapsed time")
        _freeze_diagnostics(self)

    @property
    def outcome(self):
        return self.report.outcome
