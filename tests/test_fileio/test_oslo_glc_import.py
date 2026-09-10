"""Catalog resolution policy and portable imported optics."""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from unittest.mock import patch

import pytest

import optiland.backend as be
from optiland.fileio import load_oslo_file, save_oslo_file
from optiland.materials import DataMaterial, IdealMaterial
from optiland.optic import Optic
from optiland.rays import RealRays
from tests.test_fileio.test_oslo_glc import catalog, glc_record


@pytest.mark.parametrize("strict", [False, True])
def test_selected_catalog_wins_over_builtin(
    tmp_path, lens_file, strict, set_test_backend
):
    path = catalog(tmp_path, [glc_record(name="N-BK7")])
    with patch(
        "optiland.fileio.oslo.reader.converter.Material",
        side_effect=AssertionError("no built-in lookup"),
    ):
        optic = load_oslo_file(
            lens_file(surface="GLA n-bk7"), strict=strict, glass_catalogs=[path]
        )
    m = optic.surfaces[1].material_post
    assert isinstance(m, DataMaterial)
    assert m.n(0.5).item() == pytest.approx(1.4 + 0.002 / 0.5 + 0.0001 / 0.5**3.5)


@pytest.mark.parametrize("paths", [None, []])
def test_strict_empty_list_rejects_even_known_builtin(lens_file, paths):
    with pytest.raises(ValueError, match="selected GLC catalogs: N-BK7"):
        load_oslo_file(
            lens_file(surface="GLA N-BK7"), glass_catalogs=paths, strict=True
        )


def test_strict_lists_every_missing_name(lens_file):
    with pytest.raises(ValueError, match="selected GLC catalogs: GLASS_A, GLASS_B"):
        load_oslo_file(
            lens_file(surface="GLA GLASS_B", second="GLA GLASS_A"), strict=True
        )


@pytest.mark.parametrize("paths", [None, []])
def test_permissive_empty_list_warns_before_fallback(lens_file, paths):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        optic = load_oslo_file(lens_file(surface="GLA N-BK7"), glass_catalogs=paths)
    assert "no definition in selected GLC catalogs" in str(caught[0].message)
    assert optic.surfaces[1].material_post.n(0.55).item() > 1.5


def test_approximation_diagnostics_are_retained(lens_file):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_oslo_file(lens_file(surface="GLA BAF13"))
    messages = [str(w.message) for w in caught]
    assert any("no definition in selected GLC" in m for m in messages)
    assert any("approximate historical Abbe" in m for m in messages)


@pytest.mark.parametrize("strict", [False, True])
def test_explicit_binding_wins_and_direct_indices_win_over_binding(
    tmp_path, lens_file, strict
):
    paths = [
        catalog(tmp_path, [glc_record()], name="a.glc"),
        catalog(tmp_path, [glc_record(dispersion="3 3 1.7 0 0")], name="b.glc"),
    ]
    overrides = {"sample_a": IdealMaterial(1.8)}
    optic = load_oslo_file(
        lens_file(surface="GLA SAMPLE_A", second="GLA SAMPLE_A 1.6"),
        strict=strict,
        glass_catalogs=paths,
        material_overrides=overrides,
    )
    assert optic.surfaces[1].material_post.n(0.5).item() == 1.8
    assert optic.surfaces[2].material_post.n(0.5).item() == 1.6


@pytest.mark.parametrize("strict", [False, True])
def test_malformed_selected_data_never_falls_back(tmp_path, lens_file, strict):
    path = catalog(tmp_path, [glc_record(name="N-BK7", dispersion="9 0")])
    with pytest.raises(ValueError, match="synthetic.glc:2: N-BK7: unsupported"):
        load_oslo_file(
            lens_file(surface="GLA N-BK7"), glass_catalogs=[path], strict=strict
        )


def test_air_mirrors_and_direct_data_need_no_catalog(lens_file):
    optic = load_oslo_file(lens_file(second="RFL"), strict=True, glass_catalogs=[])
    assert optic.surfaces[1].material_post.n(0.55).item() == 1.5
    assert optic.surfaces[2].interaction_model.is_reflective


def test_configurations_evaluate_coefficients_at_selected_wavelengths(
    tmp_path, lens_file, set_test_backend
):
    path = catalog(tmp_path)
    lens = lens_file(surface="GLA SAMPLE_A", footer="CFG NEW; WV1 2 .7; END")
    first = load_oslo_file(lens, glass_catalogs=[path], strict=True)
    second = load_oslo_file(lens, glass_catalogs=[path], strict=True, configuration=2)
    assert second.primary_wavelength == pytest.approx(0.7)
    assert (
        first.surfaces[1].material_post.to_dict()
        == second.surfaces[1].material_post.to_dict()
    )
    assert second.surfaces[1].material_post.n(0.7).item() == pytest.approx(
        1.4 + 0.002 / 0.7 + 0.0001 / 0.7**3.5
    )


def test_native_roundtrip_after_catalog_deletion_in_fresh_process(tmp_path, lens_file):
    path = catalog(tmp_path, [glc_record(transmission="1 3 .4 .7 .5 .75 .8 .99")])
    optic = load_oslo_file(
        lens_file(surface="GLA SAMPLE_A"), glass_catalogs=[path], strict=True
    )
    saved = tmp_path / "optic.json"
    saved.write_text(json.dumps(optic.to_dict()))
    path.unlink()
    restored = Optic.from_dict(json.loads(saved.read_text()))
    assert (
        restored.surfaces[1].material_post.to_dict()
        == optic.surfaces[1].material_post.to_dict()
    )
    script = """
import json, sys
from optiland.optic import Optic
from optiland.materials.material_file import MaterialFile
def no_external_files(*args):
    raise AssertionError('Unexpected material file dependency')
MaterialFile._read_file = no_external_files
optic = Optic.from_dict(json.load(open(sys.argv[1])))
m = optic.surfaces[1].material_post
print(json.dumps({'n': m.n(.5).item(), 't': m.internal_transmittance(.5, 10).item()}))
"""
    run = subprocess.run(
        [sys.executable, "-c", script, str(saved)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(run.stdout) == pytest.approx(
        {"n": 1.4 + 0.002 / 0.5 + 0.0001 / 0.5**3.5, "t": 0.75**2}
    )
    with pytest.raises(NotImplementedError, match="native JSON"):
        save_oslo_file(restored, tmp_path / "export.len")


def test_imported_transmission_drives_native_propagation_after_reload(
    tmp_path, lens_file, set_test_backend
):
    path = catalog(tmp_path, [glc_record(transmission="1 3 .4 .7 .5 .75 .8 .99")])
    optic = load_oslo_file(
        lens_file(surface="GLA SAMPLE_A"), glass_catalogs=[path], strict=True
    )
    restored = Optic.from_dict(json.loads(json.dumps(optic.to_dict())))
    material = restored.surfaces[1].material_post
    rays = RealRays(*(be.array([v]) for v in (0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.5)))
    material.propagation_model.propagate(rays, 10.0)
    assert rays.z.item() == 10.0
    assert rays.i.item() == pytest.approx(0.75**2)
