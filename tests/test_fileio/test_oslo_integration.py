"""Original folded prescription combining the supported import features."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

import optiland.backend as be
from optiland.fileio import load_oslo_file
from optiland.fileio.oslo.reader.parser import OsloDataParser
from optiland.materials import DataMaterial
from optiland.optic import Optic
from optiland.rays import PolarizationState, PolarizedRays
from tests.test_fileio.test_oslo_glc_fixtures import FIXTURES, fictional_index
from tests.utils import assert_allclose


def assert_folded_trace(optic, gap, wavelength, transmission):
    """Check analytic path loss and clipping, also usable by a fresh process."""
    rays = PolarizedRays(
        be.array([0, 0.6]),
        be.zeros(2),
        be.full(2, -1),
        be.zeros(2),
        be.zeros(2),
        be.ones(2),
        be.ones(2),
        be.full(2, wavelength),
    )
    for surface in optic.surfaces[1:]:
        surface.trace(rays)
    rays.update_intensity(PolarizationState())
    # Two millimeters before the mirror, then the configuration's folded gap
    # within the same glass. Jones amplitudes .5 multiply intensity by .25.
    expected = 0.25 * transmission ** ((2 + abs(gap)) / 5)
    assert_allclose(rays.i, [expected, 0])
    assert be.all(be.isfinite(rays.x) & be.isfinite(rays.y) & be.isfinite(rays.z))
    # OSLO TLA rotates about -x: the 45-degree mirror sends +z into -y.
    assert_allclose(
        [rays.x[0].item(), rays.y[0].item(), rays.z[0].item()], [0, gap - 3, 4]
    )
    assert_allclose([rays.L[0].item(), rays.M[0].item(), rays.N[0].item()], [0, -1, 0])
    # The hole admits both rays, but the later checked stop clips the second.
    assert_allclose(optic.surfaces[1].intensity, [1, 1])
    assert optic.surfaces[6].intensity[1].item() == 0


@pytest.mark.parametrize(
    "configuration,gap,wavelength,transmission",
    [(1, -5, 0.5, 0.75), (2, -8, 0.7, 0.87)],
)
def test_folded_configuration_with_catalog_hole_jones_and_clip(
    tmp_path, set_test_backend, configuration, gap, wavelength, transmission
):
    catalog_path = tmp_path / "fictional.glc"
    shutil.copyfile(FIXTURES / "valid" / "version-7-02.glc", catalog_path)
    path = tmp_path / "combined.len"
    path.write_text(
        'LEN NEW "combined" 1 7; NAO .02; OBH .1; WV .5; TH 30; '
        "NXT; AIR; TH 2; APN 1; ATP A 2; AAC A 1; "
        "AX1 A -.1; AX2 A .1; AY1 A -.1; AY2 A .1; "
        "NXT; GLA FAKE_LA; TH 2; TCE 125; "
        "NXT; RFL; TLA 45; BEN; TH -5; "
        "NXT; AIR; TH -2; "
        "NXT; AIR; TH 0; JAA .5; JAD .5; JPA 90; "
        "NXT; AIR; AP CHK .4; TH -1; "
        "NXT; AIR; END 7; CFG NEW; TH 3 2 -8; WV1 2 .7; END; CFAC 2 NO; CFWT 2 .4"
    )
    data = OsloDataParser(path, strict=True).parse()
    assert data.diagnostics == []
    assert data.surfaces[2]["TCE"] == 125
    assert data.configurations[2].active is False
    assert data.configurations[2].weight == 0.4
    optic = load_oslo_file(
        path,
        strict=True,
        configuration=configuration,
        glass_catalogs=[catalog_path],
    )
    assert optic.surfaces[3].thickness == gap
    assert optic.primary_wavelength == wavelength
    material = optic.surfaces[3].material_post
    assert isinstance(material, DataMaterial)
    assert_allclose(material.n(wavelength), fictional_index("FAKE_LA", wavelength))
    # Set a deliberate input state; Jones records do not define the source.
    optic.updater.set_polarization(PolarizationState())
    saved = tmp_path / "combined.json"
    saved.write_text(json.dumps(optic.to_dict()), encoding="utf-8")
    catalog_path.unlink()  # Only the temporary copy of our original fake catalog.
    restored = Optic.from_dict(json.loads(saved.read_text(encoding="utf-8")))
    for candidate in (optic, restored):
        assert_folded_trace(candidate, gap, wavelength, transmission)
    script = """
import json, sys
import optiland.backend as be
import optiland.fileio.oslo.catalog as glc
from optiland.materials.material_file import MaterialFile
from optiland.optic import Optic
from tests.test_fileio.test_oslo_integration import assert_folded_trace
def no_external_data(*args, **kwargs):
    raise AssertionError('Unexpected external material dependency')
glc.read_glc_catalog = no_external_data
MaterialFile._read_file = no_external_data
be.set_backend(sys.argv[2])
be.set_precision('float64')
with open(sys.argv[1], encoding='utf-8') as stream:
    optic = Optic.from_dict(json.load(stream))
assert_folded_trace(optic, *map(float, sys.argv[3:]))
print('verified')
"""
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(saved),
            be.get_backend(),
            str(gap),
            str(wavelength),
            str(transmission),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert run.stdout.strip() == "verified"
