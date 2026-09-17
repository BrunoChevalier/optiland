"""Detached material handlers preserve backend, cancellation and source stamps."""

from __future__ import annotations

import json
import threading

import pytest

import optiland.backend as be
from optiland.materials import DataMaterial
from optiland.materials.properties import MaterialQuery
from optiland.materials.sources import OwnedEntry, OwnedSource, load_source
from optiland_gui.services import material_jobs
from optiland_gui.services.job_records import BackendConfig, CalculationCancelled


def owned_source(source_id="catalog:a", names=("Crown", "Flint")):
    return OwnedSource(
        source_id,
        tuple(
            OwnedEntry(
                str(index),
                name,
                json.dumps(
                    DataMaterial.from_coefficients(
                        "formula 5",
                        [1.5 + index * 0.1, 0.02, -2],
                        name=name,
                        wavelength_range=(0.4, 0.8),
                    ).to_dict()
                ).encode(),
                None,
            )
            for index, name in enumerate(names)
        ),
    )


def test_handlers_return_portable_data_and_exact_query_stamps(set_test_backend):
    config = BackendConfig.capture()
    result = material_jobs.load_material_source(
        None,
        {"source": owned_source(), "backend": config},
        lambda *args: None,
        threading.Event(),
    )
    assert not result.failed
    assert len(result.snapshot.materials) == 2
    assert result.snapshot.materials[0].owned_data is not None
    query = material_jobs.filter_materials(
        None,
        {
            "materials": result.snapshot,
            "query": MaterialQuery(name="Flint"),
            "request_id": 17,
            "backend": config,
        },
        lambda *args: None,
        threading.Event(),
    )
    assert query.request_id == 17
    assert query.stamps == result.snapshot.stamps
    assert len(query.matches) == 1
    assert query.matches[0].key.record_id == "1"
    assert be.get_backend() == config.name


@pytest.mark.parametrize(
    "handler", [material_jobs.load_material_source, material_jobs.filter_materials]
)
def test_handlers_do_no_work_after_initial_cancel(handler):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(CalculationCancelled):
        handler(
            None,
            {},
            lambda *args: pytest.fail("Cancelled work reported progress"),
            cancelled,
        )


def test_source_handler_checks_cancellation_inside_loader(monkeypatch):
    cancelled = threading.Event()

    def stop(request, *, checkpoint):
        cancelled.set()
        checkpoint()

    monkeypatch.setattr(material_jobs, "load_source", stop)
    with pytest.raises(CalculationCancelled):
        material_jobs.load_material_source(
            None,
            {"source": owned_source(), "backend": BackendConfig()},
            lambda *args: None,
            cancelled,
        )


@pytest.mark.parametrize(
    "handler,name",
    [
        (material_jobs.load_material_source, "load_source"),
        (material_jobs.filter_materials, "query_materials"),
    ],
)
def test_completed_but_cancelled_calculation_is_not_returned(
    monkeypatch, handler, name
):
    cancelled = threading.Event()

    def stop(*args, **kwargs):
        cancelled.set()
        return object()

    monkeypatch.setattr(material_jobs, name, stop)
    with pytest.raises(CalculationCancelled):
        handler(
            None,
            {
                "source": owned_source(),
                "backend": BackendConfig(),
                "materials": load_source(owned_source()).snapshot,
                "query": MaterialQuery(),
                "request_id": 1,
            },
            lambda *args: None,
            cancelled,
        )
