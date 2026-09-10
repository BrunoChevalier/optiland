# Original synthetic GLC fixtures

Every catalog here was authored for Optiland tests using invented `FAKE_*` glass
names, catalog labels, coefficients, summaries and transmission samples. These
are not manufacturer/OSLO installer catalogs, renamed real entries, or perturbed
copies of proprietary optical data. No original catalog is needed to use them.
The numeric laws are chosen for simple independent analytic checks; summary nd/Vd
values deliberately need not equal the coefficient law. Do not use these fictional
materials to design an optical instrument.

## Valid files

`valid/version-*.glc` contains one complete file for each supported header:
5.00, 5.11, 5.32, 6.31, 6.4, 6.63, 6.65, 7.02, 24.1 and 26.1. Each contains the
same seven original records to test the accepted reader contract across versions.
This is not evidence that every model existed in every historical OSLO release.
The files are intentionally small, readable UTF-8 text.

| Record | Dispersion | Thermal | Transmission and optional groups |
| --- | --- | --- | --- |
| `FAKE_LA` | Laurent, model 1 | Schott, model 1 | Unsorted four-sample 5 mm table; wavelength limits; negative reference ID |
| `FAKE_SE` | Sellmeier, model 2 | Constant negative relative dn/dT, model 2 | Absent transmission; explicit absent range/reference groups |
| `FAKE_CO` | Conrady, model 3 | Corning, model 3 | Declared empty transmission table; reference ID without wavelength limits |
| `FAKE_HI` | Hikari, model 6 | Model 0 with an unused nonzero coefficient | No transmission; range-only optional group |
| `FAKE_HE` | Herzberger, model 7 | Absent thermal law | No transmission or trailing groups |
| `FAKE_ZERO` | Constant Conrady | Model 0 with an unused zero coefficient | Transmission endpoints 0 and 1; zero is retained but has no finite extinction |
| `FAKE000001` | Constant Conrady | Absent | Ten-character name; negative Abbe/TCE summaries, zero density/transmission summary, unrelated positive nd |

`test_oslo_glc_fixtures.py` checks every file, all seven material laws and native
material restoration on NumPy and Torch. Coefficient expectations are independent
equations in the test, not calls to the production formula evaluator.

## Invalid files and generated edge cases

`invalid/` contains nine independently authored examples: unknown version,
truncated mandatory group, duplicate transmission wavelengths, transmission above
one, negative density, nonpositive nd, unsupported range group, mismatched record
count and extra trailing data. Each must produce a source-located error.

The tests also write temporary fake GLC files for the following cases; generating
these variations avoids a large collection of almost identical committed files.

| Coverage | Tests |
| --- | --- |
| UTF-8, BOM, Windows-1252, LF/CRLF/CR, tabs, blank lines, uppercase extension, D/d/E numeric exponents | `test_oslo_glc_edges.py` |
| Empty catalogs, missing labels, unknown versions and noninteger header counts | `test_oslo_glc_edges.py`, `test_oslo_glc.py` |
| Every mandatory-token truncation; nonfinite values in all nine numeric summaries | `test_oslo_glc_edges.py` |
| Unknown models, invalid/missing group counts, incomplete tails, noninteger reference IDs, overlong names | `test_oslo_glc_edges.py`, `test_oslo_glc.py` |
| Byte/record/record-count limits at and beyond the boundary; 50/51 transmission samples | `test_oslo_glc_edges.py` |
| Identical/conflicting duplicates, case-insensitive names, repeated paths, distinct directories, override rules and malformed unused records | `test_oslo_glc_edges.py`, `test_oslo_glc.py`, `test_oslo_glc_import.py` |
| Missing materials, strict/permissive catalogs, explicit bindings and saved direct indices | `test_oslo_glc_import.py` |
| Quadratic stencil ties, overshoot/undershoot, zero extinction, thickness scaling and precision-collapsed samples | `test_oslo_glc_edges.py`, `test_oslo_glc_fixtures.py`, `test_embedded_material.py` |
| Thermal units, reference temperature, unused coefficients, wavelength ranges and native data ownership | `test_oslo_glc.py`, `test_oslo_glc_fixtures.py`, `test_embedded_material.py` |
| Folded configurations, reflection inside glass, hole passage, clipping, Jones attenuation and fresh-process reload after removal of the temporary catalog | `test_oslo_integration.py` |

The existing database-collision tests intentionally use a known built-in name
with invented coefficients to test precedence. They do not contain the actual
catalog record for that name. All records in this on-disk corpus use fake names.

## Validation limit

This corpus covers the currently supported versions/models and known input edge
classes. It cannot prove coverage of every undocumented OSLO format or reproduce
a historical catalog edition, and it does not establish parity with running OSLO.
Future format support needs an explicit fixture, independently checked expected
values and updated documentation. Proprietary data stays outside the repository.
