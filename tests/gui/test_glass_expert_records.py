"""Bounded immutable setup admission preserves exact source/definition identity."""

from __future__ import annotations

import pickle
from dataclasses import replace

import pytest

from optiland.materials.properties import MaterialKey, SourceStamp
from optiland.optimization.material_candidates import MaterialPool
from optiland_gui.services import glass_expert_records as records
from optiland_gui.services.glass_expert_records import FrozenDefinition, MaterialTarget
from tests.gui.test_glass_expert_jobs import make_pool, make_setup, prepare


@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b"{",
        b'{"a":NaN}',
        "{}",
        b"\xff",
    ],
)
def test_definition_rejects_nonobject_nonfinite_or_nonbytes(data):
    with pytest.raises(ValueError):
        FrozenDefinition("row", "variable", data)


@pytest.mark.parametrize(
    "identifier,kind", [("", "variable"), ("x" * 201, "operand"), ("a", "unknown")]
)
def test_definition_id_and_kind_are_explicit(identifier, kind):
    with pytest.raises(ValueError):
        FrozenDefinition(identifier, kind, b"{}")


def test_settings_reject_text_in_numeric_controls():
    with pytest.raises(ValueError, match="finite and positive"):
        records.GlassExpertSettings(tol="0.01")


def test_definition_rejects_live_objects_and_setup_rejects_invalid_sections(
    minimal_optic,
):
    with pytest.raises(ValueError, match="plain JSON"):
        FrozenDefinition.from_dict("row", "variable", {"optic": minimal_optic})
    setup = make_setup(minimal_optic)
    assert setup.definitions_key == (
        tuple(row.data for row in setup.variables),
        tuple(row.data for row in setup.operands),
    )
    for change in (
        {"baseline": None},
        {"settings": {}},
        {"variables": ()},
        {"variables": (setup.variables[0], setup.variables[0])},
        {"operands": (replace(setup.operands[0], kind="variable"),)},
        {"targets": (setup.targets[0], setup.targets[0])},
        {"targets": (replace(setup.targets[0], variable_id="absent"),)},
    ):
        with pytest.raises(ValueError):
            replace(setup, **change)
    with pytest.raises(ValueError):
        MaterialTarget("material:0", True, make_pool())
    with pytest.raises(ValueError):
        MaterialTarget("material:0", 0, ())


def two_targets(setup, second_pool):
    extra = FrozenDefinition.from_dict(
        "material:2", "variable", {"type": "material", "surface_number": 2}
    )
    return replace(
        setup,
        variables=(*setup.variables, extra),
        targets=(*setup.targets, MaterialTarget(extra.identifier, 2, second_pool)),
    )


def test_pool_transport_deduplicates_records_without_losing_membership(minimal_optic):
    setup = make_setup(minimal_optic)
    pool = pickle.loads(pickle.dumps(setup.targets[0].pool))
    assert pool.candidates[0] is not setup.targets[0].pool.candidates[0]
    expanded = two_targets(setup, pool)
    assert (
        expanded.targets[0].pool.candidates[0] is expanded.targets[1].pool.candidates[0]
    )
    assert expanded.source_stamps == (SourceStamp("owned", "1"),)
    assert expanded.input_digest != setup.input_digest


def test_conflicting_records_or_source_generations_are_rejected(minimal_optic):
    setup = make_setup(minimal_optic)
    first = setup.targets[0].pool.candidates[0]
    with pytest.raises(ValueError, match="conflicting pool"):
        two_targets(setup, MaterialPool((replace(first, display_name="different"),)))
    changed = replace(
        first,
        key=MaterialKey("owned", "other", "record"),
        stamp=SourceStamp("owned", "2"),
    )
    with pytest.raises(ValueError, match="conflicting captured"):
        two_targets(setup, MaterialPool((changed,)))


def test_candidate_count_and_encoded_transport_overhead_are_bounded(
    minimal_optic, monkeypatch
):
    setup = make_setup(minimal_optic)
    monkeypatch.setattr(records, "MAX_CANDIDATES", 1)
    with pytest.raises(ValueError, match="unique candidates"):
        replace(setup, run_id="limited")
    monkeypatch.setattr(records, "MAX_CANDIDATES", 10_000)
    payload_size = (
        len(setup.baseline.data)
        + len(setup.operand_metadata)
        + sum(len(v.data) for v in (*setup.variables, *setup.operands))
        + sum(len(c.owned_data) for c in setup.targets[0].pool.candidates)
    )
    monkeypatch.setattr(records, "MAX_RUN_BYTES", payload_size)
    with pytest.raises(ValueError, match="run budget"):
        replace(setup, run_id="limited")


def test_prepared_sequences_and_diagnostics_are_owned_and_bounded(minimal_optic):
    prepared = prepare(make_setup(minimal_optic))
    original = list(prepared.baseline_operands)
    warnings = [str(index) for index in range(1001)] + ["x" * 3000]
    owned = replace(prepared, baseline_operands=original, warnings=warnings)
    original.clear()
    warnings.clear()
    assert owned.baseline_operands == prepared.baseline_operands
    assert len(owned.warnings) == 1001
    assert owned.warnings[-1] == "2 additional diagnostics omitted."
    shortened = replace(prepared, warnings=("x" * 3000,))
    assert len(shortened.warnings[0]) <= records.MAX_DIAGNOSTIC
    assert "truncated" in shortened.warnings[0]
    with pytest.raises(ValueError, match="finite real"):
        replace(prepared.baseline_operands[0], value=float("nan"))
    with pytest.raises(ValueError, match="boolean"):
        replace(prepared.baseline_operands[0], within_bounds=1)
    with pytest.raises(ValueError, match="strings"):
        replace(prepared, warnings=(12,))
    with pytest.raises(ValueError, match="strings"):
        replace(prepared, warnings=(["mutable"],))
