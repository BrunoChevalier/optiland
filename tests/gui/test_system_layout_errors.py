"""Layout failures expose their cause and recover after correcting the input."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from optiland.coatings import PolarizerCoating
from optiland_gui.optiland_connector import OptilandConnector
from optiland_gui.system_properties_panel import PolarizationEditor
from optiland_gui.viewer_panel import MatplotlibViewer, Rays2D


@pytest.fixture
def viewer(qapp, set_test_backend, minimal_optic):
    connector = OptilandConnector()
    connector.load_optic_from_object(minimal_optic)
    widget = MatplotlibViewer(connector)
    yield widget
    widget.close()


def test_missing_polarization_explains_required_setting(viewer, caplog, set_test_backend):
    optic = viewer.connector.get_optic()
    optic.surfaces[1].interaction_model.coating = PolarizerCoating()

    with caplog.at_level(logging.ERROR, logger="optiland_gui.viewer_panel"):
        viewer.plot_optic()

    text = viewer.ax.texts[0]
    assert "Polarization must be set" in text.get_text()
    assert "System Properties > Polarization" in text.get_text()
    assert "Apply Polarization" in text.get_text()
    assert text.get_transform() is viewer.ax.transAxes
    assert optic.polarization == "ignore"
    assert not viewer._is_plotting
    assert any(record.exc_info and record.exc_info[0] is ValueError for record in caplog.records)


@pytest.mark.parametrize("mode", ["Unpolarized", "Polarized"])
def test_applying_polarization_restores_layout(viewer, mode, set_test_backend):
    optic = viewer.connector.get_optic()
    optic.surfaces[1].interaction_model.coating = PolarizerCoating()
    viewer.plot_optic()
    assert "Polarization must be set" in viewer.ax.texts[0].get_text()

    editor = PolarizationEditor(viewer.connector)
    try:
        editor.load_data()
        editor.cmbMode.setCurrentText(mode)
        if mode == "Polarized":
            editor.spnEx.setValue(1.0)
        editor.btnApply.click()
        viewer.plot_optic()
        assert not editor.lblError.isVisible()
        assert optic.polarization != "ignore"
        assert not any("Error plotting system" in text.get_text() for text in viewer.ax.texts)
        assert viewer.ax.get_title().endswith("(2D)")
        assert viewer.ax.lines
        assert not viewer._is_plotting
    finally:
        editor.close()


@pytest.mark.parametrize("error", [RuntimeError("Deliberate rendering failure"), RuntimeError()])
def test_plot_error_clears_partial_output_and_retains_reason(viewer, error, caplog):
    def fail_after_drawing(ax, **kwargs):
        ax.plot([1000, 2000], [3000, 4000])
        raise error

    with patch.object(Rays2D, "plot", side_effect=fail_after_drawing), caplog.at_level(
        logging.ERROR, logger="optiland_gui.viewer_panel"
    ):
        viewer.plot_optic()

    text = viewer.ax.texts[0]
    assert (str(error) or "RuntimeError") in text.get_text()
    assert "Polarization" not in text.get_text()
    assert text.get_transform() is viewer.ax.transAxes
    assert not viewer.ax.lines
    assert not viewer._is_plotting
    assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)
