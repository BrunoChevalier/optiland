"""Original synthetic GLC records; no manufacturer catalog fixtures."""

from __future__ import annotations

import hashlib
import json

import pytest

from optiland.fileio.oslo.catalog import (
    SUPPORTED_GLC_VERSIONS,
    load_glc_catalogs,
    read_glc_catalog,
)
from optiland.materials import IdealMaterial


def glc_record(
    name="SAMPLE_A",
    dispersion="3 3 1.4 .002 .0001",
    thermal="0 0",
    transmission="0 0",
    tail="",
):
    return (
        f"{name} 1.5 60 2.5 1.2 70 .9 1 300 12345 ? A "
        f"{dispersion} {thermal} {transmission} {tail}"
    ).strip()


def catalog(
    tmp_path,
    records=None,
    *,
    name="synthetic.glc",
    version="7.02",
    label="Synthetic catalog",
):
    records = [glc_record()] if records is None else records
    path = tmp_path / name
    path.write_text(
        f"{version} {len(records)} {label}\n" + "\n".join(records) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("version", sorted(SUPPORTED_GLC_VERSIONS))
@pytest.mark.parametrize(
    "dispersion,expected",
    [
        (
            "1 6 2.2 .01 .002 .0001 .00001 .000001",
            (
                2.2
                + 0.01 * 0.5**2
                + 0.002 / 0.5**2
                + 0.0001 / 0.5**4
                + 0.00001 / 0.5**6
                + 0.000001 / 0.5**8
            )
            ** 0.5,
        ),
        (
            "2 6 1.2 .1 .5 .01 .04 100",
            (1 + 1.2 * 0.25 / 0.24 + 0.1 * 0.25 / 0.21 + 0.5 * 0.25 / (-99.75)) ** 0.5,
        ),
        ("3 3 1.4 .002 .0001", 1.4 + 0.002 / 0.5 + 0.0001 / 0.5**3.5),
        (
            "6 9 2.2 .01 .001 .002 .0001 .00001 .000001 .0000001 .00000001",
            (
                2.2
                + 0.01 * 0.5**2
                + 0.001 * 0.5**4
                + 0.002 / 0.5**2
                + 0.0001 / 0.5**4
                + 0.00001 / 0.5**6
                + 0.000001 / 0.5**8
                + 0.0000001 / 0.5**10
                + 0.00000001 / 0.5**12
            )
            ** 0.5,
        ),
        (
            "7 6 1.6 .003 .0001 .01 .001 .0001",
            1.6
            + 0.003 / (0.25 - 0.028)
            + 0.0001 / (0.25 - 0.028) ** 2
            + 0.01 * 0.5**2
            + 0.001 * 0.5**4
            + 0.0001 * 0.5**6,
        ),
    ],
)
def test_supported_catalog_dispersion(
    tmp_path, version, dispersion, expected, set_test_backend
):
    path = catalog(tmp_path, [glc_record(dispersion=dispersion)], version=version)
    (entry,) = read_glc_catalog(path)
    assert entry.location == f"{path}:2"
    assert entry.material.n(0.5).item() == pytest.approx(expected)
    (provenance,) = entry.material.metadata["provenance"]
    assert provenance == {
        "catalog": "Synthetic catalog",
        "format_version": version,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    assert str(tmp_path) not in json.dumps(entry.material.to_dict())


@pytest.mark.parametrize(
    "thermal,model",
    [
        ("0 0", None),
        ("1 6 .000001 .00000001 0 .0000001 0 .2", "schott"),
        ("2 1 .000002", "constant"),
        ("3 4 .000001 .0000001 0 0", "corning"),
    ],
)
def test_thermal_transmission_and_optional_groups(tmp_path, thermal, model):
    path = catalog(
        tmp_path,
        [
            glc_record(
                thermal=thermal,
                transmission="1 3 .6 .82 .4 .7 .5 .75",
                tail="1 2 .35 .9 1 1 -123",
            )
        ],
    )
    m = read_glc_catalog(path)[0].material
    assert (m.definition.thermal.kind if m.definition.thermal else None) == model
    assert m.definition.dispersion.wavelength_range_um == (0.35, 0.9)
    assert m.metadata["oslo"]["reference_id"] == -123
    assert m.metadata["oslo"]["tce_1e7"] == 70
    assert m.internal_transmittance(0.45, 5).item() == pytest.approx(0.7225)


def test_external_legacy_zero_placeholder(tmp_path):
    m = read_glc_catalog(
        catalog(tmp_path, [glc_record(thermal="0 1 0")], version="6.65")
    )[0].material
    assert m.definition.thermal is None
    assert m.metadata["oslo"]["thermal_coefficients"] == [0]


def test_negative_abbe_summary_is_valid_for_anomalous_dispersion(tmp_path):
    path = catalog(tmp_path, [glc_record().replace("1.5 60", "1.5 -30")])
    assert read_glc_catalog(path)[0].material.metadata["oslo"]["vd"] == -30


def test_declared_empty_transmission_table_retains_absence(tmp_path):
    path = catalog(tmp_path, [glc_record(transmission="1 0")])
    material = read_glc_catalog(path)[0].material
    assert material.definition.extinction is None
    assert material.metadata["oslo"]["transmission_model"] == 1


@pytest.mark.parametrize(
    "model,coefficients,expected_slope",
    [
        (2, "2", 2e-6),
        (
            3,
            "1 .2 .03 .004",
            (1 + 0.2 / 0.5**2 + 0.03 / 0.5**4 + 0.004 / 0.5**6) * 1e-6,
        ),
    ],
)
def test_relative_thermal_catalog_units_are_normalized(
    tmp_path, model, coefficients, expected_slope
):
    path = catalog(
        tmp_path,
        [
            glc_record(
                dispersion="3 3 1.5 0 0",
                thermal=f"{model} {len(coefficients.split())} {coefficients}",
            )
        ],
    )
    material = read_glc_catalog(path)[0].material
    assert material.n(0.5, temperature=30).item() == pytest.approx(
        1.5 + 10 * expected_slope
    )


def test_no_thermal_model_never_applies_unused_nonzero_value(tmp_path):
    path = catalog(tmp_path, [glc_record(thermal="0 1 5.3")])
    material = read_glc_catalog(path)[0].material
    assert material.definition.thermal is None
    assert material.metadata["oslo"]["thermal_coefficients"] == [5.3]
    assert material.n(0.5, temperature=50).item() == material.n(0.5).item()


@pytest.mark.parametrize(
    "change,match",
    [
        ({"dispersion": "4 6 1 2 3 4 5 6"}, "dispersion"),
        ({"dispersion": "1 3 1 2 3"}, "dispersion"),
        ({"dispersion": "2 -1"}, "count"),
        ({"dispersion": "2 1000000000"}, "count"),
        ({"dispersion": "3 3 nan 0 0"}, "non-finite"),
        ({"name": "NAMETOOLONG"}, "10 characters"),
        ({"thermal": "4 0"}, "thermal"),
        ({"thermal": "3 1 0"}, "thermal"),
        ({"thermal": "0 2 3 4"}, "placeholder"),
        ({"transmission": "2 0"}, "transmission"),
        ({"transmission": "1 3 .4 .8 .4 .9 .6 .95"}, "transmission"),
        ({"transmission": "1 3 .4 .8 .5 1.1 .6 .95"}, "transmission"),
        ({"tail": "1 2 .8 .4"}, "limits"),
        ({"tail": "9 2 .4 .8"}, "range"),
        ({"tail": "0 0 2 1 3"}, "reference"),
        ({"tail": "0 0 1 1 1.2"}, "integer"),
        ({"tail": "0 0 0 0 1"}, "trailing"),
        ({"tail": "1"}, "truncated"),
    ],
)
def test_bad_records_fail_with_location(tmp_path, change, match):
    with pytest.raises(ValueError, match=f"synthetic.glc:2:.*{match}"):
        read_glc_catalog(catalog(tmp_path, [glc_record(**change)]))


@pytest.mark.parametrize(
    "header", ["", "7.02", "7.02 -1", "7.02 1000000", "9.99 1", "7.02 0"]
)
def test_invalid_header_and_record_counts(tmp_path, header):
    path = tmp_path / "bad.glc"
    path.write_text(header + "\n" + glc_record())
    with pytest.raises(ValueError, match="bad.glc:1"):
        read_glc_catalog(path)


def test_duplicate_definitions_and_bindings(tmp_path):
    a = catalog(tmp_path, name="a.glc", label="A")
    b = catalog(tmp_path, name="b.glc", label="B")
    both = load_glc_catalogs([a, b, a], {})
    reverse = load_glc_catalogs([b, a], {})
    assert both["sample_a"].to_dict() == reverse["sample_a"].to_dict()
    assert len(both["sample_a"].metadata["provenance"]) == 2
    b = catalog(tmp_path, [glc_record(dispersion="3 3 1.7 .002 .0001")], name="b.glc")
    with pytest.raises(ValueError, match="Conflicting.*SAMPLE_A.*a.glc:2.*b.glc:2"):
        load_glc_catalogs([b, a], {})
    assert load_glc_catalogs([a, b], {"sample_a": IdealMaterial(1.6)}) == {}


def test_override_does_not_hide_malformed_selected_catalog(tmp_path):
    path = catalog(tmp_path, [glc_record(dispersion="8 0")])
    with pytest.raises(ValueError, match="dispersion"):
        load_glc_catalogs([path], {"sample_a": IdealMaterial(1.6)})


def test_conflicting_duplicate_basenames_identify_both_directories(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    a = catalog(left, name="same.glc")
    b = catalog(right, [glc_record(dispersion="3 3 1.8 0 0")], name="same.glc")
    with pytest.raises(ValueError) as caught:
        load_glc_catalogs([a, b], {})
    assert str(a) in str(caught.value) and str(b) in str(caught.value)
    data = json.dumps(read_glc_catalog(a)[0].material.to_dict())
    assert str(left) not in data and "left" not in data


def test_catalog_encoding_and_collection_type(tmp_path):
    path = catalog(tmp_path)
    path.write_bytes(("6.65 1 Synthetic é\n" + glc_record()).encode("cp1252"))
    assert (
        read_glc_catalog(path)[0].material.metadata["provenance"][0]["catalog"]
        == "Synthetic é"
    )
    with pytest.raises(TypeError, match="collection"):
        load_glc_catalogs(path, {})
    cdb = tmp_path / "wrong.cdb"
    cdb.write_text("7.02 0")
    with pytest.raises(ValueError, match="GLC"):
        read_glc_catalog(cdb)
