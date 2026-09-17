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
from optiland.materials import AbbeMaterial, DataMaterial, IdealMaterial, Material
from optiland.materials.properties import (
    C_LINE_UM,
    D_LINE_UM,
    F_LINE_UM,
    MaterialKey,
    MaterialQuery,
    MaterialSnapshot,
    MaterialSummary,
    PropertyValue,
    SourceStamp,
    describe_material,
    query_materials,
    reconstruct_material,
)

KEY = MaterialKey("catalog:synthetic", "Synthetic", "glass-a")
STAMP = SourceStamp(KEY.source_id, "revision-1")


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
