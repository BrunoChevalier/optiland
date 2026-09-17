"""GUI-safe metadata capture and isolated worker-side source discovery."""

from __future__ import annotations

import builtins
import pickle
from dataclasses import replace
from pathlib import Path
from threading import Event

import pandas as pd
import pytest

from optiland.materials import registry as registry_module
from optiland.materials.properties import reconstruct_material
from optiland.materials.registry import MaterialRegistry
from optiland.materials.sources import (
    DirectorySource,
    InstalledSource,
    RegistrySource,
    load_source,
)
from optiland_gui.services.job_records import BackendConfig, CalculationCancelled
from optiland_gui.services.material_jobs import load_material_source


def write_material(directory, name="Exact", index=1.5):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yml"
    path.write_text(
        "REFERENCE: synthetic fixture\nDATA:\n- type: formula 5\n"
        f"  wavelength_range: 0.4 0.8\n  coefficients: {index} 0.01 -2\n",
        encoding="utf-8",
    )
    return path


def csv_row(path):
    return {
        "filename": path.name,
        "filename_no_ext": "Exact",
        "name": "Exact",
        "group": "glass",
        "reference": "CSV reference",
        "category_name": "Synthetic",
        "category_name_full": "Synthetic",
        "min_wavelength": 0.4,
        "max_wavelength": 0.8,
    }


def forbidden(*args, **kwargs):
    raise AssertionError("Metadata capture performed filesystem or plugin work")


def test_registered_metadata_is_exact_and_performs_no_io(tmp_path, monkeypatch):
    registry = MaterialRegistry()
    path = write_material(tmp_path / "Local")
    registry.register_file(path)
    expected = registry.entries(group="user")
    monkeypatch.setattr(MaterialRegistry, "_instance", registry)
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "resolve", forbidden)
        guard.setattr(Path, "is_dir", forbidden)
        guard.setattr(pd, "read_csv", forbidden)
        guard.setattr(registry_module.plugins, "load_plugins", forbidden)
        assert registry.registered_entries() == expected
        assert registry.registered_entries(group="USER", catalog="LOCAL") == expected
        assert registry.registered_entries(catalog="local.*") == ()
        assert MaterialRegistry.registered_snapshot() == expected
        # Request construction itself must not discover catalogs either.
        assert InstalledSource().source_id == "rii:installed"
        assert DirectorySource("local:test", str(path.parent)).path == str(path.parent)
    path.unlink()
    assert registry.registered_entries() == expected


def test_registered_snapshot_does_not_initialize_singleton(monkeypatch):
    monkeypatch.setattr(MaterialRegistry, "_instance", None)
    monkeypatch.setattr(MaterialRegistry, "_auto_discover", forbidden)
    monkeypatch.setattr(MaterialRegistry, "__init__", forbidden)
    assert MaterialRegistry.registered_snapshot() == ()
    assert MaterialRegistry._instance is None


def test_installed_request_uses_exact_installed_identity(monkeypatch, set_test_backend):
    registry = MaterialRegistry()
    expected = next(e for e in registry.entries(catalog="schott") if e.name == "N-BK7")
    calls = []

    def discovered(self, *, group=None, catalog=None):
        calls.append((self, group))
        return (expected, replace(expected, source_id="rii:user"))

    monkeypatch.setattr(MaterialRegistry, "entries", discovered)
    monkeypatch.setattr(MaterialRegistry, "instance", forbidden)
    result = load_material_source(
        None,
        {"source": InstalledSource(), "backend": BackendConfig.capture()},
        lambda *args, **kwargs: None,
        Event(),
    )
    assert len(calls) == 1 and calls[0][1] == "glass"
    assert calls[0][0] is not registry
    assert not result.failed and result.diagnostics == ()
    (material,) = result.snapshot.materials
    assert material.key.source_id == expected.source_id
    assert material.key.record_id == expected.record_id
    assert material.key.catalog == expected.catalog
    assert material.nd.value == pytest.approx(1.5168, abs=5e-5)


def test_gui_registered_records_do_not_depend_on_worker_singleton(
    tmp_path, monkeypatch, set_test_backend
):
    registry = MaterialRegistry()
    path = write_material(tmp_path / "Private", index=1.7)
    registry.register_file(path)
    request = RegistrySource("rii:user", registry.registered_entries())
    request = pickle.loads(pickle.dumps(request))
    monkeypatch.setattr(MaterialRegistry, "_instance", MaterialRegistry())
    monkeypatch.setattr(MaterialRegistry, "instance", forbidden)
    result = load_material_source(
        None,
        {"source": request, "backend": BackendConfig.capture()},
        lambda *args, **kwargs: None,
        Event(),
    )
    assert not result.failed
    assert result.snapshot.materials[0].nd.value == pytest.approx(
        1.7 + 0.01 / 0.5875618**2
    )
    assert MaterialRegistry.registered_snapshot() == ()


@pytest.mark.parametrize("indexed", [False, True])
def test_directory_ingestion_is_isolated_and_owned(
    tmp_path, monkeypatch, indexed, set_test_backend
):
    path = write_material(tmp_path / "Local")
    if indexed:
        pd.DataFrame([csv_row(path), csv_row(path)]).to_csv(
            path.parent / "catalog.csv", index=False
        )
    singleton = MaterialRegistry()
    monkeypatch.setattr(MaterialRegistry, "_instance", singleton)
    monkeypatch.setattr(MaterialRegistry, "instance", forbidden)
    request = DirectorySource("local:one", str(path.parent))
    result = load_source(request)
    assert not result.failed and result.diagnostics == ()
    records = result.snapshot.materials
    assert len(records) == (2 if indexed else 1)
    assert len({record.key for record in records}) == len(records)
    assert {record.key.source_id for record in records} == {"local:one"}
    assert singleton.registered_entries() == ()
    assert load_source(request) == result
    other = load_source(DirectorySource("local:two", str(path.parent)))
    assert {record.key for record in records}.isdisjoint(
        record.key for record in other.snapshot.materials
    )
    path.unlink()
    assert reconstruct_material(records[0]).n(0.5).item() == pytest.approx(1.54)


@pytest.mark.parametrize(
    "kind", ["missing", "empty", "bad_csv", "bad_yaml", "bad_data"]
)
def test_directory_failure_does_not_publish_partial_catalog(tmp_path, kind):
    directory = tmp_path / "Catalog"
    if kind != "missing":
        directory.mkdir()
    if kind in {"bad_yaml", "bad_data"}:
        write_material(directory, "Good")
        (directory / "Bad.yml").write_text(
            "DATA: [" if kind == "bad_yaml" else "DATA: [{type: unknown}]",
            encoding="utf-8",
        )
    if kind == "bad_csv":
        (directory / "catalog.csv").write_text("incorrect\nvalue\n", encoding="utf-8")
    result = load_source(DirectorySource("local:staged", str(directory)))
    assert result.failed and result.diagnostics
    assert result.snapshot.stamps[0].source_id == "local:staged"


def test_directory_file_failure_after_index_discovery_is_staged(tmp_path):
    path = write_material(tmp_path / "Local")
    pd.DataFrame([csv_row(path), csv_row(path) | {"filename": "missing.yml"}]).to_csv(
        path.parent / "catalog.csv", index=False
    )
    result = load_source(DirectorySource("local:staged", str(path.parent)))
    assert result.failed
    assert len(result.snapshot.materials) == 2
    assert len(result.diagnostics) == 1
    assert sum(m.owned_data is not None for m in result.snapshot.materials) == 1


@pytest.mark.parametrize(
    "factory, arguments",
    [
        (InstalledSource, {"source_id": "user"}),
        (InstalledSource, {"group": ""}),
        (DirectorySource, {"source_id": "", "path": "x"}),
        (DirectorySource, {"source_id": "local", "path": ""}),
    ],
)
def test_source_request_validation(factory, arguments):
    with pytest.raises(ValueError):
        factory(**arguments)


@pytest.mark.parametrize("kind", ["installed", "directory"])
def test_cancellation_after_discovery_propagates_without_partial_result(
    tmp_path, monkeypatch, kind
):
    calls = []

    def checkpoint():
        calls.append(None)
        if len(calls) == 2:
            raise CalculationCancelled()

    if kind == "installed":
        monkeypatch.setattr(MaterialRegistry, "entries", lambda *args, **kwargs: ())
        request = InstalledSource()
    else:
        path = write_material(tmp_path / "Local")
        request = DirectorySource("local:staged", str(path.parent))
    with pytest.raises(CalculationCancelled):
        load_source(request, checkpoint=checkpoint)


def test_absolute_index_locator_identity_matches_registered_metadata(tmp_path):
    path = write_material(tmp_path / "Local")
    pd.DataFrame([csv_row(path) | {"filename": str(path)}]).to_csv(
        path.parent / "catalog.csv", index=False
    )
    registry = MaterialRegistry()
    registry.load_catalog(path.parent)
    assert registry.registered_entries() == registry.entries(catalog="local")
