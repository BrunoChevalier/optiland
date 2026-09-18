"""Shared-worker GlassExpert manifest loading and one-artifact preparation."""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import asdict, dataclass
from pathlib import Path

from optiland.materials.properties import MaterialKey
from optiland.optimization.glass_expert_replay import (
    MAX_REPLAY_BYTES,
    decode_glass_expert_manifest,
    encode_glass_expert_manifest,
    restore_replay_baseline,
)
from optiland.optimization.material_candidates import MaterialCandidate, MaterialPool

from .glass_expert_jobs import prepare_setup
from .glass_expert_portability import capture_portable_optic
from .glass_expert_records import (
    FrozenDefinition,
    GlassExpertJobResult,
    GlassExpertSettings,
    GlassExpertSetup,
    MaterialTarget,
    PreparedGlassExpertSetup,
    canonical_json,
)
from .job_records import check_cancelled
from .material_apply_tasks import validate_spectrum
from .prepared_optic import PreparedOptic


@dataclass(frozen=True)
class LoadedManifest:
    """Fresh detached preflight; saved result data grants no authority."""

    operation_id: str
    prepared: PreparedGlassExpertSetup
    recorded_digest: str
    warnings: tuple[str, ...]
    kind: str = "load"


@dataclass(frozen=True)
class ManifestExport:
    """One owned encoded artifact, or an explicit unavailable diagnostic."""

    operation_id: str
    kind: str
    data: bytes | None
    unavailable_reason: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class OpenedCandidate:
    """Portable reconstruction for a separate document, never current Apply."""

    operation_id: str
    prepared: PreparedOptic
    run_id: str
    input_digest: str
    kind: str = "open_candidate"


def source_warnings(stamps, current):
    """Describe missing generations without looking up substitute materials."""
    current = {stamp.source_id: stamp for stamp in current}
    messages = tuple(
        f"Captured source {stamp.source_id[:1800]!r} is "
        f"{'missing' if stamp.source_id not in current else 'a different generation'}; "
        "embedded materials are retained for detached inspection."
        for stamp in stamps
        if current.get(stamp.source_id) != stamp
    )
    return messages[:1000] + (
        (f"{len(messages) - 1000} additional source warnings omitted.",)
        if len(messages) > 1000
        else ()
    )


def _bounded(value):
    if len(pickle.dumps(value, protocol=5)) > MAX_REPLAY_BYTES:
        raise ValueError("GlassExpert file data exceeds the 64 MiB transport limit.")
    return value


def _definitions(rows):
    return [{"id": row.identifier, "definition": row.as_dict()} for row in rows]


def _payload(prepared, result):
    if not isinstance(prepared, PreparedGlassExpertSetup):
        raise ValueError("Prepare an exact frozen problem before exporting it.")
    setup = prepared.setup
    if result is not None and (
        not isinstance(result, GlassExpertJobResult)
        or result.run_id != setup.run_id
        or result.input_digest != setup.input_digest
    ):
        raise ValueError("Export result does not belong to the prepared run.")
    candidates = {c.key: c for target in setup.targets for c in target.pool.candidates}
    return {
        "baseline": None
        if prepared.portable_baseline is None
        else {
            "native": prepared.portable_baseline.decode("utf-8"),
            "digest": hashlib.sha256(prepared.portable_baseline).hexdigest(),
        },
        "backend": asdict(setup.baseline.backend),
        "variables": _definitions(setup.variables),
        "operands": _definitions(setup.operands),
        "resolved_variables": _definitions(prepared.resolved_variables),
        "resolved_operands": _definitions(prepared.resolved_operands),
        "targets": [
            {
                "variable_id": target.variable_id,
                "surface_number": target.surface_number,
                "keys": [asdict(c.key) for c in target.pool.candidates],
            }
            for target in setup.targets
        ],
        "candidates": [
            {"record": c.to_dict(), "fingerprint": c.fingerprint}
            for c in candidates.values()
        ],
        "settings": setup.settings.as_dict(),
        "operand_metadata": json.loads(setup.operand_metadata),
        "baseline_merit": prepared.baseline_merit,
        "baseline_operands": [asdict(row) for row in prepared.baseline_operands],
        "result": None
        if result is None
        else {
            "report": asdict(result.report),
            "candidate_operands": [asdict(row) for row in result.candidate_operands],
        },
        "warnings": list(prepared.warnings),
        "replay_unavailable": prepared.replay_unavailable,
    }


def _script(data):
    # repr(bytes) keeps data inert even when a user label contains Python syntax.
    # Execution is an explicit user action in a separately installed core runtime.
    result = (
        '"""Frozen GlassExpert replay; generated data, no GUI dependency."""\n'
        "import json\nfrom dataclasses import asdict\n"
        "from optiland.optimization.glass_expert_replay import (\n"
        "    decode_glass_expert_manifest, replay_glass_expert)\n\n"
        f"MANIFEST = {data!r}\n\n"
        "if __name__ == '__main__':\n"
        "    inputs = decode_glass_expert_manifest(MANIFEST)\n"
        "    result = replay_glass_expert(inputs)\n"
        "    print(json.dumps(asdict(result.report), "
        "sort_keys=True, allow_nan=False))\n"
    ).encode()
    if len(result) > MAX_REPLAY_BYTES:
        raise ValueError("Embedded script exceeds the 64 MiB output limit.")
    return result


def prepare_export(snapshot, parameters, progress, cancelled):
    """Encode one artifact without opening a chooser or changing document state."""
    check_cancelled(cancelled)
    kind = parameters["kind"]
    prepared, result = parameters["prepared"], parameters.get("result")
    if kind not in {"manifest", "script", "candidate"}:
        raise ValueError("Unknown GlassExpert export kind.")
    if snapshot is not None and snapshot != prepared.setup.baseline:
        raise ValueError("Export snapshot differs from the captured run.")
    progress("Preparing GlassExpert " + kind)
    payload = _payload(prepared, result)
    reason, data = None, None
    if kind == "candidate":
        data = None if result is None else result.native_candidate
        if data is None:
            reason = (
                None if result is None else result.replay_unavailable
            ) or "No complete native candidate is available."
    elif kind == "script" and prepared.portable_baseline is None:
        reason = prepared.replay_unavailable or "Portable baseline unavailable."
    else:
        data = encode_glass_expert_manifest(payload)
        if kind == "script":
            data = _script(data)
    check_cancelled(cancelled)
    if data is not None and (type(data) is not bytes or len(data) > MAX_REPLAY_BYTES):
        raise ValueError("GlassExpert output exceeds its byte limit.")
    return _bounded(
        ManifestExport(
            parameters["operation_id"], kind, data, reason, prepared.warnings
        )
    )


def load_manifest(snapshot, parameters, progress, cancelled):
    """Read bounded public JSON and build an entirely new detached preflight."""
    check_cancelled(cancelled)
    progress("Reading frozen GlassExpert setup")
    chunks, count = [], 0
    with Path(parameters["path"]).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            check_cancelled(cancelled)
            count += len(chunk)
            if count > MAX_REPLAY_BYTES:
                raise ValueError("GlassExpert manifest exceeds the 64 MiB input limit.")
            chunks.append(chunk)
    inputs = decode_glass_expert_manifest(b"".join(chunks))
    payload = inputs.payload
    optic = restore_replay_baseline(
        inputs, checkpoint=lambda: check_cancelled(cancelled)
    )
    candidates = {
        c.key: c
        for c in (
            MaterialCandidate.from_dict(row["record"]) for row in payload["candidates"]
        )
    }
    setup = GlassExpertSetup(
        parameters["run_id"],
        PreparedOptic.capture(optic),
        tuple(
            FrozenDefinition.from_dict(row["id"], "variable", row["definition"])
            for row in payload["variables"]
        ),
        tuple(
            FrozenDefinition.from_dict(row["id"], "operand", row["definition"])
            for row in payload["operands"]
        ),
        tuple(
            MaterialTarget(
                target["variable_id"],
                target["surface_number"],
                MaterialPool(
                    tuple(candidates[MaterialKey(**key)] for key in target["keys"])
                ),
            )
            for target in payload["targets"]
        ),
        GlassExpertSettings(**payload["settings"]),
        canonical_json(payload["operand_metadata"]),
    )
    prepared = prepare_setup(setup.baseline, {"setup": setup}, progress, cancelled)
    import math

    if (
        not math.isclose(
            prepared.baseline_merit,
            payload["baseline_merit"],
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
        or _definitions(prepared.resolved_variables) != payload["resolved_variables"]
        or _definitions(prepared.resolved_operands) != payload["resolved_operands"]
    ):
        raise ValueError("Loaded setup differs from its recorded resolved objective.")
    warnings = source_warnings(setup.source_stamps, parameters.get("source_stamps", ()))
    check_cancelled(cancelled)
    return _bounded(
        LoadedManifest(
            parameters["operation_id"],
            prepared,
            inputs.digest,
            tuple(dict.fromkeys((*prepared.warnings, *warnings))),
        )
    )


def open_candidate(snapshot, parameters, progress, cancelled):
    """Prove complete portable reconstruction before opening a separate model."""
    check_cancelled(cancelled)
    prepared, result = parameters["prepared"], parameters["result"]
    payload = _payload(prepared, result)
    if snapshot is not None and snapshot != prepared.setup.baseline:
        raise ValueError("Candidate snapshot differs from the captured run.")
    if (
        result is None
        or result.candidate is None
        or not result.report.candidate_eligible
        or result.native_candidate is None
    ):
        raise ValueError(
            "A complete portable candidate is required to open separately."
        )
    native = result.native_candidate
    payload["baseline"] = {
        "native": native.decode("utf-8"),
        "digest": hashlib.sha256(native).hexdigest(),
    }
    progress("Reconstructing portable GlassExpert candidate")
    inputs = decode_glass_expert_manifest(encode_glass_expert_manifest(payload))
    optic = restore_replay_baseline(
        inputs, checkpoint=lambda: check_cancelled(cancelled)
    )
    if (
        capture_portable_optic(optic, cancelled) != native
        or capture_portable_optic(result.candidate.restore(), cancelled) != native
    ):
        raise ValueError(
            "Portable candidate differs from the retained complete result."
        )
    for surface in optic.surfaces:
        validate_spectrum(optic, surface.material_post, cancelled)
    check_cancelled(cancelled)
    return _bounded(
        OpenedCandidate(
            parameters["operation_id"],
            PreparedOptic.capture(optic),
            result.run_id,
            result.input_digest,
        )
    )
