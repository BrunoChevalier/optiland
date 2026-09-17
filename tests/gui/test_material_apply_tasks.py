"""Owned material preparation preserves native identity and optical dependencies."""

from __future__ import annotations

import threading

import pytest

from optiland.materials import DataMaterial, IdealMaterial
from optiland.materials.portability import capture_material
from optiland_gui.services.job_records import CalculationCancelled
from optiland_gui.services.material_apply_tasks import prepare_material_change
from optiland_gui.services.prepared_optic import PreparedOptic


def payload(*, bounds="raise", support=(0.4, 0.8)):
    return capture_material(
        DataMaterial.from_coefficients(
            "formula 5",
            [1.55, 0.005, -2],
            name="Exact owned glass",
            wavelength_range=support,
            bounds=bounds,
        )
    )


def prepare(optic, *, row=1, data=None):
    return prepare_material_change(
        PreparedOptic.capture(optic),
        {"row": row, "payload": payload() if data is None else data},
        lambda *args: None,
        threading.Event(),
    )


def test_owned_apply_preserves_surfaces_and_propagates_next_medium(
    minimal_optic, set_test_backend
):
    optic = minimal_optic
    original = optic.surfaces[1].material_post
    result = prepare(optic)
    candidate = result.candidate.restore()
    assert result.outcome == "prepared"
    assert result.row_mapping == ((0, 0), (1, 1), (2, 2), (3, 3))
    assert result.affected_rows == (1, 2)
    assert optic.surfaces[1].material_post is original
    assert capture_material(candidate.surfaces[1].material_post) == payload()
    assert candidate.surfaces[2].material_pre is candidate.surfaces[1].material_post
    assert (
        candidate.surfaces[1].material_post.propagation_model.material
        is candidate.surfaces[1].material_post
    )
    assert result.native_snapshot is not None


def test_exact_owned_noop_does_not_prepare_history_candidate(minimal_optic):
    candidate = prepare(minimal_optic).candidate.restore()
    result = prepare(candidate)
    assert result.outcome == "unchanged"
    assert result.candidate is None and result.native_snapshot is None


def test_object_medium_supported_image_rejected(minimal_optic):
    assert prepare(minimal_optic, row=0).outcome == "prepared"
    with pytest.raises(ValueError, match="image"):
        prepare(minimal_optic, row=3)


def test_known_support_rejects_clamp_and_unknown_ideal_is_explicit(minimal_optic):
    with pytest.raises(ValueError, match="outside known n support"):
        prepare(minimal_optic, data=payload(bounds="clamp", support=(0.6, 0.8)))
    result = prepare(minimal_optic, data=capture_material(IdealMaterial(1.6)))
    assert result.warnings == ("n support is analytic.", "k support is analytic.")


def test_material_pickup_and_unproven_generic_write_are_rejected(minimal_optic):
    minimal_optic.pickups.add(2, "surfaces.surfaces[i].material_post", 1)
    with pytest.raises(ValueError, match="pickup controls"):
        prepare(minimal_optic)


def test_builtin_geometry_pickup_is_preserved(minimal_optic):
    minimal_optic.pickups.add(1, "radius", 2, scale=-1)
    candidate = prepare(minimal_optic).candidate.restore()
    assert len(candidate.pickups) == 1
    assert float(candidate.surfaces[2].geometry.radius) == -50.0


@pytest.mark.parametrize("mutation", ["reorder", "overwrite", "nonfinite"])
def test_updater_cannot_change_identity_material_or_finite_geometry(
    minimal_optic, monkeypatch, mutation
):
    from optiland.optic.optic_updater import OpticUpdater

    def update(updater):
        if mutation == "reorder":
            updater.optic.surfaces._surfaces.reverse()
        elif mutation == "overwrite":
            updater.optic.surfaces[1].material_post = IdealMaterial(1.7)
        else:
            updater.optic.surfaces[1].geometry.cs.x = float("nan")

    monkeypatch.setattr(OpticUpdater, "update", update)
    with pytest.raises(ValueError, match="identity or order|overwrote|nonfinite"):
        prepare(minimal_optic)


def test_reflective_target_and_external_pickup_rejected(minimal_optic):
    minimal_optic.surfaces[1].interaction_model.is_reflective = True
    with pytest.raises(ValueError, match="reflective"):
        prepare(minimal_optic)
    minimal_optic.surfaces[1].interaction_model.is_reflective = False
    external = PreparedOptic.capture(minimal_optic).restore()
    minimal_optic.pickups.add(1, "radius", 2, source_optic=external)
    with pytest.raises(ValueError, match="external-optic"):
        prepare(minimal_optic)


def test_cancelled_preparation_does_not_restore_or_update(minimal_optic, monkeypatch):
    snapshot = PreparedOptic.capture(minimal_optic)
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        PreparedOptic, "restore", lambda _: pytest.fail("Cancelled restore")
    )
    with pytest.raises(CalculationCancelled):
        prepare_material_change(snapshot, {}, lambda *args: None, cancelled)


def test_independent_k_support_and_invalid_index_are_rejected(minimal_optic):
    material = DataMaterial.from_coefficients(
        "formula 5",
        (1.6,),
        wavelength_range=(0.4, 0.8),
        extinction={
            "kind": "tabulated_k",
            "wavelengths_um": (0.6, 0.8),
            "values": (0.001, 0.002),
        },
    )
    with pytest.raises(ValueError, match="outside known k support"):
        prepare(minimal_optic, data=capture_material(material))
    with pytest.raises(ValueError, match="n is invalid"):
        prepare(minimal_optic, data=capture_material(IdealMaterial(-1.0)))


def test_instance_modified_material_setter_is_rejected_without_calling_it(
    minimal_optic,
):
    from optiland_gui.services.material_apply_tasks import validate_target

    minimal_optic.updater.set_material = lambda *args: pytest.fail(
        "Custom setter invoked"
    )
    with pytest.raises(ValueError, match="custom updater"):
        validate_target(minimal_optic, 1)


def test_dependency_cannot_produce_nan_curvature(minimal_optic, monkeypatch):
    from optiland.optic.optic_updater import OpticUpdater

    def update(updater):
        updater.optic.surfaces[2].geometry.radius = float("nan")

    monkeypatch.setattr(OpticUpdater, "update", update)
    with pytest.raises(ValueError, match="invalid surface geometry"):
        prepare(minimal_optic)


def test_builtin_solve_is_preserved_and_custom_solve_is_rejected(minimal_optic):
    from types import SimpleNamespace

    from optiland_gui.services.material_apply_tasks import validate_target

    minimal_optic.solves.add("quick_focus")
    candidate = prepare(minimal_optic).candidate.restore()
    assert len(candidate.solves) == 1
    candidate.solves.solves.append(SimpleNamespace(optic=candidate))
    with pytest.raises(ValueError, match="custom or external-optic solve"):
        validate_target(candidate, 1)


def test_invalid_row_generic_pickup_and_missing_wavelengths(minimal_optic):
    from optiland_gui.services.material_apply_tasks import validate_target

    with pytest.raises(ValueError, match="no longer available"):
        validate_target(minimal_optic, -1)
    minimal_optic.pickups.add(1, "surfaces.surfaces[i].geometry.radius", 2)
    with pytest.raises(ValueError, match="write path"):
        prepare(minimal_optic)
    minimal_optic.pickups.pickups.clear()
    minimal_optic.wavelengths.wavelengths.clear()
    with pytest.raises(ValueError, match="Configure finite positive wavelengths"):
        prepare(minimal_optic)
