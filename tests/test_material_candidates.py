"""Public exact-pool and native categorical-variable contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

import optiland.backend as be
from optiland.materials import AbbeMaterial, DataMaterial, IdealMaterial
from optiland.materials.portability import capture_material
from optiland.materials.properties import (
    C_LINE_UM,
    D_LINE_UM,
    F_LINE_UM,
    MaterialKey,
    ReferenceConditions,
    SourceStamp,
    describe_material,
)
from optiland.optic import Optic
from optiland.optimization import (
    MaterialCandidate,
    MaterialPool,
    MaterialReference,
    OptimizationProblem,
    prepare_material_candidate,
    validate_material_spectrum,
)
from optiland.optimization.scaling.linear import LinearScaler
from optiland.optimization.variable.material import MaterialVariable

KEY = MaterialKey("source:a", "catalog", "exact-record")
STAMP = SourceStamp(KEY.source_id, "generation-1")
WAVES = (F_LINE_UM, D_LINE_UM, C_LINE_UM)


def material(offset=1.5, dispersion=0.005, **kwargs):
    return DataMaterial.from_coefficients(
        "formula 5",
        [offset, dispersion, -2],
        name="Same name",
        wavelength_range=(0.4, 0.8),
        bounds="raise",
        **kwargs,
    )


def candidate(value=None, key=KEY, stamp=STAMP):
    value = material() if value is None else value
    summary = describe_material(value, key)
    summary = replace(summary, owned_data=capture_material(value))
    return prepare_material_candidate(summary, stamp, wavelengths_um=WAVES)


def optic(value):
    lens = Optic()
    lens.surfaces.add(index=0, radius=float("inf"), thickness=float("inf"))
    lens.surfaces.add(index=1, radius=50, thickness=5, material=value, is_stop=True)
    lens.surfaces.add(index=2, radius=-50, thickness=45)
    lens.surfaces.add(index=3)
    lens.set_aperture("EPD", 10)
    lens.fields.set_type("angle")
    lens.fields.add(y=0)
    lens.wavelengths.add(D_LINE_UM, is_primary=True)
    lens.updater.update()
    return lens


def test_same_names_and_equal_coordinates_retain_exact_keys(set_test_backend):
    first = candidate()
    key = MaterialKey("source:b", "catalog", "exact-record")
    second = candidate(key=key, stamp=SourceStamp(key.source_id, "generation-2"))
    pool = MaterialPool((first, second))
    assert first.display_name == second.display_name
    assert (first.nd, first.vd) == (second.nd, second.vd)
    assert pool.resolve(KEY) is first
    assert pool.resolve(key) is second
    with pytest.raises(ValueError, match="outside"):
        pool.resolve("Same name")


def test_public_pool_json_round_trip_preserves_optics_and_qualification(
    set_test_backend,
):
    original = candidate()
    data = json.loads(json.dumps(MaterialPool((original,)).to_dict(), allow_nan=False))
    restored = MaterialPool.from_dict(data).resolve(KEY)
    assert restored == original
    assert restored.fingerprint == hashlib.sha256(original.owned_data).hexdigest()
    left, right = restored.restore(), restored.restore()
    assert left is not right
    assert left.propagation_model.material is left
    assert right.propagation_model.material is right
    assert capture_material(left) == capture_material(right) == original.owned_data
    left.name = "Changed"
    assert right.name == original.display_name
    assert restored.validate(WAVES) == original.validate(WAVES)


def test_pool_deeply_copies_sequence_and_candidate_bytes():
    original = candidate()
    buffer = bytearray(original.owned_data)
    copied = replace(original, owned_data=buffer)
    entries = [copied]
    pool = MaterialPool(entries)
    entries.clear()
    buffer[0] = ord("x")
    assert pool.candidates == (original,)
    assert copied.owned_data == original.owned_data
    with pytest.raises(FrozenInstanceError):
        copied.nd = 2.0


@pytest.mark.parametrize("kind", ["empty", "duplicate", "generation", "not_candidate"])
def test_pool_rejects_ambiguous_or_empty_membership(kind):
    first = candidate()
    choices = {
        "empty": (),
        "duplicate": (first, first),
        "generation": (
            first,
            replace(
                first,
                key=replace(KEY, record_id="other"),
                stamp=replace(STAMP, generation="other"),
            ),
        ),
        "not_candidate": ("name",),
    }
    with pytest.raises(ValueError):
        MaterialPool(choices[kind])


@pytest.mark.parametrize(
    "field,value",
    [
        ("nd", True),
        ("nd", 0),
        ("nd", -1),
        ("nd", float("nan")),
        ("vd", False),
        ("vd", float("inf")),
        ("vd", "50"),
        ("key", "key"),
        ("stamp", "stamp"),
        ("stamp", SourceStamp("wrong-source", "1")),
        ("display_name", None),
        ("conditions", None),
        ("owned_data", b""),
        ("owned_data", "{}"),
    ],
)
def test_candidate_metadata_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        replace(candidate(), **{field: value})


@pytest.mark.parametrize(
    "record",
    [
        None,
        {},
        {"candidates": []},
        {"candidates": "bad"},
        {"candidates": [], "extra": 1},
    ],
)
def test_invalid_public_pool_records_fail_without_lookup(record):
    with pytest.raises(ValueError):
        MaterialPool.from_dict(record)


@pytest.mark.parametrize("tamper", ["extra", "key", "nonfinite", "native"])
def test_invalid_public_candidate_records_fail(tamper):
    record = candidate().to_dict()
    if tamper == "extra":
        record["extra"] = True
    elif tamper == "key":
        record["key"] = {"unknown": "field"}
    elif tamper == "nonfinite":
        record["material"]["metadata"]["invalid"] = float("inf")
    else:
        record["material"] = {"type": "Material", "name": "N-BK7"}
    with pytest.raises(ValueError):
        restored = MaterialCandidate.from_dict(record)
        restored.restore()


def test_deserialization_does_not_certify_numeric_or_condition_metadata(
    set_test_backend,
):
    record = candidate().to_dict()
    record["nd"] += 0.1
    loaded = MaterialCandidate.from_dict(record)
    with pytest.raises(ValueError, match="incorrect model nd"):
        loaded.validate(WAVES)
    wrong_conditions = replace(
        candidate(), conditions=ReferenceConditions(temperature_c=25)
    )
    with pytest.raises(ValueError, match="conditions"):
        wrong_conditions.validate(WAVES)


def test_spaced_source_json_is_canonicalized_without_changing_identity(
    set_test_backend,
):
    original = candidate()
    spaced = json.dumps(json.loads(original.owned_data), indent=2).encode()
    summary = replace(describe_material(original.restore(), KEY), owned_data=spaced)
    normalized = prepare_material_candidate(summary, STAMP, wavelengths_um=WAVES)
    assert normalized == original
    assert normalized.fingerprint == original.fingerprint
    assert normalized.key == KEY and normalized.stamp == STAMP
    assert capture_material(normalized.restore()) == original.owned_data


@pytest.mark.parametrize(
    "payload", [b"not JSON", b"[]", b"null", b"NaN", b'{"number":Infinity}', b'"text"']
)
def test_invalid_native_json_is_rejected_during_structural_capture(payload):
    with pytest.raises(ValueError):
        replace(candidate(), owned_data=payload)


def test_native_restore_rejects_state_the_codec_would_discard():
    record = candidate().to_dict()
    record["material"]["unpersisted_state"] = {"custom": 1}
    loaded = MaterialCandidate.from_dict(record)
    with pytest.raises(ValueError, match="canonical native"):
        loaded.restore()


def test_negative_abbe_is_valid_but_zero_dispersion_is_unavailable(set_test_backend):
    anomalous = candidate(material(dispersion=-0.005))
    assert anomalous.vd < 0
    anomalous.validate(WAVES)
    summary = describe_material(material(dispersion=0), KEY)
    with pytest.raises(ValueError, match="computed vd"):
        prepare_material_candidate(summary, STAMP, wavelengths_um=WAVES)
    with pytest.raises(ValueError, match="portable"):
        prepare_material_candidate(
            replace(summary, owned_data=None), STAMP, wavelengths_um=WAVES
        )


def test_known_support_is_strict_even_with_clamp(set_test_backend):
    value = DataMaterial.from_samples([0.4, 0.8], [1.53, 1.50], bounds="clamp")
    prepared = candidate(value)
    with pytest.raises(ValueError, match="outside known n support"):
        prepared.validate((0.3,))


def test_unknown_extinction_and_analytic_support_are_attributed(set_test_backend):
    assert any(
        "unknown" in message and "zero-k" in message
        for message in candidate().validate(WAVES)
    )
    assert validate_material_spectrum(IdealMaterial(1.5), WAVES) == (
        "n support is analytic.",
        "k support is analytic.",
    )
    unknown = DataMaterial.from_coefficients("formula 5", [1.5, 0.005, -2])
    assert any(
        "n support is unknown" in message
        for message in validate_material_spectrum(unknown, WAVES)
    )


def test_measured_k_has_independent_domain_and_no_unknown_warning(set_test_backend):
    value = material(
        extinction={
            "kind": "tabulated_k",
            "wavelengths_um": [0.5, 0.7],
            "values": [0.0, 0.01],
        }
    )
    summary = describe_material(value, KEY)
    # The k table covers d but not F; its native zero is measured, not missing.
    prepared = prepare_material_candidate(summary, STAMP, wavelengths_um=(D_LINE_UM,))
    assert prepared.validate((0.5,)) == ()
    with pytest.raises(ValueError, match="outside known k support"):
        prepared.validate(WAVES)


def test_native_thermal_and_transmission_survive_pool_json(set_test_backend):
    value = material(
        thermal={
            "kind": "constant",
            "coefficients": [2e-6],
            "reference_temperature_c": 20,
        },
        extinction={
            "kind": "internal_transmittance",
            "wavelengths_um": [0.4, 0.6, 0.8],
            "values": [0, 0.7, 0.85],
            "thickness_mm": 5.0,
            "interpolation": "local_quadratic",
        },
    )
    prepared = candidate(value)
    loaded = MaterialCandidate.from_dict(json.loads(json.dumps(prepared.to_dict())))
    restored = loaded.restore()
    assert restored.to_dict() == value.to_dict()
    assert loaded.conditions.temperature_c == 20
    assert be.to_numpy(restored.internal_transmittance(0.4, 5)).item() == 0
    assert be.to_numpy(
        restored.internal_transmittance(0.6, 10)
    ).item() == pytest.approx(0.49)
    assert loaded.validate(WAVES) == ()


@pytest.mark.parametrize(
    "index,k,quantity",
    [
        (0, 0, "n"),
        (-1, 0, "n"),
        (float("nan"), 0, "n"),
        (1.5, -0.1, "k"),
        (1.5, float("inf"), "k"),
    ],
)
def test_invalid_actual_optical_values_fail(index, k, quantity, set_test_backend):
    with pytest.raises(ValueError, match=f"Material {quantity} is invalid"):
        validate_material_spectrum(IdealMaterial(index, k), WAVES)


@pytest.mark.parametrize("waves", [(True,), (0,), (-1,), (float("nan"),), ("0.5",)])
def test_invalid_validation_wavelengths_rejected(waves):
    with pytest.raises(ValueError, match="positive wavelengths"):
        candidate().validate(waves)


def test_cancellation_propagates_before_native_evaluation():
    class Cancelled(Exception):
        pass

    calls = []

    def checkpoint():
        calls.append(True)
        raise Cancelled()

    with pytest.raises(Cancelled):
        candidate().validate(WAVES, checkpoint=checkpoint)
    assert calls == [True]


def test_typed_variable_keeps_reference_and_restores_exact_native_identity(
    set_test_backend,
):
    reference_material = AbbeMaterial(1.52, 60, model="polynomial")
    lens = optic(reference_material)
    before = capture_material(reference_material)
    pool = MaterialPool((candidate(),))
    behavior = MaterialVariable(lens, 1, pool)
    reference = behavior.get_value()
    assert isinstance(reference, MaterialReference)
    assert lens.surfaces[1].material_post is reference_material
    assert capture_material(lens.surfaces[1].material_post) == before
    behavior.update_value(KEY)
    assert behavior.get_value() == KEY
    assert (
        capture_material(lens.surfaces[1].material_post) == pool.resolve(KEY).owned_data
    )
    behavior.update_value(reference)
    assert lens.surfaces[1].material_post is reference_material
    assert behavior.get_value() is reference
    assert capture_material(reference_material) == before


def test_unknown_key_and_foreign_reference_fail_before_mutation(set_test_backend):
    lens = optic(material(offset=1.6))
    behavior = MaterialVariable(lens, 1, MaterialPool((candidate(),)))
    before = lens.surfaces[1].material_post
    for invalid in (replace(KEY, record_id="absent"), replace(behavior.reference)):
        with pytest.raises(ValueError, match="outside"):
            behavior.update_value(invalid)
        assert lens.surfaces[1].material_post is before


def test_native_edit_invalidates_reported_assignment(set_test_backend):
    lens = optic(material(offset=1.6))
    behavior = MaterialVariable(lens, 1, MaterialPool((candidate(),)))
    behavior.update_value(KEY)
    lens.surfaces[1].material_post.name = "Changed exact native name"
    with pytest.raises(ValueError, match="no longer matches"):
        behavior.get_value()


def test_same_key_assignments_do_not_share_mutable_material_instances(set_test_backend):
    first, second = optic(material()), optic(material())
    pool = MaterialPool((candidate(),))
    a, b = MaterialVariable(first, 1, pool), MaterialVariable(second, 1, pool)
    a.update_value(KEY)
    b.update_value(KEY)
    assert first.surfaces[1].material_post is not second.surfaces[1].material_post
    first.surfaces[1].material_post.name = "Changed"
    assert b.get_value() == KEY


def test_wrapper_preserves_categorical_key_and_rejects_numeric_scaler(set_test_backend):
    lens = optic(material(offset=1.6))
    pool = MaterialPool((candidate(),))
    with pytest.raises(ValueError, match="identity scaling"):
        MaterialVariable(lens, 1, pool, scaler=LinearScaler())
    problem = OptimizationProblem()
    problem.add_variable(lens, "material", surface_number=1, glass_selection=pool)
    variable = problem.variables[0]
    assert isinstance(variable.value, MaterialReference)
    variable.update(KEY)
    assert variable.value == KEY
    variable.reset()
    assert isinstance(variable.value, MaterialReference)
