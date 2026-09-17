"""Capture native optical state without depending on material files or catalogs.

Payloads contain ordinary native JSON, not executable code or lookup requests.
File-backed materials become owned definitions valid on their declared domains.
Custom evaluators and propagation require an explicit future adapter rather than
silently substituting a built-in model with different optical behavior.
"""

from __future__ import annotations

import json

import numpy as np

import optiland.backend as be
from optiland.materials.abbe import (
    AbbeMaterial,
    AbbeMaterialE,
    AbbePolynomialModel,
    BuchdahlDModel,
    BuchdahlEModel,
)
from optiland.materials.base import BaseMaterial
from optiland.materials.data import DataMaterial
from optiland.materials.ideal import IdealMaterial
from optiland.materials.material import Material
from optiland.materials.material_file import MaterialFile
from optiland.propagation.base import BasePropagationModel
from optiland.propagation.homogeneous import HomogeneousPropagation

_NATIVE_TYPES = (DataMaterial, IdealMaterial, AbbeMaterial, AbbeMaterialE)
_CAPTURE_TYPES = (*_NATIVE_TYPES, Material, MaterialFile)


def _check_methods(instance) -> None:
    """Reject instance-level substitutions of any class-defined behavior."""
    if any(
        name in vars(instance) and callable(getattr(type(instance), name, None))
        for cls in type(instance).__mro__
        for name in vars(cls)
    ):
        raise ValueError("Instance-patched evaluators cannot be captured")


def _check_builtin_material(material: BaseMaterial) -> None:
    if type(material) not in _CAPTURE_TYPES:
        raise ValueError("Unsupported material type for portable optical capture")
    _check_methods(material)
    propagation = material.propagation_model
    if (
        type(propagation) is not HomogeneousPropagation
        or propagation.material is not material
    ):
        raise ValueError("Portable capture requires owned homogeneous propagation")
    _check_methods(propagation)
    if isinstance(material, MaterialFile):
        name = material._n_formula
        method_name = (
            "_" + name.replace(" ", "_")
            if isinstance(name, str) and name.startswith("formula ")
            else "_tabulated_n"
        )
        evaluator = material.formula_map.get(name)
        if name is not None and (
            evaluator is None
            or getattr(evaluator, "__func__", None)
            is not getattr(MaterialFile, method_name, None)
            or getattr(evaluator, "__self__", None) is not material
        ):
            raise ValueError("Custom file-material formulas cannot be captured")


def _check_abbe_model(material: AbbeMaterial | AbbeMaterialE) -> None:
    expected = (
        BuchdahlEModel
        if type(material) is AbbeMaterialE
        else {"polynomial": AbbePolynomialModel, "buchdahl": BuchdahlDModel}.get(
            material.model_name
        )
    )
    model = material.model
    if type(model) is not expected:
        raise ValueError("Custom Abbe models cannot be captured")
    _check_methods(model)
    for name in ("ALPHA", "WAVE_REF", "V1_COEFFS", "V2_COEFFS", "V3_COEFFS"):
        if not hasattr(expected, name):
            continue
        if not np.array_equal(
            be.to_numpy(getattr(model, name, None)),
            be.to_numpy(getattr(expected, name)),
        ):
            raise ValueError("Modified Abbe fitted constants cannot be captured")


def _owned_polynomial(material: AbbeMaterial) -> DataMaterial:
    """Represent the live finite polynomial exactly using shared formula 5."""
    # The existing model owns the index/Abbe-to-coefficient calculation. Reverse
    # its descending coefficient order into formula 5's constant/power pairs;
    # this is a representation change, not a spectral fit or approximation.
    polynomial = be.to_numpy(material.model._get_coefficients()).reshape(-1).tolist()
    coefficients = [polynomial[-1]]
    for power, coefficient in enumerate(reversed(polynomial[:-1]), start=1):
        coefficients.extend((coefficient, power))
    return DataMaterial.from_coefficients(
        "formula 5",
        coefficients,
        name=material.display_name,
        wavelength_range=material.spectral_range("n"),
        metadata={"source_model": material.to_dict()},
    )


def capture_material(material: BaseMaterial) -> bytes:
    """Capture a supported built-in material's live state as canonical JSON.

    Live file coefficients, tables and active thermal state are copied into an
    owned definition without reopening the file. Native ideal and Buchdahl Abbe
    models retain their parameters; modified Buchdahl constants are unsupported.
    Polynomial Abbe models become exact owned polynomial definitions using their
    live fit coefficients, without sampling optical functions or reopening files. All
    captured models must use unmodified homogeneous propagation bound to their
    own material. Unsupported state raises ``ValueError`` without approximation.
    """
    _check_builtin_material(material)
    if isinstance(material, MaterialFile):
        material = material.to_owned_material()
    elif type(material) in (AbbeMaterial, AbbeMaterialE):
        _check_abbe_model(material)
        if type(material.model) is AbbePolynomialModel:
            material = _owned_polynomial(material)
    payload = json.dumps(
        material.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return payload


def _reject_nonfinite(value: str):
    raise ValueError(f"Portable material JSON contains non-finite {value}")


def restore_material(payload: bytes) -> BaseMaterial:
    """Restore supported native JSON with propagation rebound to the new object.

    Only exact built-in owned, ideal and Buchdahl Abbe codecs are accepted. Registry or
    filename based material requests and custom factory registrations are never
    consulted as alternatives, including when an input spoofs a built-in name.
    """
    if not isinstance(payload, bytes):
        raise ValueError("Portable material payload must be JSON bytes")
    data = json.loads(payload, parse_constant=_reject_nonfinite)
    json.dumps(data, allow_nan=False)
    if not isinstance(data, dict):
        raise ValueError("Portable material payload must be an object")
    material_type = data.get("type")
    if not isinstance(material_type, str):
        raise ValueError("Portable material type must be a string")
    expected = {cls.__name__: cls for cls in _NATIVE_TYPES}.get(material_type)
    if expected is None or BaseMaterial._registry.get(data["type"]) is not expected:
        raise ValueError("Unsupported native material type in portable payload")
    if expected is AbbeMaterial and data.get("model") != "buchdahl":
        raise ValueError("Polynomial Abbe payloads require an owned definition")
    if data.get("propagation_model") != {"class": "HomogeneousPropagation"} or (
        BasePropagationModel._registry.get("HomogeneousPropagation")
        is not HomogeneousPropagation
    ):
        raise ValueError("Portable payload requires built-in homogeneous propagation")
    # The shared native codec remains the single source of optical decoding.
    material = BaseMaterial.from_dict(data)
    _check_builtin_material(material)
    return material
