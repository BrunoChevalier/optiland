"""Exact discovery retains catalog identity instead of repeating name lookup."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest
import yaml

import optiland.backend as be
from optiland.materials import _material_matching
from optiland.materials.registry import MaterialRegistry
from optiland.materials.warnings import OptilandMaterialWarning


def write_glass(directory, name="Shared", reference="Reference [a+b]", index=1.5):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "REFERENCE": reference,
                "DATA": [
                    {
                        "type": "formula 5",
                        "coefficients": str(index),
                        "wavelength_range": "0.4 0.8",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_discovery_retains_equal_names_and_opens_exact_file(tmp_path, set_test_backend):
    registry = MaterialRegistry()
    first = write_glass(tmp_path / "Catalog.A", index=1.5)
    second = write_glass(tmp_path / "Catalog.B", index=1.7)
    registry.register_file(first)
    registry.register_file(second)
    entries = registry.entries(group="USER")
    assert len(entries) == 2
    assert {entry.name for entry in entries} == {"Shared"}
    assert {entry.source_id for entry in entries} == {"rii:user"}
    assert len({entry.record_id for entry in entries}) == 2
    assert entries[0].reference == "Reference [a+b]"
    assert be.to_numpy(entries[0].open_material().n(0.55)).item() == pytest.approx(1.5)
    assert be.to_numpy(entries[1].open_material().n(0.55)).item() == pytest.approx(1.7)
    assert registry.entries(catalog="CATALOG.A") == (entries[0],)
    assert registry.entries(catalog="Catalog.") == ()
    assert registry.entries(group="us.*") == ()


def test_discovery_ids_survive_filters_and_unrelated_registration(tmp_path):
    registry = MaterialRegistry()
    registry.register_file(write_glass(tmp_path / "First"))
    original = registry.entries(group="user")
    registry.register_file(write_glass(tmp_path / "Second"))
    assert registry.entries(catalog="First") == original
    with pytest.raises(FrozenInstanceError):
        original[0].name = "changed"
    assert registry.entries(catalog="First")[0].name == "Shared"


def test_repeated_registrations_and_distinct_references_remain_visible(tmp_path):
    registry = MaterialRegistry()
    first = write_glass(tmp_path / "one" / "Same", reference="R1")
    second = write_glass(tmp_path / "two" / "Same", reference="R2")
    registry.register_file(first)
    with pytest.warns(OptilandMaterialWarning):
        registry.register_file(second)
    with pytest.warns(OptilandMaterialWarning):
        registry.register_file(first)
    entries = registry.entries(catalog="same")
    assert len(entries) == 3
    assert len({entry.record_id for entry in entries}) == 3
    assert [entry.reference for entry in entries] == ["R1", "R2", "R1"]
    assert entries[0].path == entries[2].path
    assert registry.entries(group="user") == entries


def test_builtin_entry_is_installed_and_can_be_resolved_without_search(monkeypatch):
    registry = MaterialRegistry()
    entry = next(e for e in registry.entries(catalog="SCHOTT") if e.name == "N-BK7")
    assert entry.source_id == "rii:installed"
    assert entry.group == "glass"

    def forbidden(*args, **kwargs):
        raise AssertionError("Discovery entry unexpectedly used name lookup")

    monkeypatch.setattr(MaterialRegistry, "resolve", forbidden)
    monkeypatch.setattr(MaterialRegistry, "_resolve_with_row", forbidden)
    material = entry.open_material(bounds="raise")
    assert material.bounds == "raise"
    assert be.to_numpy(material.n(0.5875618)).item() == pytest.approx(1.5168, abs=5e-5)


def test_csv_catalog_origin_is_user_even_when_group_is_glass(tmp_path):
    path = write_glass(tmp_path, reference="unused")
    pd.DataFrame(
        [
            {
                "group": "glass",
                "category_name": "custom",
                "category_name_full": "custom",
                "name": "Shared",
                "filename_no_ext": "Shared",
                "reference": None,
                "filename": path.name,
                "min_wavelength": 0.4,
                "max_wavelength": 0.8,
            }
        ]
    ).to_csv(tmp_path / "catalog.csv", index=False)
    registry = MaterialRegistry()
    registry.load_catalog(tmp_path)
    (entry,) = registry.entries(catalog=tmp_path.name)
    assert entry.source_id == "rii:user"
    assert entry.group == "glass"
    assert entry.reference is None
    assert entry.path == str(path.resolve())


def test_entry_is_local_locator_and_does_not_cache_file_contents(tmp_path):
    registry = MaterialRegistry()
    path = write_glass(tmp_path / "local")
    registry.register_file(path)
    (entry,) = registry.entries(group="user")
    material = entry.open_material()
    write_glass(path.parent, index=1.6)
    assert be.to_numpy(material.n(0.55)).item() == pytest.approx(1.5)
    assert be.to_numpy(entry.open_material().n(0.55)).item() == pytest.approx(1.6)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        entry.open_material()


def test_user_shadow_does_not_hide_installed_discovery_records(tmp_path):
    registry = MaterialRegistry()
    installed = registry.entries(catalog="schott")
    path = write_glass(tmp_path / "schott", name="N-BK7", index=1.7)
    with pytest.warns(OptilandMaterialWarning, match="shadows a built-in"):
        registry.register_file(path)
    all_entries = registry.entries(catalog="schott")
    assert tuple(e for e in all_entries if e.source_id == "rii:installed") == installed
    (user,) = (e for e in all_entries if e.source_id == "rii:user")
    assert user.name == "N-BK7"
    assert registry.resolve("N-BK7", catalog="schott", match_policy="strict") == str(
        path
    )
    assert be.to_numpy(user.open_material().n(0.55)).item() == pytest.approx(1.7)


def test_installed_identity_is_independent_of_package_location(monkeypatch, tmp_path):
    registry = MaterialRegistry()
    before = registry.entries(catalog="schott")
    monkeypatch.setattr(_material_matching, "_DATA_NK_DIR", str(tmp_path))
    after = registry.entries(catalog="schott")
    assert [e.record_id for e in after] == [e.record_id for e in before]
    assert [e.source_id for e in after] == [e.source_id for e in before]
    assert all(
        left.path != right.path for left, right in zip(before, after, strict=True)
    )
