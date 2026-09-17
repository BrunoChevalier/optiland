"""Captured source bytes, occurrence identity and atomic ingestion contracts."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace

import pytest

from optiland.fileio.oslo.catalog import decode_glc_catalog, read_glc_catalog
from optiland.materials import AbbeMaterial, DataMaterial, IdealMaterial
from optiland.materials.properties import (
    MaterialQuery,
    query_materials,
    reconstruct_material,
)
from optiland.materials.registry import RegistryEntry
from optiland.materials.sources import (
    GlcSource,
    OwnedEntry,
    OwnedSource,
    RegistrySource,
    capture_design,
    load_source,
)


def rii_entry(tmp_path, record_id="a", *, source_id="rii:installed", value=1.5):
    path = tmp_path / f"{record_id}.yml"
    path.write_text(
        "REFERENCES: synthetic\nDATA:\n- type: formula 5\n"
        f"  wavelength_range: 0.4 0.8\n  coefficients: {value} 0.01 -2\n",
        encoding="utf-8",
    )
    return RegistryEntry(
        source_id, record_id, "SYNTH", "glass", "SAME", "ref", str(path)
    )


def glc_bytes(*values):
    rows = [
        f"SAME 1.5 60 2.5 0 0 1 1 1 1 A A 3 3 {value} 0.01 0 0 0 0 0 1 2 0.4 0.8"
        for value in values
    ]
    return (f"26.1 {len(rows)} SYNTH\n" + "\n".join(rows) + "\n").encode()


def test_rii_bytes_are_owned_and_generation_tracks_contents(tmp_path, set_test_backend):
    entry = rii_entry(tmp_path)
    request = RegistrySource(entry.source_id, (entry,))
    first = load_source(request)
    record = first.snapshot.materials[0]
    assert not first.failed and first.diagnostics == ()
    assert record.display_name == "SAME"
    assert record.nd.value == pytest.approx(1.5 + 0.01 / 0.5875618**2)
    assert record.n_range_um == (0.4, 0.8)
    assert first == load_source(request)
    rii_entry(tmp_path, value=1.6)
    second = load_source(request)
    assert first.snapshot.stamps != second.snapshot.stamps
    assert record.key == second.snapshot.materials[0].key
    (tmp_path / "a.yml").unlink()
    restored = reconstruct_material(record)
    assert restored.n(0.5).item() == pytest.approx(1.54)
    assert query_materials(first.snapshot, MaterialQuery()).matches
    failed = load_source(request)
    assert failed.failed and len(failed.diagnostics) == 1
    assert failed.snapshot.materials[0].nd.value is None


def test_rii_identity_order_and_partial_failure_are_explicit(tmp_path):
    a = rii_entry(tmp_path, "a")
    b = rii_entry(tmp_path, "b", value=1.6)
    original = load_source(RegistrySource(a.source_id, (b, a)))
    assert original == load_source(RegistrySource(a.source_id, (a, b)))
    assert len({m.key for m in original.snapshot.materials}) == 2
    user = replace(a, source_id="rii:user")
    assert (
        load_source(RegistrySource(user.source_id, (user,))).snapshot.materials[0].key
        != original.snapshot.materials[0].key
    )
    ref_changed = load_source(
        RegistrySource(a.source_id, (replace(a, reference="new ref"), b))
    )
    assert original.snapshot.stamps != ref_changed.snapshot.stamps
    (tmp_path / "b.yml").write_text("[]", encoding="utf-8")
    partial = load_source(RegistrySource(a.source_id, (a, b)))
    assert not partial.failed
    assert len(partial.snapshot.materials) == 2
    assert partial.diagnostics[0].record_id == "b"
    assert (
        partial.snapshot.materials[1].nd.reason
        == "RII source must contain a YAML object"
    )


@pytest.mark.parametrize(
    "text", ["[]", "DATA: [", "DATA: [{type: unsupported}]", "\udcff"]
)
def test_unreadable_rii_is_failed_with_a_diagnostic_row(tmp_path, text):
    entry = rii_entry(tmp_path)
    (tmp_path / "a.yml").write_bytes(text.encode("utf-8", errors="surrogateescape"))
    result = load_source(RegistrySource(entry.source_id, (entry,)))
    assert result.failed
    assert result.diagnostics[0].record_id == entry.record_id
    assert result.snapshot.materials[0].owned_data is None


def test_inspectable_rii_without_portable_representation(tmp_path):
    entry = rii_entry(tmp_path)
    (tmp_path / "a.yml").write_text(
        "DATA:\n- type: tabulated n\n  data: |\n    0.55 1.5\n", encoding="utf-8"
    )
    result = load_source(RegistrySource(entry.source_id, (entry,)))
    assert not result.failed
    assert result.diagnostics and result.snapshot.materials[0].owned_data is None


def test_glc_decoder_owns_exact_read_bytes_and_preserves_duplicate_occurrences(
    tmp_path, set_test_backend
):
    raw = glc_bytes(1.5, 1.6)
    path = tmp_path / "same.glc"
    path.write_bytes(raw)
    decoded = decode_glc_catalog(raw, name=path)
    assert [e.material.to_dict() for e in decoded] == [
        e.material.to_dict() for e in read_glc_catalog(path)
    ]
    first = load_source(GlcSource("glc:left", str(path)))
    second = load_source(GlcSource("glc:right", str(path)))
    path.unlink()
    assert not first.failed and first.diagnostics == ()
    assert first.snapshot.stamps[0].generation == second.snapshot.stamps[0].generation
    assert len({m.key for m in first.snapshot.materials}) == 2
    assert {m.key for m in first.snapshot.materials}.isdisjoint(
        m.key for m in second.snapshot.materials
    )
    for expected, record in zip((1.52, 1.62), first.snapshot.materials, strict=True):
        owned = reconstruct_material(record)
        assert owned.n(0.5).item() == pytest.approx(expected)
        assert (
            owned.metadata["provenance"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
        )
        assert str(tmp_path) not in record.owned_data.decode()
    # No further file access is needed to decode the captured bytes.
    assert len(decode_glc_catalog(raw)) == 2


@pytest.mark.parametrize("kind", ["extension", "missing", "header", "record"])
def test_glc_failures_are_atomic(tmp_path, kind):
    path = tmp_path / ("bad.txt" if kind == "extension" else "bad.glc")
    if kind != "missing":
        path.write_bytes(b"bad" if kind == "header" else glc_bytes(1.5) + b"invalid\n")
    result = load_source(GlcSource("selected", str(path)))
    assert result.failed and not result.snapshot.materials
    assert len(result.snapshot.stamps) == len(result.diagnostics) == 1
    assert result.diagnostics[0].record_id is None


def test_glc_decoder_rejects_uncaptured_data_and_oversize(monkeypatch):
    with pytest.raises(TypeError, match="must be bytes"):
        decode_glc_catalog("26.1 0")
    monkeypatch.setattr("optiland.fileio.oslo.catalog.MAX_CATALOG_BYTES", 3)
    with pytest.raises(ValueError, match="size limit"):
        decode_glc_catalog(b"26.1 0")


@pytest.mark.parametrize(
    "data", ["DATA: [null]", "DATA: [{type: tabulated n, data: ''}]"]
)
def test_malformed_rii_shapes_do_not_erase_healthy_records(tmp_path, data):
    good = rii_entry(tmp_path, "a")
    bad = rii_entry(tmp_path, "b")
    (tmp_path / "b.yml").write_text(data, encoding="utf-8")
    result = load_source(RegistrySource(good.source_id, (good, bad)))
    assert not result.failed
    assert result.snapshot.materials[0].nd.status == "available"
    assert result.snapshot.materials[1].nd.status == "unsupported"
    assert len(result.diagnostics) == 1


def test_unlabeled_glc_generation_tracks_effective_catalog_identity(tmp_path):
    raw = glc_bytes(1.5).replace(b" SYNTH\n", b"\n", 1)
    first_path, second_path = tmp_path / "first.glc", tmp_path / "second.glc"
    first_path.write_bytes(raw)
    second_path.write_bytes(raw)
    first = load_source(GlcSource("glc", str(first_path)))
    second = load_source(GlcSource("glc", str(second_path)))
    assert first.snapshot.stamps != second.snapshot.stamps
    assert first.snapshot.materials[0].key.catalog == "first"
    assert second.snapshot.materials[0].key.catalog == "second"
    for record in (first.snapshot.materials[0], second.snapshot.materials[0]):
        assert (
            reconstruct_material(record).metadata["provenance"][0]["sha256"]
            == hashlib.sha256(raw).hexdigest()
        )
    first_path.write_bytes(glc_bytes())
    empty = load_source(GlcSource("glc", str(first_path)))
    assert not empty.failed and not empty.snapshot.materials


def test_current_design_is_detached_and_tracks_material_changes(set_test_backend):
    material = DataMaterial.from_samples([0.4, 0.8], [1.6, 1.5], name="Live")
    before = material.to_dict()
    request = capture_design(
        [("surface-a", material), ("surface-b", material), ("air", IdealMaterial(1))],
        document_id="one",
    )
    first = load_source(request)
    assert material.to_dict() == before
    assert len(first.snapshot.materials) == 3
    assert {m.key.record_id for m in first.snapshot.materials} == {
        "surface-a",
        "surface-b",
        "air",
    }
    assert first.snapshot.stamps[0].source_id == "design:one"
    changed = capture_design([("surface-a", IdealMaterial(1.7))], document_id="one")
    assert first.snapshot.stamps != load_source(changed).snapshot.stamps
    assert load_source(request) == first
    assert (
        load_source(capture_design([("surface-a", material)], document_id="two"))
        .snapshot.materials[0]
        .key.source_id
        == "design:two"
    )
    restored = reconstruct_material(
        next(m for m in first.snapshot.materials if m.key.record_id == "surface-a")
    )
    assert restored.n(0.6).item() == pytest.approx(1.55)


def test_design_abbe_is_portable_and_custom_types_are_diagnostic(set_test_backend):
    class Custom(IdealMaterial):
        pass

    source = capture_design(
        [("abbe", AbbeMaterial(1.6, 60, model="buchdahl")), ("custom", Custom(1.5))],
        document_id="one",
    )
    result = load_source(source)
    assert not result.failed
    assert len(result.diagnostics) == 1 and result.diagnostics[0].record_id == "custom"
    assert result.snapshot.materials[0].nd.value == pytest.approx(1.6)
    assert result.snapshot.materials[1].nd.status == "unsupported"


def test_owned_bytes_are_copied_and_malformed_rows_are_retained():
    payload = bytearray(b"not JSON")
    entry = OwnedEntry("a", "Malformed", payload)
    payload.clear()
    result = load_source(
        OwnedSource("owned", (entry, OwnedEntry("b", "Unknown", None, "custom law")))
    )
    assert entry.payload == b"not JSON"
    assert len(result.diagnostics) == 2 and not result.failed
    assert all(record.owned_data is None for record in result.snapshot.materials)
    with pytest.raises(FrozenInstanceError):
        entry.payload = b"changed"


@pytest.mark.parametrize(
    "make",
    [
        lambda: OwnedSource("", ()),
        lambda: OwnedEntry("", "name", b"{}"),
        lambda: OwnedEntry("a", 1, b"{}"),
        lambda: OwnedEntry("a", "name", {}),
        lambda: OwnedEntry("a", "name", None),
        lambda: OwnedEntry("a", "name", None, 1),
        lambda: GlcSource("a", ""),
        lambda: OwnedSource("a", (object(),)),
        lambda: OwnedSource("a", (OwnedEntry("a", "name", b"{}"),) * 2),
    ],
)
def test_invalid_source_contracts_fail_before_worker_submission(make):
    with pytest.raises(ValueError):
        make()


def test_source_identity_capacity_and_checkpoint_failures(tmp_path, monkeypatch):
    entry = rii_entry(tmp_path)
    with pytest.raises(ValueError, match="belong"):
        RegistrySource("other", (entry,))
    with pytest.raises(TypeError, match="Unknown"):
        load_source(object())
    with pytest.raises(ValueError, match="nonempty"):
        capture_design([], document_id="")
    monkeypatch.setattr("optiland.materials.sources.MAX_SOURCE_RECORDS", 1)
    with pytest.raises(ValueError, match="record limit"):
        capture_design(
            [("a", IdealMaterial(1.5)), ("b", IdealMaterial(1.6))], document_id="one"
        )
    with pytest.raises(ValueError, match="record limit"):
        OwnedSource("a", (OwnedEntry("a", "a", b"{}"), OwnedEntry("b", "b", b"{}")))
    monkeypatch.setattr("optiland.materials.sources.MAX_SOURCE_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        OwnedSource("a", (OwnedEntry("a", "a", b"{}"),))
    with pytest.raises(ValueError, match="owned-data byte limit"):
        load_source(RegistrySource(entry.source_id, (entry,)))
    monkeypatch.setattr("optiland.materials.sources.MAX_RII_BYTES", 1)
    result = load_source(RegistrySource(entry.source_id, (entry,)))
    assert result.failed and "byte limit" in result.diagnostics[0].message


@pytest.mark.parametrize("kind", ["rii", "glc", "owned"])
def test_cancellation_propagates_without_publishing_a_partial_source(tmp_path, kind):
    entry = rii_entry(tmp_path)
    path = tmp_path / "cancel.glc"
    path.write_bytes(glc_bytes(1.5))
    request = {
        "rii": RegistrySource(entry.source_id, (entry,)),
        "glc": GlcSource("glc", str(path)),
        "owned": capture_design([("a", IdealMaterial(1.5))], document_id="one"),
    }[kind]
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError("cancel request")

    with pytest.raises(ValueError, match="cancel request"):
        load_source(request, checkpoint=cancel)
    assert calls == 3


def test_empty_sources_are_successful_and_stamped():
    for source in (RegistrySource("rii", ()), OwnedSource("owned", ())):
        result = load_source(source)
        assert not result.failed and not result.snapshot.materials
        assert len(result.snapshot.stamps) == 1
