"""Independent optical fixtures for cached maps and bounded exact comparisons."""

from __future__ import annotations

import math
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, replace

import pytest

import optiland.backend as be
from optiland.materials import DataMaterial, IdealMaterial
from optiland.materials.comparison import (
    MAX_DETAIL_FIELDS,
    MAX_DETAIL_TEXT,
    SpectralRequest,
    compare_materials,
    map_coordinates,
    material_details,
    nearby_materials,
)
from optiland.materials.portability import capture_material
from optiland.materials.properties import (
    C_LINE_UM,
    D_LINE_UM,
    F_LINE_UM,
    G_LINE_UM,
    MaterialKey,
    MaterialSnapshot,
    PropertyValue,
    SourceStamp,
    describe_material,
)


def analytic(name="Crown", *, support=(0.4, 0.8), coefficient=0.005, **kwargs):
    return DataMaterial.from_coefficients(
        "formula 5",
        [1.5, coefficient, -2],
        name=name,
        wavelength_range=support,
        **kwargs,
    )


def record(material=None, identity="first"):
    material = analytic() if material is None else material
    value = describe_material(material, MaterialKey("synthetic", "Catalog", identity))
    return replace(value, owned_data=capture_material(material))


def snapshot(*records):
    return MaterialSnapshot((SourceStamp("synthetic", "generation-one"),), records)


def test_cached_lines_and_every_map_mode_match_independent_equations(
    set_test_backend, monkeypatch
):
    material = analytic()
    calls = []
    original = material.n

    def index(wavelength):
        calls.append(wavelength)
        return original(wavelength)

    monkeypatch.setattr(material, "n", index)
    summary = describe_material(material, MaterialKey("synthetic", "Catalog", "first"))
    assert calls == [D_LINE_UM, F_LINE_UM, C_LINE_UM, G_LINE_UM]
    assert G_LINE_UM == 0.4358343

    def n(wavelength):
        return 1.5 + 0.005 / wavelength**2

    denominator = n(F_LINE_UM) - n(C_LINE_UM)
    vd = (n(D_LINE_UM) - 1) / denominator
    assert summary.n_f.value == pytest.approx(n(F_LINE_UM), rel=1e-12)
    assert summary.n_c.value == pytest.approx(n(C_LINE_UM), rel=1e-12)
    assert summary.n_g.value == pytest.approx(n(G_LINE_UM), rel=1e-12)
    monkeypatch.setattr(
        DataMaterial,
        "n",
        lambda *args: pytest.fail("Map re-evaluated an optical model"),
    )
    expected = {
        "nd-vd": (vd, n(D_LINE_UM)),
        "nd-dispersion": (denominator, n(D_LINE_UM)),
        "partial-fd": (vd, (n(F_LINE_UM) - n(D_LINE_UM)) / denominator),
        "partial-gf": (vd, (n(G_LINE_UM) - n(F_LINE_UM)) / denominator),
        "partial-dc": (vd, (n(D_LINE_UM) - n(C_LINE_UM)) / denominator),
    }
    results = {}
    for view, values in expected.items():
        result = map_coordinates(snapshot(replace(summary, owned_data=None)), view)
        assert result.stamps == snapshot(summary).stamps
        assert result.descending_x == (view != "nd-dispersion")
        assert (
            result.coordinates[0].x.value,
            result.coordinates[0].y.value,
        ) == pytest.approx(values, rel=1e-12)
        assert "n_F − n_C" in result.ylabel or view in {"nd-vd", "nd-dispersion"}
        results[view] = result.coordinates[0].y.value
    assert results["partial-fd"] + results["partial-dc"] == pytest.approx(1)


def test_alternate_maps_keep_negative_dispersion_zero_and_missing_lines(
    set_test_backend,
):
    negative = record(analytic(coefficient=-0.005))
    zero = record(IdealMaterial(1.5), "zero")
    limited = record(analytic(support=(0.48, 0.8)), "limited")
    assert negative.vd.value < 0
    assert (
        map_coordinates(snapshot(negative), "partial-fd").coordinates[0].y.status
        == "available"
    )
    assert map_coordinates(snapshot(zero), "nd-dispersion").coordinates[0].x.value == 0
    for view in ("partial-fd", "partial-gf", "partial-dc"):
        assert (
            "zero dispersion"
            in map_coordinates(snapshot(zero), view).coordinates[0].y.reason
        )
    point = map_coordinates(snapshot(limited), "partial-gf").coordinates[0]
    assert point.x.status == "available" and point.y.status == "unsupported"


def test_common_index_interval_excludes_unknown_and_retains_unavailable_pins(
    set_test_backend,
):
    a, b = record(), record(analytic(support=(0.5, 0.7)), "second")
    unknown = record(analytic(support=None), "unknown")
    absent = MaterialKey("missing", "Catalog", "deleted")
    request = SpectralRequest((unknown.key, b.key, absent, a.key), sample_count=5)
    result = compare_materials(snapshot(a, b, unknown), request)
    assert result.interval_um == (0.5, 0.7)
    assert result.wavelengths_um[0] == 0.5 and result.wavelengths_um[-1] == 0.7
    assert tuple(series.key for series in result.series) == request.keys
    assert all(value.status == "missing" for value in result.series[0].values)
    assert "unknown" in result.series[0].warnings[0]
    assert all(value.status == "missing" for value in result.series[2].values)
    assert result.series[2].details is None
    assert [value.value for value in result.series[1].values] == pytest.approx(
        [1.5 + 0.005 / wavelength**2 for wavelength in result.wavelengths_um]
    )
    assert result.series[1].conditions == b.conditions
    with pytest.raises(FrozenInstanceError):
        result.interval_um = (0.4, 0.8)


def test_unknown_and_disjoint_default_domains_require_explicit_interval(
    set_test_backend,
):
    unknown = record(analytic(support=None))
    default = compare_materials(snapshot(unknown), SpectralRequest((unknown.key,)))
    assert default.interval_um is None and not default.wavelengths_um
    assert "explicit" in default.notes[0]
    explicit = compare_materials(
        snapshot(unknown),
        SpectralRequest((unknown.key,), interval_um=(0.4, 0.8), sample_count=3),
    )
    assert all(value.status == "available" for value in explicit.series[0].values)
    assert "unknown" in explicit.series[0].warnings[0]
    a, b = (
        record(analytic(support=(0.4, 0.5)), "a"),
        record(analytic(support=(0.6, 0.8)), "b"),
    )
    disjoint = compare_materials(snapshot(a, b), SpectralRequest((a.key, b.key)))
    assert disjoint.interval_um is None and len(disjoint.series) == 2


def test_explicit_interval_preserves_domain_gaps_even_with_native_clamp(
    set_test_backend,
):
    material = DataMaterial.from_samples([0.5, 0.6], [1.6, 1.5], bounds="clamp")
    value = record(material)
    result = compare_materials(
        snapshot(value),
        SpectralRequest((value.key,), interval_um=(0.4, 0.7), sample_count=4),
    )
    assert [value.status for value in result.series[0].values] == [
        "unsupported",
        "available",
        "available",
        "unsupported",
    ]
    assert [value.value for value in result.series[0].values[1:3]] == pytest.approx(
        [1.6, 1.5]
    )


def test_native_singularity_is_one_gap_and_does_not_erase_valid_neighbors(
    set_test_backend,
):
    material = DataMaterial.from_coefficients(
        "formula 1", [0.5, 0.01, 0.5], wavelength_range=(0.4, 0.8)
    )
    value = record(material)
    warning = (
        pytest.warns(RuntimeWarning, match="divide by zero")
        if be.get_backend() == "numpy"
        else nullcontext()
    )
    with warning:
        result = compare_materials(
            snapshot(value),
            SpectralRequest((value.key,), interval_um=(0.4, 0.6), sample_count=5),
        )
    assert [value.status for value in result.series[0].values] == [
        "available",
        "available",
        "invalid",
        "available",
        "available",
    ]


def transmission_record():
    return record(
        analytic(
            support=(0.5, 0.9),
            extinction={
                "kind": "internal_transmittance",
                "wavelengths_um": [0.4, 0.5, 0.6],
                "values": [0, 0.25, 1],
                "thickness_mm": 5,
                "interpolation": "local_quadratic",
            },
        )
    )


@pytest.mark.parametrize("thickness", [0, 2.5, 5, 10])
def test_native_transmission_interpolates_before_thickness_scaling_including_zero(
    set_test_backend, monkeypatch, thickness
):
    value = transmission_record()
    monkeypatch.setattr(
        DataMaterial,
        "k",
        lambda *args: pytest.fail("Transmission table was converted through k"),
    )
    result = compare_materials(
        snapshot(value),
        SpectralRequest(
            (value.key,),
            quantity="transmission",
            thickness_mm=thickness,
            sample_count=5,
        ),
    )
    assert result.interval_um == (0.4, 0.6)  # independent of declared n support
    expected = [base ** (thickness / 5) for base in (0, 0.0625, 0.25, 0.5625, 1)]
    assert [point.value for point in result.series[0].values] == pytest.approx(
        expected, abs=1e-12
    )
    assert dict(result.series[0].details.fields)["Source thickness (mm)"] == "5.0"


def test_extinction_table_transmission_matches_beer_law_and_gaps(set_test_backend):
    material = analytic(
        extinction={
            "kind": "tabulated_k",
            "wavelengths_um": [0.5, 0.7],
            "values": [1e-5, 3e-5],
        },
        bounds="clamp",
    )
    value = record(material)
    result = compare_materials(
        snapshot(value),
        SpectralRequest(
            (value.key,),
            quantity="transmission",
            interval_um=(0.4, 0.8),
            sample_count=5,
            thickness_mm=2,
        ),
    )
    assert (
        result.series[0].values[0].status
        == result.series[0].values[-1].status
        == "unsupported"
    )
    assert [point.value for point in result.series[0].values[1:4]] == pytest.approx(
        [
            math.exp(-4 * math.pi * k * 2000 / wave)
            for wave, k in ((0.5, 1e-5), (0.6, 2e-5), (0.7, 3e-5))
        ]
    )


@pytest.mark.parametrize("thickness", [0, 10])
def test_unknown_extinction_is_unavailable_even_at_zero_thickness(
    set_test_backend, thickness
):
    value = record()
    result = compare_materials(
        snapshot(value),
        SpectralRequest(
            (value.key,),
            quantity="transmission",
            interval_um=(0.4, 0.8),
            sample_count=3,
            thickness_mm=thickness,
        ),
    )
    assert all(
        point.status == "missing" and point.value is None
        for point in result.series[0].values
    )
    assert "actual owned" in result.series[0].warnings[0]


def test_details_are_bounded_exact_and_explicitly_attributed(set_test_backend):
    value = record(analytic(metadata={"reference": "x" * 20000}))
    details = material_details(value)
    fields = dict(details.fields)
    assert details.model == "formula 5"
    assert fields["Coefficient c0"] == "1.5" and fields["Coefficient c1"] == "0.005"
    assert "Raw source metadata (descriptive)" in fields
    assert sum(len(text) for _, text in details.fields) <= MAX_DETAIL_TEXT
    assert len(details.fields) <= MAX_DETAIL_FIELDS
    assert any("truncated" in note for note in details.notes)
    unavailable = replace(value, owned_data=None)
    result = compare_materials(
        snapshot(unavailable),
        SpectralRequest((value.key,), interval_um=(0.4, 0.8), sample_count=3),
    )
    assert result.series[0].details is None
    assert all(point.status == "unsupported" for point in result.series[0].values)


def test_nearby_scales_signed_deltas_ties_and_missing_coordinates():
    target = replace(
        record(), nd=PropertyValue(1.5, "available"), vd=PropertyValue(60, "available")
    )
    a = replace(
        target,
        key=MaterialKey("synthetic", "Catalog", "a"),
        nd=PropertyValue(1.52, "available"),
        vd=PropertyValue(62, "available"),
    )
    b = replace(a, key=MaterialKey("synthetic", "Catalog", "b"))
    c = replace(
        target,
        key=MaterialKey("synthetic", "Catalog", "c"),
        vd=PropertyValue(58, "available"),
    )
    missing = replace(
        target,
        key=MaterialKey("synthetic", "Catalog", "missing"),
        nd=PropertyValue(None, "missing"),
    )
    result = nearby_materials(
        snapshot(target, b, missing, c, a), target.key, nd_scale=0.02, vd_scale=2
    )
    assert [match.key for match in result.matches] == [c.key, a.key, b.key]
    assert result.matches[0].delta_nd == 0 and result.matches[0].delta_vd == -2
    assert result.matches[1].distance == pytest.approx(math.sqrt(2))
    assert result.excluded_counts == (("unavailable_coordinates", 1),)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keys": (MaterialKey("s", "c", "a"),) * 2},
        {"keys": tuple(MaterialKey("s", "c", str(i)) for i in range(5))},
        {"quantity": "k"},
        {"sample_count": 1},
        {"sample_count": 2002},
        {"sample_count": True},
        {"interval_um": (0.5, 0.5)},
        {"interval_um": (0, 1)},
        {"interval_um": (0.4, math.inf)},
        {"thickness_mm": -1},
    ],
)
def test_invalid_spectral_requests_are_rejected_before_work(kwargs):
    with pytest.raises(ValueError):
        SpectralRequest(**{"keys": (), **kwargs})


def test_invalid_map_and_nearby_inputs_are_actionable():
    value = record()
    with pytest.raises(ValueError, match="view"):
        map_coordinates(snapshot(value), "unsupported")
    for scale in (0, -1, math.nan):
        with pytest.raises(ValueError, match="scale"):
            nearby_materials(snapshot(value), value.key, nd_scale=scale, vd_scale=1)
    with pytest.raises(ValueError, match="limit"):
        nearby_materials(snapshot(value), value.key, nd_scale=1, vd_scale=1, limit=101)
    with pytest.raises(ValueError, match="target"):
        nearby_materials(
            snapshot(value), MaterialKey("a", "b", "c"), nd_scale=1, vd_scale=1
        )


def test_checkpoint_cancels_during_spectral_samples():
    value = record()
    checkpoints = []

    def stop():
        checkpoints.append(True)
        if len(checkpoints) == 5:
            raise InterruptedError("Cancelled")

    with pytest.raises(InterruptedError, match="Cancelled"):
        compare_materials(
            snapshot(value),
            SpectralRequest((value.key,), sample_count=1000),
            checkpoint=stop,
        )
    assert len(checkpoints) == 5


def test_ideal_model_is_explicit_analytic_index_with_unavailable_transmission(
    set_test_backend,
):
    value = record(IdealMaterial(1.6, 0.001))
    request = SpectralRequest((value.key,), interval_um=(0.4, 0.8), sample_count=3)
    result = compare_materials(snapshot(value), request)
    assert [point.value for point in result.series[0].values] == pytest.approx(
        [1.6] * 3
    )
    assert result.series[0].details.model == "IdealMaterial"
    assert "Analytic" in result.series[0].details.notes[0]
    transmission = compare_materials(
        snapshot(value), replace(request, quantity="transmission")
    )
    assert all(point.status == "missing" for point in transmission.series[0].values)


def test_long_sampled_and_formula_definitions_have_bounded_details(set_test_backend):
    sampled = DataMaterial.from_samples(
        [0.4 + 0.01 * i for i in range(20)],
        [1.6 - 0.001 * i for i in range(20)],
        thermal={
            "kind": "schott",
            "coefficients": [1e-6, 0, 0, 0, 0, 0.1],
            "reference_temperature_c": 20,
        },
    )
    details = material_details(record(sampled))
    assert "first 12" in details.notes[0]
    assert dict(details.fields)["Index samples"] == "20"
    assert dict(details.fields)["Thermal reference temperature (°C)"] == "20.0"
    # A legitimate long polynomial remains intact in the material; only detail
    # presentation is truncated, with a visible count/limit explanation.
    polynomial = DataMaterial.from_coefficients("formula 5", [1.5] + [0, 1] * 40)
    long_details = material_details(record(polynomial))
    assert len(long_details.fields) == MAX_DETAIL_FIELDS
    assert any("field limit" in note for note in long_details.notes)


@pytest.mark.parametrize(
    "native,status",
    [
        (0, "invalid"),
        (-1, "invalid"),
        (math.inf, "invalid"),
        (1 + 2j, "unsupported"),
        ([1.5, 1.6], "unsupported"),
    ],
)
def test_invalid_native_scalar_results_create_explicit_gaps(
    monkeypatch, native, status
):
    value = record()
    monkeypatch.setattr(DataMaterial, "n", lambda *args: native)
    result = compare_materials(
        snapshot(value), SpectralRequest((value.key,), sample_count=3)
    )
    assert all(
        point.status == status and point.value is None
        for point in result.series[0].values
    )


def test_native_unimplemented_model_reports_unsupported_samples(monkeypatch):
    value = record()

    def unsupported(*args):
        raise NotImplementedError("Native model unavailable")

    monkeypatch.setattr(DataMaterial, "n", unsupported)
    result = compare_materials(
        snapshot(value), SpectralRequest((value.key,), sample_count=3)
    )
    assert all(
        point.status == "unsupported" and point.reason == "Native model unavailable"
        for point in result.series[0].values
    )


def test_nearby_overflow_is_excluded_and_partial_overflow_is_unavailable():
    target = record()
    candidate = replace(
        target,
        key=MaterialKey("synthetic", "Catalog", "huge"),
        nd=PropertyValue(1e308, "available"),
    )
    neighbors = nearby_materials(
        snapshot(target, candidate), target.key, nd_scale=1e-308, vd_scale=1
    )
    assert not neighbors.matches and neighbors.excluded_counts == (
        ("nonfinite_distance", 1),
    )
    tiny = replace(
        target,
        nd=PropertyValue(1e308, "available"),
        n_f=PropertyValue(2e-308, "available"),
        n_c=PropertyValue(1e-308, "available"),
    )
    point = map_coordinates(snapshot(tiny), "partial-fd").coordinates[0]
    assert point.y.status == "invalid" and point.y.value is None
