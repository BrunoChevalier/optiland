"""Independent numerical coordinates, source identity and immutable query results."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace

import numpy as np
import pytest

import optiland.backend as be
from optiland.materials import (
    AbbeMaterial,
    AbbeMaterialE,
    DataMaterial,
    IdealMaterial,
    Material,
    MaterialFile,
)
from optiland.materials.properties import (
    C_LINE_UM,
    D_LINE_UM,
    F_LINE_UM,
    MaterialKey,
    MaterialQuery,
    MaterialSnapshot,
    MaterialSummary,
    PropertyValue,
    ReferenceConditions,
    SourceStamp,
    describe_material,
    query_materials,
    reconstruct_material,
)

KEY = MaterialKey("catalog:synthetic", "Synthetic", "glass-a")
STAMP = SourceStamp(KEY.source_id, "revision-1")


def test_native_conditions_and_declared_coordinates_survive_lightweight_transport(
    set_test_backend,
):
    material = MaterialFile.from_data(
        {
            "DATA": [{"type": "formula 5", "coefficients": "1.5 0.02 -2"}],
            "SPECS": {
                "n_is_absolute": False,
                "wavelength_is_vacuum": False,
                "temperature": "20.0 °C",
                "nd": 1.55,
                "Vd": -60,
            },
        },
        name="Example",
    )
    result = describe_material(material, KEY)
    assert result.nd.origin == result.vd.origin == "computed"
    assert result.nd.value != result.declared_nd.value
    assert result.vd.value != result.declared_vd.value
    assert result.declared_nd == PropertyValue(
        1.55, "available", origin="declared", source="RII SPECS.nd"
    )
    assert result.declared_vd.value == -60
    assert result.declared_vd.source == "RII SPECS.Vd (d/F/C)"
    conditions = result.conditions
    assert conditions.index_convention == "air-relative"
    assert conditions.wavelength_convention == "air"
    assert conditions.temperature_c == 20
    assert conditions.pressure_atm is None
    assert "RII SPECS.temperature" in conditions.sources
    owned = describe_material(material.to_owned_material(), KEY)
    assert owned.conditions == conditions
    lightweight = replace(owned, owned_data=None)
    assert lightweight.conditions == conditions
    assert lightweight.declared_nd == result.declared_nd
    assert lightweight.declared_vd == result.declared_vd


def test_absolute_vacuum_metadata_does_not_become_air_relative(set_test_backend):
    result = describe_material(
        analytic_material(
            metadata={
                "rii": {
                    "SPECS": {
                        "n_is_absolute": True,
                        "wavelength_is_vacuum": True,
                        "temperature": "25 C",
                    }
                }
            }
        ),
        KEY,
    )
    assert result.conditions.index_convention == "absolute"
    assert result.conditions.wavelength_convention == "vacuum"
    assert result.conditions.temperature_c == 25
    assert result.declared_nd.status == result.declared_vd.status == "missing"


def test_glc_declared_values_are_separate_from_computed_coordinates(set_test_backend):
    material = analytic_material(metadata={"oslo": {"nd": 1.9, "vd": 80}})
    result = describe_material(material, KEY)
    assert result.declared_nd.value == 1.9 and result.declared_vd.value == 80
    assert result.declared_nd.source == "GLC oslo.nd"
    assert result.declared_vd.source == "GLC oslo.vd (d/F/C)"
    assert result.conditions == ReferenceConditions()
    assert not query_materials(
        snapshot(result), MaterialQuery(target_nd=1.9, nd_tolerance=0.001)
    ).matches


@pytest.mark.parametrize("value", [True, [], {}, "nan", "inf", "garbage", 0, -2])
def test_invalid_declared_index_never_overrides_valid_model(value, set_test_backend):
    result = describe_material(
        analytic_material(metadata={"oslo": {"nd": value, "vd": value}}), KEY
    )
    assert result.nd.status == result.vd.status == "available"
    assert result.declared_nd.status == "invalid"
    assert result.declared_nd.source == "GLC oslo.nd"
    if value == -2:
        assert result.declared_vd.value == -2
    else:
        assert result.declared_vd.status == "invalid"


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"rii": []},
        {"rii": {"SPECS": []}},
        {"oslo": []},
        {"oslo": {"nd": None}},
        {"conventions": {"index": "absolute"}},
    ],
)
def test_unknown_metadata_does_not_invent_conditions(metadata, set_test_backend):
    result = describe_material(analytic_material(metadata=metadata), KEY)
    assert result.conditions == ReferenceConditions()
    assert result.declared_nd.status == result.declared_vd.status == "missing"
    abbe_e = describe_material(AbbeMaterialE(1.6, 55), KEY)
    assert abbe_e.declared_nd.status == abbe_e.declared_vd.status == "missing"


@pytest.mark.parametrize("temperature", ["293 K", 20, "-273.15 °C", "1e999 C"])
def test_invalid_or_unknown_conditions_are_explained(temperature, set_test_backend):
    result = describe_material(
        analytic_material(
            metadata={
                "rii": {
                    "SPECS": {
                        "n_is_absolute": "false",
                        "wavelength_is_vacuum": 0,
                        "temperature": temperature,
                    }
                }
            }
        ),
        KEY,
    )
    assert result.conditions.index_convention == "unknown"
    assert result.conditions.wavelength_convention == "unknown"
    assert result.conditions.temperature_c is None
    assert len(result.conditions.notes) == 3


@pytest.mark.parametrize("absolute", [None, False, True])
def test_native_thermal_reference_is_attributed_without_normalizing(
    absolute, set_test_backend
):
    specs = {"temperature": "20 °C"}
    if absolute is not None:
        specs["n_is_absolute"] = absolute
    result = describe_material(
        analytic_material(
            thermal={
                "kind": "schott",
                "coefficients": [1e-6, 0, 0, 0, 0, 0],
                "reference_temperature_c": 40,
            },
            metadata={"rii": {"SPECS": specs}},
        ),
        KEY,
    )
    assert result.conditions.temperature_c == 40
    assert result.conditions.pressure_atm == 1
    assert result.conditions.wavelength_convention == "unknown"
    assert result.conditions.index_convention == (
        "absolute" if absolute else "air-relative"
    )
    assert "Live thermal reference temperature differs" in result.conditions.notes[0]
    if absolute:
        assert "conflicts" in result.conditions.notes[1]
    assert result.nd.value == pytest.approx(1.5 + 0.02 / D_LINE_UM**2)


def test_file_active_thermal_conditions_retain_live_temperature(set_test_backend):
    material = Material("N-BK7", catalog="schott")
    material._t0 = 40
    result = describe_material(material, KEY)
    assert result.conditions.temperature_c == 40
    assert result.conditions.pressure_atm == 1
    assert (
        result.conditions
        == describe_material(material.to_owned_material(), KEY).conditions
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"index_convention": "relative"},
        {"wavelength_convention": "relative"},
        {"temperature_c": float("nan")},
        {"temperature_c": -273.15},
        {"pressure_atm": -1},
        {"pressure_atm": float("inf")},
    ],
)
def test_reference_conditions_validate_quantities(kwargs):
    with pytest.raises(ValueError):
        ReferenceConditions(**kwargs)


def test_reference_conditions_are_immutable():
    sources, notes = ["RII SPECS.temperature"], ["Example"]
    conditions = ReferenceConditions(sources=sources, notes=notes, pressure_atm=0)
    sources.clear()
    notes.clear()
    assert conditions.sources == ("RII SPECS.temperature",)
    assert conditions.notes == ("Example",)
    with pytest.raises(FrozenInstanceError):
        conditions.temperature_c = 20


def test_qualified_catalog_filter_separates_same_catalog_names():
    a = summary()
    b = replace(a, key=MaterialKey("other", a.key.catalog, a.key.record_id))
    data = MaterialSnapshot((STAMP, SourceStamp("other", "1")), (a, b))
    keys = [["other", "Synthetic"]]
    query = MaterialQuery(catalog_keys=keys)
    keys[0][0] = "changed"
    assert query.catalog_keys == (("other", "Synthetic"),)
    assert tuple(m.key for m in query_materials(data, query).matches) == (b.key,)
    assert len(query_materials(data, MaterialQuery(catalog_keys=None)).matches) == 2
    assert not query_materials(data, MaterialQuery(catalog_keys=())).matches
    # Exact qualified identity does not case-fold; legacy catalog labels still do.
    assert not query_materials(
        data, MaterialQuery(catalog_keys=(("other", "synthetic"),))
    ).matches
    assert (
        len(query_materials(data, MaterialQuery(catalogs=("synthetic",))).matches) == 2
    )


@pytest.mark.parametrize("keys", [("ab",), (("a",),), (("a", "b", "c"),), ((1, "b"),)])
def test_invalid_qualified_catalog_filter_is_rejected(keys):
    with pytest.raises(ValueError, match="exact source/catalog"):
        MaterialQuery(catalog_keys=keys)


def analytic_material(**kwargs):
    return DataMaterial.from_coefficients(
        "formula 5", [1.5, 0.02, -2], name="Glass A", **kwargs
    )


def summary(record_id="a", nd=1.5, vd=50.0, **kwargs):
    return MaterialSummary(
        MaterialKey(STAMP.source_id, "Synthetic", record_id),
        "Glass " + record_id,
        PropertyValue(nd, "available"),
        PropertyValue(vd, "available"),
        (0.4, 0.8),
        None,
        "raise",
        False,
        False,
        **kwargs,
    )


def snapshot(*materials):
    return MaterialSnapshot((STAMP,), materials)


def test_reference_coordinates_follow_independent_equation(set_test_backend):
    material = analytic_material(wavelength_range=(0.4, 0.8))
    before = material.to_dict()
    result = describe_material(material, KEY)
    expected_n = 1.5 + 0.02 / 0.5875618**2
    expected_v = (expected_n - 1) / (0.02 / 0.4861327**2 - 0.02 / 0.6562725**2)
    assert result.nd.value == pytest.approx(expected_n)
    assert result.vd.value == pytest.approx(expected_v)
    assert result.nd.status == result.vd.status == "available"
    assert result.nd.origin == "computed"
    assert result.n_range_um == (0.4, 0.8)
    assert result.bounds == "raise"
    assert material.to_dict() == before


def test_negative_abbe_and_constant_index_remain_inspectable(set_test_backend):
    anomalous = DataMaterial.from_samples([0.4, 0.8], [1.54, 1.58])
    result = describe_material(anomalous, KEY)
    expected_nd = 1.5 + 0.1 * D_LINE_UM
    expected_vd = (expected_nd - 1) / (0.1 * (F_LINE_UM - C_LINE_UM))
    assert result.nd.value == pytest.approx(expected_nd)
    assert result.vd.value == pytest.approx(expected_vd)
    assert result.vd.value < 0
    constant = describe_material(IdealMaterial(1.5), KEY)
    assert constant.nd.value == 1.5
    assert constant.vd.value is None
    assert constant.vd.status == "missing"
    assert "zero dispersion" in constant.vd.reason
    assert constant.n_range_um is None
    assert constant.has_extinction_data is None
    assert len(query_materials(snapshot(constant), MaterialQuery()).matches) == 1


def test_abbe_wrapper_is_evaluated_without_calling_its_abbe_value(set_test_backend):
    material = AbbeMaterial(1.6, 60, model="buchdahl")
    assert not callable(material.abbe)
    result = describe_material(material, KEY)
    assert result.nd.status == result.vd.status == "available"
    assert result.nd.value == pytest.approx(1.6)
    assert result.vd.value == pytest.approx(60, rel=0.02)


def test_clamp_policy_does_not_make_reference_lines_supported(set_test_backend):
    material = DataMaterial.from_samples([0.55, 0.65], [1.55, 1.53], bounds="clamp")
    assert be.to_numpy(material.n(F_LINE_UM)).item() == 1.55
    result = describe_material(material, KEY)
    assert result.nd.status == "available"
    assert result.vd.status == "unsupported"
    assert result.bounds == "clamp"
    limited = describe_material(analytic_material(wavelength_range=(0.7, 0.8)), KEY)
    assert limited.nd.status == limited.vd.status == "unsupported"


@pytest.mark.parametrize(
    "value,status",
    [
        (float("nan"), "invalid"),
        (float("inf"), "invalid"),
        (-1.0, "invalid"),
        (0.0, "invalid"),
        (np.array([]), "missing"),
        (np.array([1.5, 1.6]), "unsupported"),
        (1.5 + 0.01j, "unsupported"),
        (np.array([1.5 + 0.01j]), "unsupported"),
    ],
)
def test_invalid_or_nonscalar_model_does_not_create_coordinates(value, status):
    material = IdealMaterial(1.5)
    material.n = lambda wavelength: value
    result = describe_material(material, KEY)
    assert result.nd.status == result.vd.status == status
    assert result.nd.value is result.vd.value is None


@pytest.mark.parametrize(
    "error,status",
    [
        (NotImplementedError(), "unsupported"),
        (ValueError("invalid model"), "invalid"),
        (TypeError(), "invalid"),
    ],
)
def test_index_evaluation_failure_is_explicit(error, status):
    material = IdealMaterial(1.5)

    def fail(wavelength):
        raise error

    material.n = fail
    result = describe_material(material, KEY)
    assert result.nd.status == status
    assert result.nd.reason


def test_overflowing_abbe_ratio_is_unavailable():
    material = IdealMaterial(1.5)
    values = {D_LINE_UM: 1e308, F_LINE_UM: 2e-308, C_LINE_UM: 1e-308}
    material.n = lambda wavelength: values[wavelength]
    result = describe_material(material, KEY)
    assert result.nd.status == "available"
    assert result.vd.status == "invalid"


def test_extinction_presence_and_its_independent_support(set_test_backend):
    material = analytic_material(
        wavelength_range=(0.4, 0.8),
        extinction={
            "kind": "tabulated_k",
            "wavelengths_um": [0.5, 0.7],
            "values": [0.0, 0.0],
        },
    )
    measured = describe_material(material, KEY)
    missing = describe_material(analytic_material(), KEY)
    assert measured.has_extinction_data is True
    assert measured.has_transmission_data is False
    assert measured.k_range_um == (0.5, 0.7)
    assert missing.has_extinction_data is False
    assert missing.k_range_um is None
    assert be.to_numpy(material.k(0.6)).item() == 0
    assert be.to_numpy(analytic_material().k(0.6)).item() == 0


def test_owned_reconstruction_keeps_thermal_transmission_and_propagation(
    set_test_backend,
):
    material = analytic_material(
        bounds="clamp",
        metadata={"provenance": ["fictional catalog"]},
        thermal={
            "kind": "constant",
            "coefficients": [2e-6],
            "reference_temperature_c": 20,
        },
        extinction={
            "kind": "internal_transmittance",
            "wavelengths_um": [0.4, 0.6, 0.8],
            "values": [0.0, 0.8, 0.9],
            "thickness_mm": 5.0,
            "interpolation": "local_quadratic",
        },
    )
    record = describe_material(material, KEY)
    assert isinstance(record.owned_data, bytes)
    assert record.has_transmission_data is True
    restored = reconstruct_material(record)
    assert restored.to_dict() == material.to_dict()
    assert restored is not material
    assert restored.propagation_model is not material.propagation_model
    assert restored.propagation_model.material is restored
    assert be.to_numpy(restored.internal_transmittance(0.4, 10)).item() == 0
    material.name = "Changed after capture"
    assert reconstruct_material(record).name == "Glass A"
    modified = json.loads(record.owned_data)
    modified["metadata"]["provenance"].clear()
    assert reconstruct_material(record).metadata["provenance"] == ["fictional catalog"]
    with pytest.raises(FrozenInstanceError):
        record.display_name = "changed"


def test_file_material_is_inspectable_but_not_falsely_portable(set_test_backend):
    record = describe_material(
        Material("N-BK7", catalog="schott", match_policy="strict"), KEY
    )
    assert record.nd.status == record.vd.status == "available"
    assert record.owned_data is None
    assert record.has_extinction_data is True
    with pytest.raises(ValueError, match="no portable owned"):
        reconstruct_material(record)
    wrong_type = replace(record, owned_data=b'{"type":"Material","name":"N-BK7"}')
    with pytest.raises(ValueError, match="Unsupported native material type"):
        reconstruct_material(wrong_type)


def test_custom_behavior_does_not_claim_native_reconstruction():
    material = analytic_material()
    material.n = lambda wavelength: np.array([1.6])
    assert describe_material(material, KEY).owned_data is None
    material = analytic_material()
    material.propagation_model.propagate = lambda rays, t: None
    assert describe_material(material, KEY).owned_data is None
    material = analytic_material()
    material._dispersion = lambda wavelength: np.array([1.9])
    record = describe_material(material, KEY)
    assert record.nd.value == 1.9
    assert record.owned_data is None
    material = analytic_material()
    material.propagation_model.material = IdealMaterial(1.0)
    assert describe_material(material, KEY).owned_data is None


def test_independent_tolerances_signed_deltas_and_identity_ties():
    # Binary-exact values deliberately exercise inclusive boundaries, not rounding.
    records = snapshot(
        summary("z", 1.625, 48),
        summary("a", 1.375, 52),
        summary("middle", 1.5, 50),
        summary("bad-n", 1.75, 50),
        summary("bad-v", 1.5, 53),
    )
    result = query_materials(
        records,
        MaterialQuery(target_nd=1.5, nd_tolerance=0.125, target_vd=50, vd_tolerance=2),
        request_id=17,
    )
    assert [match.key.record_id for match in result.matches] == ["middle", "a", "z"]
    assert result.matches[1].delta_nd == -0.125
    assert result.matches[1].delta_vd == 2
    assert result.matches[2].delta_nd == 0.125
    assert result.matches[2].delta_vd == -2
    assert result.matches[1].distance == pytest.approx(math.sqrt(2))
    assert dict(result.excluded_counts) == {"nd_tolerance": 1, "vd_tolerance": 1}
    assert result.request_id == 17
    assert result.stamps is records.stamps


def test_single_coordinate_and_negative_abbe_matching():
    records = snapshot(summary("a", vd=-10), summary("b", vd=10))
    result = query_materials(records, MaterialQuery(target_vd=-10, vd_tolerance=1))
    assert [match.key.record_id for match in result.matches] == ["a"]
    assert result.matches[0].delta_nd is None
    assert result.matches[0].delta_vd == result.matches[0].distance == 0


def test_decimal_tolerance_boundaries_are_inclusive_without_widening():
    records = snapshot(summary("boundary", 1.6), summary("outside", 1.6000000001))
    result = query_materials(records, MaterialQuery(target_nd=1.5, nd_tolerance=0.1))
    assert [match.key.record_id for match in result.matches] == ["boundary"]
    assert dict(result.excluded_counts) == {"nd_tolerance": 1}


def test_source_catalog_name_and_explicit_unknown_support_policy():
    own_stamp = SourceStamp("document:one", "material-state-1")
    unknown = replace(summary("b"), n_range_um=None)
    own = replace(summary("c"), key=MaterialKey(own_stamp.source_id, "Owned", "c"))
    records = MaterialSnapshot((STAMP, own_stamp), (summary(), unknown, own))
    result = query_materials(
        records,
        MaterialQuery(
            source_ids=(STAMP.source_id,),
            catalogs=("sYnThEtIc",),
            name=" GLASS B ",
            wavelength_range_um=(0.45, 0.75),
            unknown_policy="include",
        ),
    )
    assert [match.key.record_id for match in result.matches] == ["b"]
    assert result.stamps == (STAMP, own_stamp)
    excluded = query_materials(records, MaterialQuery(wavelength_range_um=(0.45, 0.75)))
    assert dict(excluded.excluded_counts) == {"unknown_support": 1}
    wider = query_materials(records, MaterialQuery(wavelength_range_um=(0.3, 0.9)))
    assert dict(wider.excluded_counts) == {
        "unknown_support": 1,
        "wavelength_support": 2,
    }
    assert not query_materials(records, MaterialQuery(catalogs=("absent",))).matches


def test_missing_coordinates_remain_browsable_but_cannot_match():
    records = snapshot(
        replace(summary(), vd=PropertyValue(None, "missing", reason="undefined"))
    )
    assert len(query_materials(records, MaterialQuery()).matches) == 1
    result = query_materials(records, MaterialQuery(target_vd=50, vd_tolerance=1))
    assert not result.matches
    assert result.excluded_counts == (("vd_missing", 1),)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_nd": 1.5},
        {"nd_tolerance": 0.01},
        {"target_vd": 50},
        {"vd_tolerance": 1},
        {"target_nd": 1.5, "nd_tolerance": 0},
        {"target_nd": 1.5, "nd_tolerance": -1},
        {"target_nd": math.nan, "nd_tolerance": 1},
        {"target_vd": 50, "vd_tolerance": math.inf},
        {"target_nd": 0, "nd_tolerance": 0.1},
        {"unknown_policy": "guess"},
        {"wavelength_range_um": (0, 0.8)},
        {"wavelength_range_um": (0.8, 0.4)},
        {"wavelength_range_um": (0.4, math.inf)},
        {"wavelength_range_um": (0.4,)},
        {"source_ids": (["not-a-string"],)},
    ],
)
def test_invalid_query_constraints_fail_before_filtering(kwargs):
    with pytest.raises(ValueError):
        MaterialQuery(**kwargs)


def test_snapshot_freezes_inputs_and_requires_unique_stamped_identities():
    stamps, materials = [STAMP], [summary()]
    captured = MaterialSnapshot(stamps, materials)
    stamps.clear()
    materials.clear()
    assert captured.stamps == (STAMP,)
    assert len(captured.materials) == 1
    with pytest.raises(ValueError, match="duplicate material"):
        snapshot(summary(), summary())
    with pytest.raises(ValueError, match="one generation"):
        MaterialSnapshot((STAMP, STAMP), ())
    with pytest.raises(ValueError, match="matching source"):
        MaterialSnapshot((), (summary(),))


def test_summary_copies_mutable_ranges_and_byte_payload():
    limits, payload = [0.4, 0.8], bytearray(b'{"type":"DataMaterial"}')
    record = replace(
        summary(), n_range_um=limits, k_range_um=limits, owned_data=payload
    )
    limits[0] = 0.7
    payload[0] = ord("!")
    assert record.n_range_um == record.k_range_um == (0.4, 0.8)
    assert record.owned_data == b'{"type":"DataMaterial"}'
    assert isinstance(record.owned_data, bytes)
    with pytest.raises(ValueError, match="JSON bytes"):
        replace(record, owned_data={"type": "DataMaterial"})


def test_identity_and_generation_are_immutable_strings():
    with pytest.raises(ValueError, match="strings"):
        MaterialKey(["mutable"], "catalog", "record")
    with pytest.raises(ValueError, match="strings"):
        SourceStamp("source", ["mutable"])


@pytest.mark.parametrize(
    "value,status",
    [(None, "available"), (math.nan, "available"), (1.0, "missing"), (None, "unknown")],
)
def test_property_state_never_substitutes_a_number_for_missing_data(value, status):
    with pytest.raises(ValueError):
        PropertyValue(value, status)


def test_numpy_descriptor_and_query_import_without_qt_or_torch():
    script = r"""
import importlib.abc, importlib.util, sys
original_find = importlib.util.find_spec
def find_without_torch(name, *args, **kwargs):
    if name == 'torch' or name.startswith('torch.'):
        return None
    return original_find(name, *args, **kwargs)
importlib.util.find_spec = find_without_torch
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'PySide6', 'PyQt5', 'PyQt6'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockOptional())
from optiland.materials import DataMaterial
from optiland.materials.properties import *
record = describe_material(
    DataMaterial.from_samples([.4,.8], [1.6,1.5]), MaterialKey('owned', '', 'one'))
result = query_materials(
    MaterialSnapshot((SourceStamp('owned','1'),), (record,)), MaterialQuery())
assert len(result.matches) == 1
assert record.nd.status == 'available'
blocked = {'torch','PySide6','PyQt5','PyQt6'}
assert not any(name.split('.')[0] in blocked for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "MPLBACKEND": "Agg"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
