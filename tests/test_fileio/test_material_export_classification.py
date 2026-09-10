"""Export must preserve index differences and fail before discarding absorption."""

from __future__ import annotations

import pytest

from optiland.fileio import save_codev_file, save_zemax_file
from optiland.fileio.common import is_air
from optiland.materials import IdealMaterial
from optiland.optic import Optic


@pytest.mark.parametrize('index,extinction,expected', [
    (1., 0., True), (1., .01, False), (1.5, .01, False),
    (1.0000001, 0., False), (.9999999, 0., False),
])
def test_air_classification_keeps_index_and_extinction(index, extinction, expected, set_test_backend):
    assert is_air(IdealMaterial(index, extinction)) is expected


@pytest.mark.parametrize('writer', [save_zemax_file, save_codev_file])
@pytest.mark.parametrize('index', [1., 1.5])
def test_absorbing_ideal_export_preserves_existing_file(writer, index, tmp_path, set_test_backend):
    optic = Optic()
    optic.add_surface(index=0, radius=float('inf'), thickness=float('inf'))
    optic.add_surface(index=1, radius=50, thickness=5, material=IdealMaterial(index, .01), is_stop=True)
    optic.add_surface(index=2, radius=-50, thickness=50)
    optic.add_surface(index=3, radius=float('inf'), thickness=0)
    optic.set_aperture('EPD', 5)
    optic.set_field_type('angle')
    optic.add_field(y=0)
    optic.add_wavelength(.55, is_primary=True)
    path = tmp_path / 'existing.txt'
    path.write_bytes(b'previous optical design')
    with pytest.raises(NotImplementedError, match='absorption'):
        writer(optic, path)
    assert path.read_bytes() == b'previous optical design'
