"""Portable captures preserve optical state without a registry or source file."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest
import yaml

import optiland.backend as be
from optiland.materials import (
    AbbeMaterial,
    AbbeMaterialE,
    DataMaterial,
    IdealMaterial,
    Material,
    MaterialFile,
)
from optiland.materials.base import BaseMaterial
from optiland.materials.portability import capture_material, restore_material
from optiland.propagation.homogeneous import HomogeneousPropagation


def formula_data(thermal=None):
    data = {
        "DATA": [
            {
                "type": "formula 5",
                "wavelength_range": "0.4 0.8",
                "coefficients": "1.5 0.02 -2",
            },
            {"type": "tabulated k", "data": "0.45 0.001\n0.7 0.003"},
        ],
        "REFERENCE": {"citation": "Captured source", "nested": [1]},
        "COMMENTS": "Exact analytical fixture",
    }
    if thermal is not None:
        data["SPECS"] = {
            "thermal_dispersion": [{"type": "Schott", "coefficients": thermal}],
            "temperature": "20 C",
        }
    return data


def array(value):
    return be.to_numpy(value)


def test_frozen_mapping_has_no_file_dependency_or_metadata_alias(set_test_backend):
    data = formula_data()
    material = MaterialFile.from_data(data, name="missing/source.yml", bounds="raise")
    data["REFERENCE"]["nested"].append(2)
    data["DATA"][0]["coefficients"] = "1.7"
    owned = material.to_owned_material()
    assert owned.name == "source.yml"
    assert owned.bounds == "raise"
    assert owned.metadata["rii"]["REFERENCE"]["nested"] == [1]
    assert owned.metadata["source_file"] == "source.yml"
    assert owned.metadata["reference"] == material.reference_data
    assert owned.spectral_range("n") == (0.4, 0.8)
    assert owned.spectral_range("k") == (0.45, 0.7)
    assert owned.propagation_model.material is owned
    assert material.propagation_model.material is material
    waves = be.array([0.46, 0.55, 0.69])
    np.testing.assert_allclose(array(owned.n(waves)), array(material.n(waves)))
    np.testing.assert_allclose(array(owned.k(waves)), array(material.k(waves)))


def test_capture_uses_live_coefficients_not_source_yaml(set_test_backend, tmp_path):
    path = tmp_path / "live.yml"
    path.write_text(yaml.safe_dump(formula_data()), encoding="utf-8")
    material = MaterialFile(path, bounds="clamp")
    original = capture_material(material)
    material.coefficients = be.array([[1.6], [0.015], [-2.0]])
    material._k = be.array([0.002, 0.004])
    path.unlink()
    captured = capture_material(material)
    assert captured != original
    owned = restore_material(captured)
    np.testing.assert_allclose(array(owned.n(0.55)), array(material.n(0.55)))
    np.testing.assert_allclose(array(owned.k(0.55)), array(material.k(0.55)))
    material.coefficients = be.array([[1.7], [0.03], [-2.0]])
    assert float(array(owned.n(0.55)).item()) == pytest.approx(1.6 + 0.015 / 0.55**2)
    assert owned.bounds == "clamp"
    assert float(array(owned.k(0.3)).item()) == 0.002
    # Formula support becomes explicit on the owned model, even with clamp.
    assert np.isfinite(array(material.n(0.3))).all()
    with pytest.raises(ValueError, match="outside"):
        owned.n(0.3)


def test_in_place_changes_invalidate_capture_fingerprint(set_test_backend):
    material = MaterialFile.from_data(formula_data())
    material.coefficients = be.asarray([[1.5], [0.02], [-2.0]])
    before = capture_material(material)
    material.n(0.55)
    material.coefficients[0, 0] = 1.65
    after = capture_material(material)
    assert before != after
    assert restore_material(after).n(0.55).item() == pytest.approx(
        1.65 + 0.02 / 0.55**2
    )


@pytest.mark.parametrize("bounds", ["clamp", "raise"])
def test_tabular_snapshot_preserves_live_independent_grids(bounds, set_test_backend):
    material = MaterialFile.from_data(
        {
            "DATA": [
                {"type": "tabulated n", "data": "0.4 1.6\n0.8 1.5"},
                {"type": "tabulated k", "data": "0.45 0.001\n0.7 0.003"},
            ]
        },
        bounds=bounds,
    )
    material._n = be.array([1.7, 1.6])
    owned = restore_material(capture_material(material))
    assert owned.definition.dispersion.indices == (1.7, 1.6)
    assert owned.spectral_range("n") == (0.4, 0.8)
    assert owned.spectral_range("k") == (0.45, 0.7)
    np.testing.assert_allclose(array(owned.n(0.6)), array(material.n(0.6)))
    if bounds == "clamp":
        assert float(array(owned.n(0.2)).item()) == 1.7
    else:
        with pytest.raises(ValueError, match="outside"):
            owned.n(0.2)


@pytest.mark.parametrize("thermal", ["1e-6 2e-9 3e-12 4e-8 5e-11 0.1", "0 0 0 0 0 0"])
def test_active_thermal_state_preserved(thermal, set_test_backend):
    material = MaterialFile.from_data(formula_data(thermal))
    owned = restore_material(capture_material(material))
    assert (owned.definition.thermal is None) == thermal.startswith("0 ")
    wave = be.array([0.5, 0.6, 0.7])
    for temperature, pressure in [(20, 1), (45, 0.7), (-5, None)]:
        np.testing.assert_allclose(
            array(owned.n(wave, temperature=temperature, pressure=pressure)),
            array(material.n(wave, temperature=temperature, pressure=pressure)),
            rtol=1e-13,
        )


def test_live_thermal_changes_and_missing_reference_temperature(set_test_backend):
    material = MaterialFile.from_data(formula_data("0 0 0 0 0 0"))
    material.thermdispcoef = be.array([[2e-6], [0], [0], [0], [0], [0]])
    material._t0 = 25.0
    owned = restore_material(capture_material(material))
    assert owned.definition.thermal.coefficients[0] == 2e-6
    assert owned.definition.thermal.reference_temperature_c == 25
    np.testing.assert_allclose(
        array(owned.n(0.55, temperature=60)),
        array(material.n(0.55, temperature=60)),
    )
    material._t0 = None
    assert restore_material(capture_material(material)).definition.thermal is None


def test_native_transmission_metadata_bounds_and_propagation_are_retained(
    set_test_backend,
):
    material = DataMaterial.from_samples(
        [0.4, 0.8],
        [1.6, 1.5],
        name="Independent owned catalog record",
        bounds="clamp",
        metadata={"provenance": {"line": 4, "cost": "catalog value"}},
        extinction={
            "kind": "internal_transmittance",
            "wavelengths_um": [0.4, 0.6, 0.8],
            "values": [0, 0.7, 0.9],
            "thickness_mm": 10,
            "interpolation": "local_quadratic",
        },
        thermal={
            "kind": "constant",
            "coefficients": [2e-6],
            "reference_temperature_c": 20,
        },
    )
    owned = restore_material(capture_material(material))
    assert owned.to_dict() == material.to_dict()
    assert owned is not material
    assert owned.propagation_model.material is owned
    assert material.propagation_model.material is material
    assert float(array(owned.internal_transmittance(0.4, 20)).item()) == 0
    assert float(array(owned.internal_transmittance(0.6, 20)).item()) == pytest.approx(
        0.49
    )
    assert capture_material(owned) == capture_material(material)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: IdealMaterial(1.6, 0.02),
        lambda: AbbeMaterial(1.6, 55, model="polynomial"),
        lambda: AbbeMaterial(1.6, 55, model="buchdahl"),
        lambda: AbbeMaterialE(1.6, 55),
    ],
)
def test_native_live_ideal_and_abbe_parameters(factory, set_test_backend):
    material = factory()
    material.index = be.array([1.7])
    if hasattr(material, "abbe") and not callable(material.abbe):
        material.abbe = be.array([60.0])
    restored = restore_material(capture_material(material))
    if type(material) is AbbeMaterial and material.model_name == "polynomial":
        assert type(restored) is DataMaterial
        assert restored.spectral_range("n") == (0.38, 0.75)
    else:
        assert type(restored) is type(material)
    wave = be.array([0.45, 0.55, 0.65])
    np.testing.assert_allclose(array(restored.n(wave)), array(material.n(wave)))
    np.testing.assert_allclose(array(restored.k(wave)), array(material.k(wave)))
    assert restored.propagation_model.material is restored


def test_exact_catalog_material_copies_live_state_and_identity(set_test_backend):
    material = Material("N-BK7", catalog="schott", match_policy="strict")
    material.coefficients = material.coefficients * 1.001
    owned = restore_material(capture_material(material))
    assert type(owned) is DataMaterial
    assert owned.name == material.name
    assert owned.metadata["catalog_material"]["catalog"] == "schott"
    np.testing.assert_allclose(array(owned.n(0.55)), array(material.n(0.55)))


@pytest.mark.parametrize(
    "method", ["n", "_calculate_n", "to_dict", "_dispersion", "_evaluate_property"]
)
def test_instance_overrides_are_not_silently_dropped(method):
    material = DataMaterial.from_samples([0.4, 0.8], [1.6, 1.5])
    setattr(material, method, lambda *args, **kwargs: 1.0)
    with pytest.raises(ValueError, match="patched"):
        capture_material(material)


def test_subclass_and_custom_propagation_are_rejected():
    class CustomMaterial(IdealMaterial):
        pass

    with pytest.raises(ValueError, match="Unsupported"):
        capture_material(CustomMaterial(1.5))
    material = IdealMaterial(1.5)
    material.propagation_model.propagate = lambda *args: None
    with pytest.raises(ValueError, match="patched"):
        capture_material(material)
    material = IdealMaterial(1.5)
    material.propagation_model = HomogeneousPropagation(IdealMaterial(1.6))
    with pytest.raises(ValueError, match="owned homogeneous"):
        capture_material(material)


def test_custom_file_formula_and_methods_are_rejected():
    material = MaterialFile.from_data(formula_data())
    material.formula_map["formula 5"] = lambda wavelength: 1.5
    with pytest.raises(ValueError, match="Custom file"):
        capture_material(material)
    material = MaterialFile.from_data(formula_data())
    material._nair = lambda *args: 1.0
    with pytest.raises(ValueError, match="patched"):
        material.to_owned_material()


def test_custom_abbe_model_fit_and_instance_methods_are_rejected():
    material = AbbeMaterial(1.6, 60, model="buchdahl")
    material.model.ALPHA = 3
    with pytest.raises(ValueError, match="fitted constants"):
        capture_material(material)
    material.model = object()
    with pytest.raises(ValueError, match="Custom Abbe"):
        capture_material(material)
    material = AbbeMaterialE(1.6, 60)
    material.model.predict_n = lambda wave: 1.6
    with pytest.raises(ValueError, match="patched"):
        capture_material(material)


def test_live_polynomial_fit_is_captured_without_file_io(set_test_backend, monkeypatch):
    import builtins

    polynomial = AbbeMaterial(1.6, 60, model="polynomial")
    polynomial.model._fit_coefficients[0, 0] += 0.1
    wave = be.array([0.38, 0.49, 0.6, 0.75])
    expected = array(polynomial.n(wave))
    before = capture_material(polynomial)

    def no_io(*args, **kwargs):
        raise AssertionError("Capture/restore attempted file I/O")

    monkeypatch.setattr(builtins, "open", no_io)
    monkeypatch.setattr(np, "load", no_io)
    owned = restore_material(capture_material(polynomial))
    np.testing.assert_allclose(array(owned.n(wave)), expected, rtol=2e-14)
    assert owned.metadata["source_model"]["model"] == "polynomial"
    polynomial.model._fit_coefficients[0, 0] += 0.1
    assert capture_material(polynomial) != before
    np.testing.assert_allclose(array(owned.n(wave)), expected, rtol=2e-14)


def test_buchdahl_capture_and_restore_do_not_load_data(monkeypatch):
    import builtins

    models = [AbbeMaterial(1.6, 60, model="buchdahl"), AbbeMaterialE(1.6, 60)]

    def no_io(*args, **kwargs):
        raise AssertionError("Capture/restore attempted file I/O")

    monkeypatch.setattr(builtins, "open", no_io)
    for material in models:
        restored = restore_material(capture_material(material))
        np.testing.assert_allclose(array(restored.n(0.55)), array(material.n(0.55)))


@pytest.mark.parametrize(
    "data",
    [
        {"DATA": []},
        {"DATA": [{"type": "tabulated n", "data": "0.5 1.6"}]},
        {"DATA": [{"type": "tabulated nk", "data": "0.5 1.6 0.01"}]},
    ],
)
def test_no_index_and_single_sample_are_not_fabricated(data):
    with pytest.raises(ValueError):
        capture_material(MaterialFile.from_data(data))


def test_incomplete_extinction_state_is_not_fabricated():
    material = MaterialFile.from_data(formula_data())
    material._k_wavelength = None
    with pytest.raises(ValueError, match="incomplete extinction"):
        capture_material(material)


@pytest.mark.parametrize(
    "factory,args",
    [
        (MaterialFile.from_data, ([],)),
        (MaterialFile.from_data, ({},)),
        (Material.from_data, ({},)),
    ],
)
def test_from_data_rejects_invalid_constructor_inputs(factory, args):
    kwargs = {"name": 4} if args == ({},) and factory.__self__ is MaterialFile else {}
    with pytest.raises(ValueError):
        factory(*args, **kwargs)


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"type":"Material","name":"N-BK7"}',
        b'{"type":"MaterialFile","filename":"missing.yml"}',
        b'{"type":"AbbeMaterial","model":"polynomial","index":1.6,"abbe":60}',
        "not bytes",
    ],
)
def test_restore_cannot_request_file_or_registry_resolution(payload):
    with pytest.raises(ValueError):
        restore_material(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"type":[]}',
        b'{"type":null}',
        b'{"type":"IdealMaterial","index":NaN}',
        b'{"type":"IdealMaterial","index":1e400}',
    ],
)
def test_restore_rejects_nonfinite_data_and_invalid_type_fields(payload):
    with pytest.raises(ValueError):
        restore_material(payload)


def test_restore_rejects_custom_propagation_and_factory_spoofing(monkeypatch):
    data = json.loads(capture_material(IdealMaterial(1.5)))
    data["propagation_model"] = {"class": "Custom"}
    with pytest.raises(ValueError, match="propagation"):
        restore_material(json.dumps(data).encode())
    payload = capture_material(IdealMaterial(1.5))
    monkeypatch.setitem(BaseMaterial._registry, "IdealMaterial", object)
    with pytest.raises(ValueError, match="native material type"):
        restore_material(payload)


def test_file_snapshot_survives_deleted_source_empty_registry_and_no_optional_imports(
    tmp_path,
):
    source = tmp_path / "source.yml"
    source.write_text(yaml.safe_dump(formula_data()), encoding="utf-8")
    material = MaterialFile(source)
    material.coefficients = be.array([[1.7], [0.01], [-2]])
    payload = capture_material(material)
    source.unlink()
    script = """
import sys
import importlib.abc
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'torch', 'PySide6', 'PyQt5', 'PyQt6'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockOptional())
from optiland.materials.registry import MaterialRegistry
from optiland.materials.portability import restore_material
def no_registry():
    raise AssertionError('registry accessed')
MaterialRegistry.instance = no_registry
material = restore_material(sys.stdin.buffer.read())
assert abs(material.n(0.55).item() - (1.7 + 0.01 / 0.55**2)) < 1e-12
assert material.propagation_model.material is material
assert 'PySide6.QtCore' not in sys.modules
print('portable material restored without registry, source, Qt or Torch')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        input=payload,
        capture_output=True,
        env={**os.environ, "MPLBACKEND": "Agg"},
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"restored without registry" in completed.stdout
