"""Direct and real-process material comparison jobs use owned bounded inputs."""

from __future__ import annotations

import pickle
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from optiland.materials.comparison import SpectralRequest
from optiland_gui.services import material_comparison_jobs as handlers
from optiland_gui.services.calculation_jobs import CalculationJobs, DocumentState
from optiland_gui.services.job_records import BackendConfig, CalculationCancelled
from optiland_gui.services.material_jobs import encode_query_source
from tests.gui.test_calculation_jobs import wait_for
from tests.test_material_comparison import (
    analytic,
    record,
    snapshot,
    transmission_record,
)


def parameters():
    value = record()
    other = record(analytic(coefficient=0.007), "second")
    data = snapshot(value, other)
    return {
        "material_blobs": (encode_query_source(data),),
        "stamps": data.stamps,
        "material_snapshot": data,
        "request": SpectralRequest((other.key, value.key), sample_count=5),
        "view": "partial-gf",
        "backend": BackendConfig.capture(),
        "target_key": value.key,
        "nd_scale": 0.02,
        "vd_scale": 5,
        "limit": 1,
    }


def test_direct_handlers_preserve_backend_generation_order_and_lightweight_maps(
    set_test_backend,
):
    params = parameters()
    decoded = pickle.loads(params["material_blobs"][0])
    assert all(value.owned_data is None for value in decoded)
    assert all(value.n_g.status == "available" for value in decoded)
    results = [
        handler(None, params, lambda *args: None, threading.Event())
        for handler in (
            handlers.compute_map_view,
            handlers.compute_comparison,
            handlers.compute_nearby,
        )
    ]
    assert all(result.stamps == params["stamps"] for result in results)
    assert results[0].view == "partial-gf"
    assert tuple(series.key for series in results[1].series) == params["request"].keys
    assert all(
        point.status == "available"
        for series in results[1].series
        for point in series.values
    )
    assert results[2].matches[0].key == params["request"].keys[0]
    assert BackendConfig.capture() == params["backend"]


@pytest.mark.parametrize(
    "handler",
    [handlers.compute_map_view, handlers.compute_comparison, handlers.compute_nearby],
)
def test_cancelled_handlers_do_not_decode_or_report_progress(handler):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(CalculationCancelled):
        handler(
            None,
            {},
            lambda *args: pytest.fail("Cancelled job reported progress"),
            cancelled,
        )


def test_cancellation_between_source_blobs_stops_deserialization(monkeypatch):
    params = parameters()
    params["material_blobs"] *= 2
    decoded, cancelled = [], threading.Event()
    original = pickle.loads

    def decode(blob):
        decoded.append(blob)
        cancelled.set()
        return original(blob)

    monkeypatch.setattr(handlers.pickle, "loads", decode)
    with pytest.raises(CalculationCancelled):
        handlers.compute_map_view(None, params, lambda *args: None, cancelled)
    assert len(decoded) == 1


@pytest.mark.parametrize(
    "handler,core",
    [
        (handlers.compute_map_view, "map_coordinates"),
        (handlers.compute_comparison, "compare_materials"),
        (handlers.compute_nearby, "nearby_materials"),
    ],
)
def test_cancelled_completed_core_result_is_not_returned(monkeypatch, handler, core):
    cancelled = threading.Event()

    def completed(*args, **kwargs):
        cancelled.set()
        return object()

    monkeypatch.setattr(handlers, core, completed)
    with pytest.raises(CalculationCancelled):
        handler(None, parameters(), lambda *args: None, cancelled)


def test_worker_rejects_unbounded_owned_comparison_transport():
    params = parameters()
    params["material_snapshot"] = snapshot(*(record(identity=str(i)) for i in range(5)))
    with pytest.raises(ValueError, match="at most four"):
        handlers.compute_comparison(None, params, lambda *args: None, threading.Event())


def test_corrupt_summary_transport_fails_without_result():
    params = parameters()
    params["material_blobs"] = (b"invalid transport",)
    with pytest.raises(pickle.UnpicklingError):
        handlers.compute_nearby(None, params, lambda *args: None, threading.Event())


def test_real_shared_process_runs_spectral_map_and_nearby_without_document_mutation(
    qapp,
):
    document = DocumentState()
    token = document.token
    jobs = CalculationJobs(document)
    results = []
    jobs.finished.connect(results.append)
    try:
        params = parameters()
        absorbing = transmission_record()
        spectral = {
            "material_snapshot": snapshot(absorbing),
            "request": SpectralRequest(
                (absorbing.key,),
                quantity="transmission",
                thickness_mm=10,
                sample_count=5,
            ),
            "backend": BackendConfig.capture(),
        }
        for name, data in (
            ("compute_comparison", spectral),
            ("compute_map_view", params),
            ("compute_nearby", params),
        ):
            jobs.submit(
                name,
                f"optiland_gui.services.material_comparison_jobs:{name}",
                None,
                data,
            )
        wait_for(qapp, lambda: len(results) == 3, timeout=30)
        assert all(
            result.status == "succeeded" and result.current for result in results
        )
        spectra = results[0].data
        assert [value.value for value in spectra.series[0].values] == pytest.approx(
            [0, 0.0625**2, 0.25**2, 0.5625**2, 1]
        )
        assert spectra.stamps == spectral["material_snapshot"].stamps
        assert results[1].data.view == "partial-gf"
        assert len(results[2].data.matches) == 1
        assert document.token == token
    finally:
        jobs.shutdown()
        wait_for(qapp, lambda: jobs._process is None)
        jobs.deleteLater()
        document.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
