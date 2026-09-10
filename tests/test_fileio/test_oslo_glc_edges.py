"""Original synthetic boundary cases for the supported GLC input contract."""

from __future__ import annotations

import math

import pytest

import optiland.backend as be
import optiland.fileio.oslo.catalog as glc
from tests.test_fileio.test_oslo_glc import catalog, glc_record


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_text_encodings_whitespace_and_fortran_exponents(tmp_path, encoding, newline):
    record = glc_record(name="FAKE_TEXT", dispersion="3 3 1.45D+0 4d-3 2E-4")
    path = tmp_path / "fictional.GLC"
    path.write_bytes(
        newline.join(
            [
                "7.02\t1\tFictional café",
                "",
                "\t" + record.replace(" ", "\t ") + "  ",
                "",
            ]
        ).encode(encoding)
    )
    (entry,) = glc.read_glc_catalog(path)
    assert entry.location == f"{path}:3"
    assert entry.material.metadata["provenance"][0]["catalog"] == "Fictional café"
    assert entry.material.n(0.5).item() == pytest.approx(
        1.45 + 0.004 / 0.5 + 0.0002 / 0.5**3.5
    )


def test_empty_catalog_and_omitted_header_label(tmp_path):
    path = tmp_path / "fictional.glc"
    path.write_text("7.02 0\n\n", encoding="utf-8")
    assert glc.read_glc_catalog(path) == []
    path.write_text("7.02 1\n" + glc_record(name="FAKE_LABEL"), encoding="utf-8")
    assert (
        glc.read_glc_catalog(path)[0].material.metadata["provenance"][0]["catalog"]
        == "fictional"
    )


@pytest.mark.parametrize("cut", range(1, len(glc_record().split())))
def test_every_mandatory_token_truncation_fails(tmp_path, cut):
    record = " ".join(glc_record(name="FAKE_CUT").split()[:cut])
    with pytest.raises(ValueError, match="synthetic.glc:2: FAKE_CUT: truncated"):
        glc.read_glc_catalog(catalog(tmp_path, [record]))


@pytest.mark.parametrize("field", range(1, 10))
@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_nonfinite_values_in_every_numeric_summary_field(tmp_path, field, value):
    tokens = glc_record(name="FAKE_NAN").split()
    tokens[field] = value
    with pytest.raises(ValueError, match="non-finite"):
        glc.read_glc_catalog(catalog(tmp_path, [" ".join(tokens)]))


@pytest.mark.parametrize(
    "field,value", [(1, "0"), (1, "-1"), (3, "-1"), (6, "-.1"), (6, "1.1")]
)
def test_invalid_summary_bounds(tmp_path, field, value):
    tokens = glc_record(name="FAKE_BOUND").split()
    tokens[field] = value
    with pytest.raises(ValueError, match="invalid catalog"):
        glc.read_glc_catalog(catalog(tmp_path, [" ".join(tokens)]))


@pytest.mark.parametrize(
    "change",
    [
        {"dispersion": "1.0 6 1 2 3 4 5 6"},
        {"dispersion": "3 3.0 1.4 0 0"},
        {"dispersion": "6 8 1 0 0 0 0 0 0 0"},
        {"dispersion": "7 5 1 0 0 0 0"},
        {"thermal": "1 5 1 0 0 0 0"},
        {"thermal": "2 0"},
        {"thermal": "2 1 inf"},
        {"thermal": "0 -1"},
        {"thermal": "1 7 0 0 0 0 0 0 0"},
        {"transmission": "1 -1"},
        {"transmission": "1 1 .4 .8"},
        {"transmission": "1 2 .4 .8 .5 .9"},
        {"transmission": "0 3 .4 .8 .5 .9 .6 .95"},
        {"transmission": "1 3 0 .8 .5 .9 .6 .95"},
        {"transmission": "1 3 .4 -.1 .5 .9 .6 .95"},
        {"transmission": "1 3 .4 nan .5 .9 .6 .95"},
        {"tail": "0 1 .4"},
        {"tail": "1 1 .4"},
        {"tail": "1 3 .4 .6 .8"},
        {"tail": "1 2 0 .8"},
        {"tail": "1 2 .4 .4"},
        {"tail": "1 2 .4 inf"},
        {"tail": "0 0 1 0"},
        {"tail": "0 0 0 1 5"},
        {"tail": "0 0 1 2 5 6"},
        {"tail": "0 0 1 1"},
    ],
)
def test_invalid_optical_groups_cannot_be_dropped(tmp_path, change):
    with pytest.raises(ValueError, match="synthetic.glc:2: FAKE_GROUP:"):
        glc.read_glc_catalog(
            catalog(tmp_path, [glc_record(name="FAKE_GROUP", **change)])
        )


@pytest.mark.parametrize(
    "header", ["", "7.02 one", "7.02 1.0", "7.020 1", "0.00 1", "26.2 1"]
)
def test_empty_and_unrecognized_headers(tmp_path, header):
    path = tmp_path / "header.glc"
    path.write_text(header, encoding="utf-8")
    with pytest.raises(ValueError, match="header.glc:1"):
        glc.read_glc_catalog(path)


def test_file_size_limit_counts_bytes_and_includes_the_boundary(tmp_path, monkeypatch):
    path = catalog(tmp_path, [glc_record(name="FAKE_SIZE")], label="Fictional café")
    size = len(path.read_bytes())
    monkeypatch.setattr(glc, "MAX_CATALOG_BYTES", size)
    assert len(glc.read_glc_catalog(path)) == 1
    monkeypatch.setattr(glc, "MAX_CATALOG_BYTES", size - 1)
    with pytest.raises(ValueError, match="catalog exceeds size"):
        glc.read_glc_catalog(path)


def test_record_size_limit_includes_the_boundary(tmp_path, monkeypatch):
    record = glc_record(name="FAKE_SIZE")
    path = catalog(tmp_path, [record])
    monkeypatch.setattr(glc, "MAX_RECORD_CHARS", len(record))
    assert len(glc.read_glc_catalog(path)) == 1
    monkeypatch.setattr(glc, "MAX_RECORD_CHARS", len(record) - 1)
    with pytest.raises(ValueError, match="record exceeds size"):
        glc.read_glc_catalog(path)


def test_catalog_record_limit_without_large_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(glc, "MAX_CATALOG_RECORDS", 2)
    records = [glc_record(name=f"FAKE_{i}") for i in range(3)]
    assert len(glc.read_glc_catalog(catalog(tmp_path, records[:2]))) == 2
    with pytest.raises(ValueError, match="record count"):
        glc.read_glc_catalog(catalog(tmp_path, records))


@pytest.mark.parametrize("count", [50, 51])
def test_transmission_sample_count_limit(tmp_path, count):
    samples = " ".join(f"{0.4 + i / 100:.2f} .8" for i in range(count))
    path = catalog(
        tmp_path, [glc_record(name="FAKE_COUNT", transmission=f"1 {count} {samples}")]
    )
    if count == 51:
        with pytest.raises(ValueError, match="count"):
            glc.read_glc_catalog(path)
    else:
        material = glc.read_glc_catalog(path)[0].material
        assert material.internal_transmittance(0.555, 10).item() == pytest.approx(0.64)


def test_same_file_case_insensitive_duplicates_and_optical_conflicts(tmp_path):
    record = glc_record(name="FAKE_DUP")
    path = catalog(tmp_path, [record, record.replace("FAKE_DUP", "fake_dup")])
    merged = glc.load_glc_catalogs((p for p in [path, path]), {})
    assert list(merged) == ["fake_dup"]
    assert len(merged["fake_dup"].metadata["provenance"]) == 1
    path = catalog(tmp_path, [record, glc_record(name="fake_dup", thermal="2 1 3")])
    with pytest.raises(
        ValueError, match="Conflicting.*synthetic.glc:2.*synthetic.glc:3"
    ):
        glc.load_glc_catalogs([path], {})


def test_malformed_unused_record_invalidates_selected_file(tmp_path):
    path = catalog(
        tmp_path,
        [glc_record(name="FAKE_GOOD"), glc_record(name="FAKE_BAD", tail="9 0")],
    )
    with pytest.raises(ValueError, match="synthetic.glc:3: FAKE_BAD"):
        glc.load_glc_catalogs([path], {})


def test_quadratic_tie_prefers_shorter_wavelength(tmp_path, set_test_backend):
    path = catalog(
        tmp_path,
        [glc_record(name="FAKE_TIE", transmission="1 4 .25 .2 .5 .4 .75 .5 1 .9")],
    )
    material = glc.read_glc_catalog(path)[0].material
    # .625 is equidistant from .25 and 1 for the third node. The shorter
    # stencil has Lagrange weights -1/8, 3/4, 3/8: T=37/80.
    assert material.internal_transmittance(0.625, 5).item() == pytest.approx(37 / 80)
    assert material.k(0.625).item() == pytest.approx(
        -math.log(37 / 80) * 0.625 / (4 * math.pi * 5000)
    )


@pytest.mark.parametrize("samples", [".25 .95 .5 1 .75 1", ".25 .05 .5 0 .75 0"])
def test_unphysical_interpolation_is_rejected_not_clipped(
    tmp_path, samples, set_test_backend
):
    path = catalog(
        tmp_path, [glc_record(name="FAKE_OVER", transmission="1 3 " + samples)]
    )
    material = glc.read_glc_catalog(path)[0].material
    with pytest.raises(ValueError, match="Unphysical interpolated"):
        material.internal_transmittance(0.625, 5)


def test_imported_table_rejects_precision_collapsed_wavelengths(
    tmp_path, set_test_backend
):
    path = catalog(
        tmp_path,
        [glc_record(name="FAKE_PREC", transmission="1 3 1 .8 1.000000001 .85 2 .9")],
    )
    material = glc.read_glc_catalog(path)[0].material
    be.set_precision("float32")
    try:
        with pytest.raises(ValueError, match="coincide in active precision"):
            material.internal_transmittance(1, 5)
    finally:
        be.set_precision("float64")
