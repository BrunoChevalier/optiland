"""Bounded metadata attestation and independent descriptor completeness checks."""

from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from optiland.materials.sources import load_source
from optiland.optimization.material_candidates import MaterialPool
from optiland_gui.services import glass_expert_jobs
from optiland_gui.services import glass_expert_proof as proof
from optiland_gui.services.glass_expert_records import CandidateProof, MaterialTarget
from optiland_gui.services.job_records import BackendConfig, CalculationCancelled
from tests.gui.test_glass_expert_acceptance import finish_actual
from tests.gui.test_glass_expert_service import frozen as frozen
from tests.gui.test_material_jobs import owned_source


@pytest.mark.parametrize("value", ["", "a" * 63, "A" * 64, "z" * 64, 42])
def test_proof_fingerprints_require_sha256(value):
    with pytest.raises(ValueError, match="SHA256"):
        CandidateProof(value, "a" * 64, "b" * 64, "c" * 64)


def test_native_fingerprint_budget_and_infinite_native_geometry(monkeypatch):
    value = {"radius": float("inf"), "object_distance": float("inf")}
    assert proof.native_fingerprint(value) == proof.native_fingerprint(
        dict(reversed(tuple(value.items())))
    )
    monkeypatch.setattr(proof, "MAX_RUN_BYTES", 4)
    with pytest.raises(ValueError, match="64 MiB"):
        proof.native_fingerprint(value)


@pytest.mark.parametrize(
    "case",
    [
        "merit",
        "baseline",
        "continuous",
        "duplicate",
        "bound-low",
        "bound-high",
        "material",
        "fingerprint",
        "operand",
        "baseline-operand",
    ],
)
def test_self_consistent_hash_does_not_replace_complete_numeric_descriptor_proof(
    frozen, case
):
    result, prepared, _request = finish_actual(frozen)
    state = result.report.candidate
    if case == "merit":
        result = replace(
            result, report=replace(result.report, candidate=replace(state, merit=-1.0))
        )
    elif case == "baseline":
        result = replace(
            result,
            report=replace(
                result.report, baseline=replace(result.report.baseline, merit=999.0)
            ),
        )
    elif case == "continuous":
        result = replace(
            result,
            report=replace(result.report, candidate=replace(state, continuous=())),
        )
    elif case == "duplicate":
        result = replace(
            result,
            report=replace(
                result.report, candidate=replace(state, continuous=state.continuous * 2)
            ),
        )
    elif case.startswith("bound"):
        value = 0.5 if case == "bound-low" else 15.5
        result = replace(
            result,
            report=replace(
                result.report,
                candidate=replace(
                    state, continuous=(replace(state.continuous[0], value=value),)
                ),
            ),
        )
    elif case == "material":
        result = replace(
            result,
            report=replace(result.report, candidate=replace(state, materials=())),
        )
    elif case == "fingerprint":
        result = replace(
            result,
            report=replace(
                result.report,
                candidate=replace(
                    state,
                    materials=(replace(state.materials[0], fingerprint="0" * 64),),
                ),
            ),
        )
    elif case == "operand":
        result = replace(result, candidate_operands=())
    else:
        result = replace(
            result,
            baseline_operands=(replace(result.baseline_operands[0], value=999.0),),
        )
    # Even a broken internal producer that recalculated its hash cannot omit the
    # frozen original descriptors or bypass physical bounds and exact membership.
    result = replace(
        result,
        proof=replace(
            result.proof, review_fingerprint=proof.review_fingerprint(result)
        ),
    )
    with pytest.raises(ValueError, match="proof|comparison"):
        proof.verify_proof(result, prepared)


def test_all_targets_include_singleton_assignment_and_mapping(frozen):
    single = MaterialTarget(
        "material:2", 2, MaterialPool((frozen.target.pool.candidates[1],))
    )
    frozen.setup = frozen.service.capture_glass_expert_setup((frozen.target, single))
    result, prepared, _request = finish_actual(frozen)
    proof.verify_proof(result, prepared)
    assert len(result.report.candidate.materials) == 2
    assert result.report.candidate.materials[1].key == single.pool.candidates[0].key
    assert frozen.service.apply_glass_expert_candidate()
    assert (
        proof.native_fingerprint(frozen.connector.get_optic())
        == result.proof.native_fingerprint
    )


def test_cache_is_bound_to_exact_immutable_source_result(frozen, monkeypatch):
    from optiland_gui.services import glass_expert_strategy

    finish_actual(frozen)
    service, materials = frozen.service, frozen.connector.materials
    source_id = frozen.setup.source_stamps[0].source_id
    source = materials.source_result(source_id)
    source = replace(source)
    materials._sources[source_id] = source
    assert service.glass_expert_acceptance().allowed
    owner = service._glass_expert._owner
    assert owner.checked_sources[source_id] is source

    def reparsed(*args, **kwargs):
        pytest.fail("An unchanged immutable source was parsed again")

    monkeypatch.setattr(glass_expert_strategy.json, "loads", reparsed)
    assert service.glass_expert_acceptance().allowed


def test_invalid_same_stamp_payload_fails_without_raising_from_ui(frozen):
    finish_actual(frozen)
    source_id = frozen.setup.source_stamps[0].source_id
    source = frozen.connector.materials.source_result(source_id)
    rows = source.snapshot.materials
    changed = replace(rows[0], owned_data=b"{")
    frozen.connector.materials._sources[source_id] = replace(
        source, snapshot=replace(source.snapshot, materials=(changed, *rows[1:]))
    )
    assert frozen.service.glass_expert_acceptance().code == "source"


def test_cancellation_during_final_proof_does_not_return_candidate(frozen, monkeypatch):
    cancelled = threading.Event()
    original = glass_expert_jobs.make_proof

    def cancel_after_proof(*args):
        value = original(*args)
        cancelled.set()
        return value

    monkeypatch.setattr(glass_expert_jobs, "make_proof", cancel_after_proof)
    with pytest.raises(CalculationCancelled):
        glass_expert_jobs.run(
            frozen.setup.baseline,
            {"setup": frozen.setup},
            lambda *a, **k: None,
            cancelled,
        )


def test_prepared_wavelengths_are_owned_exact_frozen_presentation(frozen):
    _result, prepared, _request = finish_actual(frozen)
    expected = tuple(
        float(w.value) for w in frozen.connector.get_optic().wavelengths.wavelengths
    )
    assert prepared.wavelengths_um == expected
    supplied = list(expected)
    detached = replace(prepared, wavelengths_um=supplied)
    supplied.clear()
    assert detached.wavelengths_um == expected
    for invalid in ((), (0.0,), (-1.0,), (True,), (float("nan"),)):
        with pytest.raises(ValueError, match="wavelength|Wavelength"):
            replace(prepared, wavelengths_um=invalid)
    frozen.service._glass_expert.prepared = replace(prepared, wavelengths_um=(0.65,))
    assert not frozen.service.glass_expert_acceptance().allowed


def test_one_pool_can_retain_exact_records_from_multiple_sources(frozen):
    source = load_source(owned_source("catalog:b"))
    frozen.connector.materials._finish_source(
        "catalog:b",
        SimpleNamespace(backend=BackendConfig.capture(), document=None),
        SimpleNamespace(status="succeeded", data=source),
    )
    first = frozen.target.pool.candidates[0]
    other = replace(
        first,
        key=replace(first.key, source_id="catalog:b"),
        stamp=source.snapshot.stamps[0],
    )
    target = replace(frozen.target, pool=MaterialPool((first, other)))
    frozen.setup = frozen.service.capture_glass_expert_setup((target,))
    finish_actual(frozen)
    assert frozen.service.glass_expert_acceptance().allowed
    assert set(frozen.service._glass_expert._owner.checked_sources) == {
        "catalog:a",
        "catalog:b",
    }
    assert frozen.service.apply_glass_expert_candidate()


@pytest.mark.parametrize("case", ["mapping", "baseline"])
def test_strategy_rejects_incomplete_mapping_or_wrong_baseline_binding(frozen, case):
    result, _prepared, _request = finish_actual(frozen)
    if case == "mapping":
        result = replace(result, row_mapping=((1, 1),))
        result = replace(
            result,
            proof=replace(
                result.proof, review_fingerprint=proof.review_fingerprint(result)
            ),
        )
    else:
        result = replace(
            result, proof=replace(result.proof, baseline_fingerprint="0" * 64)
        )
    frozen.service._glass_expert.result = result
    check = frozen.service.glass_expert_acceptance()
    assert not check.allowed
    assert ("mapping" if case == "mapping" else "native baseline") in check.message
