# Materials tutorial samples

These synthetic examples are supplied under the Optiland repository license.
They contain no supplier catalog extracts and do not represent manufactured
glasses. No commercial optical software is needed.

- `singlet.json` is a native Optiland lens with four surfaces, a 10 mm entrance
  pupil, fields of 0 and 3 degrees, and the F/d/C wavelengths. Its named crown
  owns the formula `n(lambda) = 1.5 + 0.005 / lambda**2`, valid from 0.4 to
  0.8 micrometers. It has no measured extinction or transmission data. Bounds
  policy is `raise`. `make_singlet.py` regenerates it with the public API.
- `typed_glass_expert.py` is a self-contained Python API example. It creates
  a separate synthetic singlet and two exact, owned candidates sharing the name
  `Synthetic crown`, round-trips its pool through JSON, and jointly optimizes
  front radius and material. It requires this revision's typed GlassExpert API,
  prints a bounded finite JSON report, and needs no other sample files or GUI.
- The desktop GlassExpert tutorial uses `singlet.json` with the two entries in
  `tutorial.glc`, front radius bounds 20–150 mm and the primary-wavelength `f2`
  objective at 60 mm. It creates its own run manifest, public-core replay script
  and complete native candidate through the application's Export controls.
  Saved setups contain exact embedded definitions, including their original
  source identities; loading one is detached inspection/replay, never authority
  to replace a live document.
- `rii/DEMO-CROWN.yml` is a local RII-format example with
  `n(lambda) = 1.5 + 0.02 / lambda**2`, explicit air-relative index,
  air wavelengths and a 20 degrees Celsius reference. Extinction is unknown.
- `tutorial.glc` is a supported text GLC example. Its `DEMO-CROWN` uses
  `n(lambda) = 1.5 + 0.01 / lambda`; `DEMO-LOSS` uses
  `n(lambda) = 1.6 + 0.02 / lambda`. Both have 0.4–0.8 micrometer limits.
  `DEMO-LOSS` has synthetic internal-transmittance samples 0, 0.7 and 0.85
  at 0.4, 0.6 and 0.8 micrometers, respectively, for a 5 mm thickness.
  The GLC decoder retains its local-quadratic transmission interpolation.
  Its exact zero sample demonstrates the difference between supplied zero
  transmission and missing extinction data. These are teaching data, not
  measurements. The GLC format does not declare
  index/wavelength reference conventions for these records.

The two `DEMO-CROWN` records deliberately share a display name while having
different formulae. Source, catalog and exact record identifier disambiguate
them. Rounded GLC summary values are separate declared values; use computed
coordinates to compare models.

Do not rename one example to make it look like supplier data. Save edited
tutorial lenses under a new filename to retain a clean starting point.
