"""Read original on-disk GLC examples without any manufacturer catalog data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import optiland.backend as be
from optiland.fileio.oslo.catalog import SUPPORTED_GLC_VERSIONS, read_glc_catalog
from optiland.materials import BaseMaterial
from tests.utils import assert_allclose

FIXTURES = Path(__file__).parent / "fixtures" / "oslo_glc"
# Intentionally independent of the implementation: changes require a fixture and
# an explicit decision about the documented supported contract.
VERSIONS = (
    "5.00",
    "5.11",
    "5.32",
    "6.31",
    "6.4",
    "6.63",
    "6.65",
    "7.02",
    "24.1",
    "26.1",
)


def fictional_index(name, w):
    """Independent analytic laws for deliberately invented fixture coefficients."""
    if name == "FAKE_LA":
        return np.sqrt(
            2.25
            + 0.02 * w**2
            + 0.003 / w**2
            + 0.0002 / w**4
            + 0.00003 / w**6
            + 0.000004 / w**8
        )
    if name == "FAKE_SE":
        return np.sqrt(
            1
            + 1.1 * w**2 / (w**2 - 0.02)
            + 0.2 * w**2 / (w**2 - 0.09)
            + 0.4 * w**2 / (w**2 - 81)
        )
    if name == "FAKE_CO":
        return 1.45 + 0.004 / w + 0.0002 / w**3.5
    if name == "FAKE_HI":
        return np.sqrt(
            2.1
            + 0.015 * w**2
            + 0.0005 * w**4
            + 0.003 / w**2
            + 0.0002 / w**4
            + 0.00003 / w**6
            + 0.000004 / w**8
            + 0.0000005 / w**10
            + 0.00000006 / w**12
        )
    if name == "FAKE_HE":
        return (
            1.55
            + 0.004 / (w**2 - 0.028)
            + 0.0002 / (w**2 - 0.028) ** 2
            + 0.015 * w**2
            + 0.0007 * w**4
            + 0.00005 * w**6
        )
    return {"FAKE_ZERO": 1.5, "FAKE000001": 1.25}[name] + np.zeros_like(w)


def test_version_corpus_matches_the_explicit_support_contract():
    assert set(VERSIONS) == SUPPORTED_GLC_VERSIONS
    assert {p.name for p in (FIXTURES / "valid").glob("*.glc")} == {
        f"version-{version.replace('.', '-')}.glc" for version in VERSIONS
    }


@pytest.mark.parametrize("version", VERSIONS)
def test_versioned_files_have_independent_optical_values(version, set_test_backend):
    path = FIXTURES / "valid" / f"version-{version.replace('.', '-')}.glc"
    entries = read_glc_catalog(path)
    expected_names = {
        "FAKE_LA",
        "FAKE_SE",
        "FAKE_CO",
        "FAKE_HI",
        "FAKE_HE",
        "FAKE_ZERO",
        "FAKE000001",
    }
    assert {entry.material.name for entry in entries} == expected_names
    waves = np.array([[0.45, 0.55], [0.65, 0.75]])
    for line, entry in enumerate(entries, 2):
        material = entry.material
        assert entry.location == f"{path}:{line}"
        expected = be.array(fictional_index(material.name, waves))
        assert_allclose(material.n(be.array(waves)), expected)
        saved = json.dumps(material.to_dict())
        restored = BaseMaterial.from_dict(json.loads(saved))
        assert_allclose(restored.n(be.array(waves)), expected)
        assert str(FIXTURES) not in saved
        assert material.metadata["provenance"] == [
            {
                "catalog": f"Fictional Optiland fixtures {version}",
                "format_version": version,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        ]

    by_name = {entry.material.name: entry.material for entry in entries}
    # The catalog summary is deliberately unrelated to its authoritative law.
    edge = by_name["FAKE000001"]
    assert edge.metadata["oslo"]["nd"] == 0.01
    assert edge.metadata["oslo"]["vd"] == -12
    assert edge.n(0.55).item() == 1.25
    assert by_name["FAKE_HI"].definition.thermal is None
    assert by_name["FAKE_HI"].metadata["oslo"]["thermal_coefficients"] == [4.25]
    assert by_name["FAKE_HI"].definition.dispersion.wavelength_range_um == (0.35, 0.95)
    assert by_name["FAKE_CO"].definition.extinction is None
    assert by_name["FAKE_CO"].metadata["oslo"]["reference_id"] == 12
    assert by_name["FAKE_LA"].metadata["oslo"]["reference_id"] == -77
    assert by_name["FAKE_HE"].definition.dispersion.wavelength_range_um is None

    w = 0.55
    assert by_name["FAKE_SE"].n(w, temperature=35).item() == pytest.approx(
        fictional_index("FAKE_SE", w) - 15 * 3e-6
    )
    assert by_name["FAKE_CO"].n(w, temperature=35).item() == pytest.approx(
        fictional_index("FAKE_CO", w)
        + 15e-6 * (2 + 0.1 / w**2 + 0.02 / w**4 + 0.003 / w**6)
    )
    # The first three sorted samples define T=.75 + .6*(w-.5) + (w-.5)^2.
    assert by_name["FAKE_LA"].internal_transmittance(0.45, 10).item() == pytest.approx(
        0.7225**2
    )
    assert by_name["FAKE_ZERO"].internal_transmittance(0.4, 5).item() == 0
    assert by_name["FAKE_ZERO"].internal_transmittance(0.8, 5).item() == 1
    with pytest.raises(ValueError, match="Zero transmission"):
        by_name["FAKE_ZERO"].k(0.4)


@pytest.mark.parametrize(
    "filename,line,reason",
    [
        ("unknown-version.glc", 1, "version"),
        ("truncated.glc", 2, "truncated"),
        ("duplicate-wavelength.glc", 2, "transmission"),
        ("transmission-above-one.glc", 2, "transmission"),
        ("negative-density.glc", 2, "density"),
        ("nonpositive-index.glc", 2, "index"),
        ("unknown-range.glc", 2, "range"),
        ("record-count.glc", 1, "declared"),
        ("trailing-data.glc", 2, "trailing"),
    ],
)
def test_invalid_fixture_files_report_the_source(filename, line, reason):
    with pytest.raises(ValueError) as caught:
        read_glc_catalog(FIXTURES / "invalid" / filename)
    assert f"{filename}:{line}:" in str(caught.value)
    assert reason in str(caught.value)
