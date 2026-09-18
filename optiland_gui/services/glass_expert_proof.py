"""Bounded metadata proof for a trusted, already evaluated GlassExpert result.

No reconstruction, variable construction, catalog lookup or optical evaluation
belongs here. PreparedOptic bytes originate in our worker, never an imported file.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict

from optiland.fileio.optiland_handler import OptilandEncoder

from .glass_expert_records import MAX_RUN_BYTES, CandidateProof, canonical_json


def native_fingerprint(value):
    """Hash native metadata, retaining supported infinite radii/object distance."""
    data = value if isinstance(value, dict) else value.to_dict()
    digest, size = hashlib.sha256(), 0
    for chunk in OptilandEncoder(sort_keys=True, separators=(",", ":")).iterencode(
        data
    ):
        encoded = chunk.encode("utf-8")
        size += len(encoded)
        if size > MAX_RUN_BYTES:
            raise ValueError("Candidate native metadata exceeds the 64 MiB budget.")
        digest.update(encoded)
    return digest.hexdigest()


def review_fingerprint(result):
    """Bind the displayed frozen comparison to the exact worker candidate."""
    return hashlib.sha256(
        canonical_json(
            {
                "run_id": result.run_id,
                "input_digest": result.input_digest,
                "backend": asdict(result.candidate.backend),
                "components": result.candidate.component_types,
                "report": asdict(result.report),
                "warnings": result.warnings,
                "replay_unavailable": result.replay_unavailable,
                "elapsed": result.elapsed,
                "baseline_operands": [asdict(row) for row in result.baseline_operands],
                "candidate_operands": [
                    asdict(row) for row in result.candidate_operands
                ],
                "mapping": result.row_mapping,
            }
        )
    ).hexdigest()


def make_proof(result, optic, baseline_fingerprint):
    """Called only after the worker independently proves every target/value."""
    return CandidateProof(
        hashlib.sha256(result.candidate.data).hexdigest(),
        native_fingerprint(optic),
        baseline_fingerprint,
        review_fingerprint(result),
    )


def verify_proof(result, prepared):
    """Check immutable proof and complete frozen descriptors without evaluation."""
    setup, proof = prepared.setup, result.proof
    if (
        not result.report.candidate_eligible
        or result.report.baseline is None
        or result.candidate is None
        or not isinstance(proof, CandidateProof)
        or result.candidate.backend != setup.baseline.backend
        or result.run_id != setup.run_id
        or result.input_digest != setup.input_digest
    ):
        raise ValueError("No complete worker-proven candidate is available.")
    if (
        hashlib.sha256(result.candidate.data).hexdigest() != proof.prepared_fingerprint
        or review_fingerprint(result) != proof.review_fingerprint
    ):
        raise ValueError("The retained candidate differs from its worker proof.")
    state = result.report.candidate
    if (
        not math.isfinite(state.merit)
        or state.merit < 0
        or not math.isclose(
            result.report.baseline.merit,
            prepared.baseline_merit,
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
    ):
        raise ValueError("Candidate merit proof does not match its frozen baseline.")
    continuous = {row.variable_id: row.value for row in state.continuous}
    definitions = {
        row.identifier: row.as_dict()
        for row in setup.variables
        if row.as_dict()["type"] != "material"
    }
    if set(continuous) != set(definitions) or len(continuous) != len(state.continuous):
        raise ValueError("Candidate proof omits or repeats a continuous descriptor.")
    for identifier, value in continuous.items():
        definition = definitions[identifier]
        if (
            not math.isfinite(value)
            or (definition.get("min_val") is not None and value < definition["min_val"])
            or (definition.get("max_val") is not None and value > definition["max_val"])
        ):
            raise ValueError("Candidate proof violates a continuous variable bound.")
    choices = {row.variable_id: row for row in state.materials}
    if set(choices) != {target.variable_id for target in setup.targets} or len(
        choices
    ) != len(state.materials):
        raise ValueError("Candidate proof omits or repeats a material target.")
    for target in setup.targets:
        choice = choices[target.variable_id]
        if target.pool.resolve(choice.key).fingerprint != choice.fingerprint:
            raise ValueError("Candidate material proof differs from its exact pool.")
    operand_ids = tuple(row.identifier for row in prepared.resolved_operands)
    if (
        tuple(row.identifier for row in result.candidate_operands) != operand_ids
        or tuple(row.identifier for row in result.baseline_operands) != operand_ids
        or result.baseline_operands != prepared.baseline_operands
    ):
        raise ValueError("Candidate operand comparison differs from its frozen setup.")
