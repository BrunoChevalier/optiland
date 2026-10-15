"""Captured material artifacts retain optical provenance, precision and gaps."""

from __future__ import annotations

import csv
import json
import math
import threading
from dataclasses import replace

import pytest
from PIL import Image

from optiland.materials.comparison import (
    SpectralRequest,
    compare_materials,
    map_coordinates,
)
from optiland.materials.properties import (
    MaterialKey,
    MaterialMatch,
    MaterialQuery,
    PropertyValue,
    ReferenceConditions,
    SourceStamp,
)
from optiland_gui.services.file_tasks import prepare_artifact_output
from optiland_gui.services.material_exports import (
    browse_csv,
    browse_provenance,
    condition_groups,
    json_text,
    plot_png,
    spectral_csv,
    spectral_provenance,
)
from tests.test_material_comparison import (
    analytic,
    record,
    snapshot,
    transmission_record,
)

BOUNDS = (90, 10, 1.4, 2.1)


def encode(artifact, tmp_path, extension):
    target = tmp_path / f"output.{extension}"
    staged = tmp_path / ".optiland-test.tmp"
    prepare_artifact_output(
        None,
        {"path": str(target), "staged_path": str(staged), "artifact": artifact},
        lambda *args, **kwargs: None,
        threading.Event(),
    )
    assert not target.exists()
    return staged


def test_browse_csv_keeps_exact_records_missing_coordinates_and_full_precision(
    tmp_path,
):
    value = replace(record(), nd=PropertyValue(1.5123456789012345, "available"))
    missing = replace(
        value,
        key=MaterialKey("synthetic", "Catalog", "missing"),
        display_name='Comma, quote " and α',
        vd=PropertyValue(None, "unsupported", reason="No F line"),
    )
    data = snapshot(value, missing)
    view = map_coordinates(data, "nd-vd")
    query = MaterialQuery(name="Comma", catalog_keys=(("synthetic", "Catalog"),))
    provenance = browse_provenance(query, data.stamps, view.view, BOUNDS)
    artifact = browse_csv(
        data.materials,
        {p.key: p for p in view.coordinates},
        {"synthetic": "Exact source"},
        provenance,
    )
    with encode(artifact, tmp_path, "csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert float(rows[0]["n_d"]) == value.nd.value
    assert rows[0]["source_generation"] == "generation-one"
    assert (
        rows[1]["record_id"] == "missing"
        and rows[1]["material"] == missing.display_name
    )
    assert rows[1]["plot_x"] == "" and rows[1]["plot_x_status"] == "unsupported"
    assert rows[1]["plot_x_reason"] == "No F line"
    meta = json.loads(rows[0]["provenance:material_view"])
    assert meta["plot_bounds"] == list(BOUNDS)
    assert meta["query"]["catalog_keys"] == [["synthetic", "Catalog"]]
    assert json.loads(rows[0]["native_conditions"])["index_convention"] == "unknown"


def test_spectral_csv_preserves_unknown_domain_warning_and_missing_pin_rows(tmp_path):
    value = record(analytic(support=None))
    absent = MaterialKey("absent", "Catalog", "retained")
    result = compare_materials(
        snapshot(value),
        SpectralRequest((value.key, absent), interval_um=(0.4, 0.8), sample_count=3),
    )
    artifact = spectral_csv(result, {}, BOUNDS)
    with encode(artifact, tmp_path, "csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 6
    assert rows[0]["status"] == "available"
    assert json.loads(rows[0]["series_warnings"]) == list(result.series[0].warnings)
    assert "unknown" in rows[0]["series_warnings"]
    assert rows[-1]["source_id"] == "absent" and rows[-1]["source_generation"] == ""
    assert rows[-1]["status"] == "missing" and rows[-1]["value"] == ""
    assert "unavailable" in rows[-1]["reason"] and rows[-1]["thickness_mm"] == ""
    assert "details" not in rows[0]["provenance:comparison"]
    assert float(rows[0]["value"]) == result.series[0].values[0].value


def test_transmission_export_keeps_true_zero_thickness_and_declared_domain():
    value = transmission_record()
    result = compare_materials(
        snapshot(value),
        SpectralRequest(
            (value.key,), quantity="transmission", sample_count=3, thickness_mm=10
        ),
    )
    artifact = spectral_csv(result, {}, BOUNDS)
    row = dict(zip(artifact.columns, artifact.rows[0], strict=True))
    assert row["value"] == 0 and row["status"] == "available"
    assert row["quantity"] == "transmission" and row["thickness_mm"] == 10
    assert row["unit"] == "1" and json.loads(row["declared_support_um"]) == [0.4, 0.6]
    provenance = spectral_provenance(result, BOUNDS)
    assert provenance["materials"][0]["details"]["model"] == "formula 5"
    assert "interface" in provenance["notes"][0]


def test_png_encoding_round_trip_preserves_every_rgba_byte_and_unicode_metadata(
    tmp_path,
):
    pixels = bytes((1, 2, 3, 4, 255, 0, 128, 64, 80, 100, 120, 255, 0, 0, 0, 0))
    provenance = {"source": "α", "bounds": BOUNDS, "quantity": "P_g,F"}
    artifact = plot_png((2, 2, pixels), provenance)
    with Image.open(encode(artifact, tmp_path, "png")) as image:
        assert image.mode == "RGBA" and image.size == (2, 2)
        assert image.tobytes() == pixels
        assert json.loads(image.info["Optiland"])["source"] == "α"
        assert json.loads(image.info["Optiland"])["bounds"] == list(BOUNDS)


def test_provenance_cannot_hide_nonfinite_bounds_or_invalid_pixel_layout():
    with pytest.raises(ValueError):
        json_text({"bounds": [math.nan]})
    with pytest.raises(ValueError):
        plot_png((2, 2, b"short"), {})
    with pytest.raises(ValueError):
        plot_png((8193, 1, b""), {})


def test_spectral_export_rejects_mismatched_curve_table_grid():
    value = record()
    result = compare_materials(
        snapshot(value), SpectralRequest((value.key,), sample_count=3)
    )
    malformed = replace(
        result, series=(replace(result.series[0], values=result.series[0].values[:2]),)
    )
    with pytest.raises(ValueError):
        spectral_csv(malformed, {}, BOUNDS)


def test_grouped_conditions_keep_conflicting_conventions_without_duplicating_records():
    a = record()
    b = replace(a, key=MaterialKey("other", "Catalog", "same"))
    c = replace(
        a,
        key=MaterialKey("synthetic", "Catalog", "absolute"),
        conditions=ReferenceConditions(
            index_convention="absolute",
            wavelength_convention="vacuum",
            temperature_c=25,
        ),
    )
    groups = condition_groups((a, b, c, a))
    assert sum(group["count"] for group in groups) == 3
    assert len(groups) == 2
    unknown = next(
        group
        for group in groups
        if group["conditions"]["index_convention"] == "unknown"
    )
    assert unknown["count"] == 2
    assert set(unknown["sources"]) == {"synthetic", "other"}


def test_unknown_record_or_source_stamp_is_not_silently_exported():
    value = record()
    provenance = browse_provenance(
        MaterialQuery(), (SourceStamp("different", "1"),), "nd-vd", BOUNDS
    )
    point = map_coordinates(snapshot(value), "nd-vd").coordinates[0]
    with pytest.raises(KeyError):
        browse_csv((value,), {value.key: point}, {}, provenance)
    with pytest.raises(KeyError):
        browse_csv((value,), {}, {}, provenance)


def test_browse_export_preserves_visible_order_and_exact_match_deltas(tmp_path):
    a = record(identity="a")
    b = record(identity="b")
    data = snapshot(a, b)
    points = map_coordinates(data, "nd-vd").coordinates
    matches = (
        MaterialMatch(
            a.key, 1.2345678901234567e-9, -1.2345678901234567, 0.9876543210987654
        ),
        MaterialMatch(b.key, -0.000001, 2.25, 0.5),
    )
    provenance = browse_provenance(MaterialQuery(), data.stamps, "nd-vd", BOUNDS)
    artifact = browse_csv(
        (b, a), {point.key: point for point in points}, {}, provenance, matches
    )
    with encode(artifact, tmp_path, "csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["record_id"] for row in rows] == ["b", "a"]
    assert float(rows[1]["delta_nd"]) == matches[0].delta_nd
    assert float(rows[1]["delta_vd"]) == matches[0].delta_vd
    assert float(rows[1]["match_distance"]) == matches[0].distance
