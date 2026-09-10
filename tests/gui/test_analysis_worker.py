"""Real registry coverage for detached numerical and prepared-plot results."""

from __future__ import annotations

import pickle
import threading

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from optiland_gui.registry import ANALYSIS_REGISTRY
from optiland_gui.services.analysis_plots import draw_analysis_plot
from optiland_gui.services.analysis_worker import (
    normalized_parameters,
    prepare_analysis,
    validate_workload,
)
from optiland_gui.services.job_records import CalculationCancelled, OpticSnapshot


def small_parameters(name):
    defaults = normalized_parameters(name, {})
    overrides = {
        "num_points": 16,
        "num_fields": 3,
        "num_rings": 3,
        "num_steps": 3,
        "num_rays": 32,
        "grid_size": 64,
        "image_size": 64,
        "num_terms": 9,
    }
    return {k: v for k, v in overrides.items() if k in defaults}


def assert_plain(value):
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str)
            assert_plain(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_plain(item)
    else:
        assert value is None or isinstance(
            value, (str, bool, int, float, np.ndarray, np.number)
        )


@pytest.mark.parametrize("name", [name for _, name, _ in ANALYSIS_REGISTRY])
@pytest.mark.parametrize("use_defaults", [False, True], ids=["small", "defaults"])
def test_every_registry_family_prepares_owned_numeric_plots(
    name, use_defaults, minimal_optic
):
    minimal_optic.fields.add(y=1.0)
    minimal_optic.updater.update()
    original = pickle.dumps(minimal_optic.to_dict(), protocol=5)
    result = prepare_analysis(
        OpticSnapshot.capture(minimal_optic),
        {
            "name": name,
            "constructor_args": {} if use_defaults else small_parameters(name),
            "view_args": {},
            "theme": "dark",
        },
        lambda *a, **k: None,
        threading.Event(),
    )
    assert_plain(result)
    assert result["plot"]["axes"]
    assert pickle.dumps(minimal_optic.to_dict(), protocol=5) == original
    # Present the result twice, including a theme change, without an analysis object.
    before = pickle.dumps(result, protocol=5)
    for theme in ("dark", "light"):
        figure = Figure(figsize=result["plot"]["figsize"])
        FigureCanvasAgg(figure)
        draw_analysis_plot(figure, result["plot"], theme)
        figure.canvas.draw()
        assert len(figure.axes) == len(result["plot"]["axes"])
        for actual, expected in zip(figure.axes, result["plot"]["axes"], strict=True):
            np.testing.assert_allclose(actual.get_xlim(), expected["xlim"])
            np.testing.assert_allclose(actual.get_ylim(), expected["ylim"])
            for line, data in zip(actual.lines, expected["lines"], strict=True):
                np.testing.assert_allclose(
                    line.get_xydata(), data["xy"], equal_nan=True
                )
            for image, data in zip(actual.images, expected["images"], strict=True):
                np.testing.assert_allclose(
                    image.get_array(), data["values"], equal_nan=True
                )
            if expected["legend"]:
                assert [t.get_text() for t in actual.get_legend().get_texts()] == [
                    item["label"] for item in expected["legend"]["handles"]
                ]
    assert pickle.dumps(result, protocol=5) == before


def test_fft_factory_defaults_and_no_parameter_loss():
    params = normalized_parameters("FFT PSF", {"num_rays": 64})
    assert params["field"] == (0.0, 0.0)
    assert params["wavelength"] == "primary"
    assert params["num_rays"] == 64
    with pytest.raises(ValueError, match="Unsupported"):
        normalized_parameters("FFT PSF", {"mistyped": 1})


def test_dimension_aware_workload_rejected_before_allocating():
    with pytest.raises(ValueError, match="512 MiB"):
        validate_workload("FFT PSF", {"num_rays": 10000}, {}, 10, 1, 1)
    with pytest.raises(ValueError, match="positive integer"):
        validate_workload("Ray Fan", {"num_points": -1}, {}, 10, 1, 1)
    with pytest.raises(ValueError, match="64 field/focus"):
        validate_workload(
            "Through-Focus Spot", {"num_rings": 3, "num_steps": 40}, {}, 10, 2, 1
        )
    with pytest.raises(ValueError, match="512 MiB"):
        validate_workload(
            "Encircled Energy",
            {"num_rays": 100000, "distribution": "grid"},
            {},
            10,
            1,
            1,
        )
    with pytest.raises(ValueError, match="explicit image_size"):
        validate_workload(
            "MMDFT PSF", {"num_rays": 32, "pixel_pitch": 1e-12}, {}, 10, 1, 1
        )
    with pytest.raises(ValueError, match="512 MiB"):
        validate_workload("OPD", {"num_rays": 12}, {"num_points": 100000}, 10, 1, 1)


def test_worker_cancels_before_calculation(minimal_optic):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(CalculationCancelled):
        prepare_analysis(
            OpticSnapshot.capture(minimal_optic),
            {"name": "FFT PSF", "constructor_args": {}, "view_args": {}},
            lambda *a, **k: None,
            cancelled,
        )


@pytest.mark.parametrize("name", ["FFT PSF", "Huygens PSF"])
def test_polarized_factory_backend_payloads(set_test_backend, name, minimal_optic):
    from optiland.rays import PolarizationState

    minimal_optic.polarization = PolarizationState(is_polarized=False)
    parameters = {"num_rays": 16}
    parameters["grid_size" if name == "FFT PSF" else "image_size"] = 32
    result = prepare_analysis(
        OpticSnapshot.capture(minimal_optic),
        {"name": name, "constructor_args": parameters, "view_args": {}},
        lambda *a, **k: None,
        threading.Event(),
    )
    assert_plain(result)
    assert result["plot"]["axes"][0]["images"]
    assert "Vectorial" in result["plot"]["axes"][0]["title"]
