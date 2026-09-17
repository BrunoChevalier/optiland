"""Optimize an owned synthetic glass pool through the public, Qt-free API.

Run with ``python typed_glass_expert.py`` from any working directory. No catalog,
network, GUI, or adjacent sample file is needed. The two pool records deliberately
share a display name; their exact keys and native definitions remain distinct.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import optiland.backend as be
from optiland.materials import DataMaterial
from optiland.materials.properties import MaterialKey, SourceStamp, describe_material
from optiland.optic import Optic
from optiland.optimization import (
    GlassExpert,
    MaterialPool,
    OptimizationProblem,
    prepare_material_candidate,
)

WAVELENGTHS_UM = (0.4861327, 0.5875618, 0.6562725)


def make_pool() -> MaterialPool:
    """Capture two owned formulae, with a content-derived source generation."""
    summaries = tuple(
        describe_material(
            DataMaterial.from_coefficients(
                "formula 5",
                [offset, dispersion, -2],
                name="Synthetic crown",
                wavelength_range=(0.4, 0.8),
                bounds="raise",
            ),
            MaterialKey("tutorial:owned", "synthetic", record_id),
        )
        for record_id, offset, dispersion in (
            ("crown-a", 1.50, 0.010),
            ("crown-b", 1.55, 0.015),
        )
    )
    # The stamp belongs to this source snapshot. Neither the label nor n_d/V_d
    # is used as a resolver key.
    generation = hashlib.sha256(
        b"\n".join(summary.owned_data for summary in summaries)
    ).hexdigest()
    stamp = SourceStamp("tutorial:owned", generation)
    return MaterialPool(
        tuple(
            prepare_material_candidate(summary, stamp, wavelengths_um=WAVELENGTHS_UM)
            for summary in summaries
        )
    )


def make_problem(pool: MaterialPool) -> tuple[Optic, OptimizationProblem]:
    """Keep the starting glass outside the eligible pool as a reference only."""
    baseline = DataMaterial.from_coefficients(
        "formula 5",
        [1.50, 0.005, -2],
        name="Reference crown",
        wavelength_range=(0.4, 0.8),
        bounds="raise",
    )
    lens = Optic()
    lens.surfaces.add(index=0, radius=float("inf"), thickness=float("inf"))
    lens.surfaces.add(
        index=1, radius=50.0, thickness=5.0, material=baseline, is_stop=True
    )
    lens.surfaces.add(index=2, radius=-50.0, thickness=45.0)
    lens.surfaces.add(index=3)
    lens.set_aperture("EPD", 10.0)
    lens.fields.set_type("angle")
    lens.fields.add(y=0.0)
    for wavelength in WAVELENGTHS_UM:
        lens.wavelengths.add(wavelength, is_primary=wavelength == WAVELENGTHS_UM[1])
    lens.updater.update()

    problem = OptimizationProblem()
    problem.add_variable(lens, "radius", surface_number=1, min_val=20.0, max_val=150.0)
    problem.add_variable(lens, "material", surface_number=1, glass_selection=pool)
    problem.add_operand("f2", target=60.0, weight=1.0, input_data={"optic": lens})
    return lens, problem


def run_example():
    """Return the direct caller's optimized lens and complete typed report."""
    be.set_backend("numpy")
    # JSON round-trip demonstrates that the pool contains owned definitions,
    # rather than names that need ambient catalog registration.
    pool = MaterialPool.from_dict(json.loads(json.dumps(make_pool().to_dict())))
    lens, problem = make_problem(pool)
    original_manager = problem.variables
    result = GlassExpert(problem).run(
        num_neighbours=1,
        global_samples=2,
        seed=1234,
        nd_scale=0.05,
        vd_scale=20.0,
        maxiter=40,
        tol=1e-8,
        disp=False,
        verbose=False,
        variable_ids=("front-radius", "front-material"),
    )
    assert problem.variables is original_manager
    return lens, result.glass_expert


if __name__ == "__main__":
    _, report = run_example()
    print(json.dumps(asdict(report), indent=2, allow_nan=False))
