"""Tests for SurfaceService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from optiland_gui.services.surface_service import SurfaceService


@pytest.fixture()
def mock_connector(minimal_optic):
    """A minimal mock connector wired to a real Optic."""
    conn = MagicMock()
    conn._optic = minimal_optic
    conn._capture_optic_state.return_value = {}
    conn._restore_optic_state.return_value = None
    conn._undo_redo_manager = MagicMock()
    conn.set_modified.return_value = None
    conn.opticChanged = MagicMock()
    conn.opticChanged.emit.return_value = None
    conn.notify_change.side_effect = lambda *args, **kwargs: conn.opticChanged.emit()
    # Column constants
    conn.COL_COMMENT = 1
    conn.COL_RADIUS = 2
    conn.COL_THICKNESS = 3
    conn.COL_MATERIAL = 4
    conn.COL_CONIC = 5
    conn.COL_SEMI_DIAMETER = 6
    return conn


@pytest.fixture()
def service(mock_connector):
    return SurfaceService(mock_connector)


class TestSurfaceService:
    @pytest.mark.parametrize('kind', ['samples', 'formula'])
    def test_owned_material_label_and_unchanged_edit_preserve_definition(self, service, mock_connector, kind):
        from optiland.materials import DataMaterial
        from optiland.visualization.info.material_formatter import MaterialFormatter
        material = (DataMaterial.from_samples([.4, .8], [1.6, 1.4], name='N-BK7')
                    if kind == 'samples' else DataMaterial.from_coefficients('formula 5', [1.5], name='N-BK7'))
        surface = mock_connector._optic.surfaces[1]
        surface.material_post = material
        definition = material.to_dict()
        assert service._get_material_data(surface) == MaterialFormatter.format(surface) == 'N-BK7'
        service.set_surface_data(1, mock_connector.COL_MATERIAL, 'N-BK7')
        assert surface.material_post is material
        assert surface.material_post.to_dict() == definition

    @pytest.mark.parametrize('index,extinction,label', [(1., 0., 'Air'), (1., .01, '1.0'), (1.5, 0., '1.5')])
    def test_material_display_uses_shared_label_without_array_formatting(self, service, mock_connector, index, extinction, label):
        from optiland.materials import IdealMaterial
        surface = mock_connector._optic.surfaces[1]
        material = IdealMaterial(index, extinction)
        surface.material_post = material
        assert service._get_material_data(surface) == label
        service.set_surface_data(1, mock_connector.COL_MATERIAL, label)
        assert surface.material_post is material

    def test_add_surface_increases_count(self, service, mock_connector):
        initial = service.get_surface_count()
        service.add_surface()
        assert service.get_surface_count() == initial + 1
        mock_connector.opticChanged.emit.assert_called()

    def test_changed_material_label_replaces_owned_definition(self, service, mock_connector):
        from optiland.materials import DataMaterial, IdealMaterial

        surface = mock_connector._optic.surfaces[1]
        surface.material_post = DataMaterial.from_samples([0.4, 0.8], [1.6, 1.4], name="Owned")
        service.set_surface_data(1, mock_connector.COL_MATERIAL, "1.7")
        assert isinstance(surface.material_post, IdealMaterial)
        assert float(surface.material_post.n(0.55)) == 1.7
        mock_connector.opticChanged.emit.assert_called()

    def test_remove_surface_decreases_count(self, service, mock_connector):
        initial = service.get_surface_count()
        # Remove surface at row 1 (first real lens surface)
        service.remove_surface(1)
        assert service.get_surface_count() == initial - 1
        mock_connector.opticChanged.emit.assert_called()

    def test_update_radius(self, service, mock_connector):
        # Surface 1 radius should update without raising
        service.set_surface_data(1, mock_connector.COL_RADIUS, "75.0")
        optic = mock_connector._optic
        radius = optic.surface_group.surfaces[1].geometry.radius
        assert abs(float(radius) - 75.0) < 1e-6
        mock_connector.opticChanged.emit.assert_called()

    def test_type_conversion_standard_to_biconic(self, service, mock_connector):
        surface = mock_connector._optic.surface_group.surfaces[1]
        assert surface.surface_type != "biconic"
        service.set_surface_type(1, "biconic")
        surface = mock_connector._optic.surface_group.surfaces[1]
        assert surface.surface_type == "biconic"

    def test_get_geometry_types_contains_standard(self, service):
        types = service.get_geometry_types()
        assert isinstance(types, list)
        assert len(types) > 0
        assert "standard" in types

    def test_unknown_surface_type_ignored(self, service, mock_connector):
        # Should not raise and should not change the surface
        original_type = mock_connector._optic.surface_group.surfaces[1].surface_type
        service.set_surface_type(1, "not_a_real_type")
        assert (
            mock_connector._optic.surface_group.surfaces[1].surface_type
            == original_type
        )
