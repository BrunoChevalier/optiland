"""Regenerate the self-contained, synthetic Materials tutorial lens.

Run from an environment containing this Optiland revision. No vendor catalog,
network access or material registry registration is required.
"""

from __future__ import annotations

from pathlib import Path

from optiland.fileio import save_optiland_file
from optiland.materials import DataMaterial
from optiland.optic import Optic


def make_singlet() -> Optic:
    """Return a synthetic 10 mm aperture singlet with owned optical data."""
    material = DataMaterial.from_coefficients(
        "formula 5",
        [1.5, 0.005, -2],
        name="Tutorial crown",
        wavelength_range=(0.4, 0.8),
        bounds="raise",
        metadata={
            "description": "Synthetic teaching model; not a manufactured glass.",
            "provenance": [{"source": "Optiland Materials tutorial"}],
        },
    )
    lens = Optic()
    lens.surfaces.add(index=0, radius=float("inf"), thickness=float("inf"))
    lens.surfaces.add(
        index=1,
        radius=50.0,
        thickness=5.0,
        material=material,
        is_stop=True,
        comment="Front: tutorial crown follows this surface",
    )
    lens.surfaces.add(
        index=2, radius=-50.0, thickness=45.0, comment="Back: air follows this surface"
    )
    lens.surfaces.add(index=3, comment="Image")
    lens.set_aperture("EPD", 10.0)
    lens.fields.set_type("angle")
    lens.fields.add(y=0.0)
    lens.fields.add(y=3.0)
    for wavelength in (0.4861327, 0.5875618, 0.6562725):
        lens.wavelengths.add(wavelength, is_primary=wavelength == 0.5875618)
    lens.updater.update()
    return lens


if __name__ == "__main__":
    save_optiland_file(make_singlet(), Path(__file__).with_name("singlet.json"))
