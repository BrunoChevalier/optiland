OSLO import compatibility
=========================

Use ``load_oslo_file("design.len")`` to read a sequential OSLO prescription.
Unsupported optical commands emit warnings; inspect them before relying on the
result. To reject unsupported commands and known approximations, use::

    from optiland.fileio import load_oslo_file

    optic = load_oslo_file(
        "design.len", strict=True, glass_catalogs=["catalogs/vendor.glc"]
    )

Strict imports require an explicitly supplied definition for every named glass.
Air, mirrors, saved direct indices and ``material_overrides`` do not require GLC
files. The loader never discovers catalogs automatically.

Malformed data raises ``ValueError``. Parser errors and unsupported-command
warnings identify the file, line and surface. ``OsloDataParser(...).parse()``
returns an ``OsloDataModel`` containing the parsed prescription before conversion
to an ``Optic``. This model also exposes structured ``diagnostics``.
Strict import is a compatibility check, not a certificate of agreement with OSLO.
No CCL, include file, optimization program or external executable is executed.

Supported mappings
------------------

Command names are case insensitive. Quoted strings may contain semicolons and
``//``; those delimiters split statements and comments only outside strings.
UTF-8 (including BOM) and legacy Windows-1252 text are accepted.

.. list-table:: Sequential prescription support
   :header-rows: 1
   :widths: 30 70

   * - Commands
     - Mapping and limits
   * - ``LEN NEW``, ``NXT``, ``GTO``, ``END``
     - Object, optical and image surfaces; updating a previous surface preserves
       its other data. The first prescription is imported.
   * - ``UNI``, ``EBR``, ``FNO``, ``NAO``, ``NAP``, ``PUK``, ``TELE``
     - Lens units converted to millimeters. Working f-number/image NA/slope
       determine the entrance pupil via a paraxial trace. EBR specifies the axial
       beam radius at surface 1; finite-object imports account for the displaced
       entrance pupil. These specifications require valid paraxial geometry.
       TELE sets object-space telecentricity and is preserved in native JSON.
       Real telecentric launch supports finite object-height fields in air with
       entry along +z, using an equivalent object NA. Other launch combinations
       warn in permissive mode and fail in strict mode.
       Native paraxial chief-ray analysis still aims at the stop; use real rays
       to evaluate the imported telecentric launch.
       NAO requires a finite object and an aperture greater than zero and less
       than the object medium's refractive index; hemisphere/extended launches
       are outside this mapping.
   * - ``ANG``, ``OBH``, ``GIH``; ``RST NEW`` / ``F``
     - Maximum field or explicit fractional X/Y positions, weights and symmetric
       pupil vignetting. Fractional object positions are converted through tangent
       space for angular fields. The angular reference must be below 90 degrees;
       wide-angle ray aiming (WARM) is not mapped. GIH refers to the Gaussian
       focal plane.
       Without a table, generate on-axis, 0.7 and full-field points.
   * - ``WV``, ``WVn``, ``WW``, ``WWn``
     - Replacement and indexed wavelength/weight assignments. Default d/F/C
       wavelengths are 0.58756/0.48613/0.65627 micrometers; WV1 is primary.
       Indexed edits preserve untouched wavelengths; a bulk WV replaces the set.
       The primary wavelength must have positive weight; other weights may be zero.
       Direct-index glass retains the wavelengths active when it was defined.
   * - ``RD``, ``RDF``, ``CV``, ``CVF``, ``TH``, ``THF``, ``CC``
     - Spheres/conics, planar RD=0, signed thickness and infinity sentinels.
       Object distances with magnitude at least 1e8 lens units are infinite,
       independently of the conversion to millimeters.
       Image-surface TH is a focus shift added to the preceding nominal gap
       after solves and pickups. The physical detector position and final gap
       retain that shift; native image thickness is zero. Focus shifts require
       an interior surface; combining them with an image GC reference is not
       mapped.
       Fixed markers describe editing constraints and do not change the snapshot.
   * - ``AD`` through ``AG``; ``ASP ADO/ASR/ARA/ASX`` and ``ASn``
     - AD starts at r^4. ASR uses even radial powers, ARA all positive radial
       powers, ASX triangular-indexed XY monomials. Nonzero radial AS0 is rejected.
       Dimensional coefficients scale with their actual polynomial powers.
       Nonzero ASR AS1, ARA AS1/AS2 and ASX AS0..AS5 retain real-ray geometry
       but warn because the native paraxial engine omits their changes to vertex,
       normal or power. Strict mode rejects those cases; derived paraxial pupils,
       fields and solves must be treated as approximations.
   * - ``CVX``, ``RDX``
     - Toroidal surfaces with supported rotational profiles. Unsupported
       combinations are rejected rather than converted to a different formula.
   * - ``AIR``, ``AIF``, ``RFL``, ``RFH``, ``GLA``, ``GLF``
     - Air, reflection, named catalog glass, constant index and sampled index
       data. Distinct saved indices use ``DataMaterial``: exact at samples,
       linear between samples, with extrapolation rejected. Wavelengths that
       collapse together in the active backend precision cannot be interpolated;
       use higher precision for those samples. Two-parameter model
       glass and historical fallback glasses use approximate Abbe dispersion.
       Strict import requires an exact name in the supplied GLC catalogs. Explicit
       ``material_overrides`` bind names to verified native material definitions;
       see catalog handling below.
   * - ``AP``, ``APF``, ``AP CHK/UNC``, ``APCK``, ``AST``
     - Ordinary AP retains drawing bounds; CHK enables clipping. APCK OFF
       disables clipping. Default import uses checked/special apertures for
       clipping, matching OSLO spot-diagram behavior. The default stop is surface 1.
   * - ``APN``, ``ATP``, ``AAC``, ``AGN``, ``AAN``, ``AX1/2``, ``AY1/2``,
       ``AVX1..4``, ``AVY1..4``, ``APK``
     - Ellipses, rectangles, triangles, quadrangles, centroid-based rotation,
       obstructions and unions of intersecting aperture groups. Omitted legacy
       coordinates are zero. Transmitting/obstructing actions are supported.
       Undeviated holes (AAC 1) are supported on plain air-to-air planes without
       phase or polarization elements: their outline is retained without clipping.
       Other holes are diagnosed. APK copies a preceding special aperture.
       Polygon boundaries must be nondegenerate; quadrangles must have strictly
       convex vertices in clockwise or counterclockwise order.
   * - ``DCX/Y/Z``, ``TLA/B/C``, ``DT``, ``TOX/Y/Z``, ``GC``, ``RCO``, ``BEN``
     - OSLO intrinsic Euler rotations, signed X/Y tilts, translation order,
       pivots, preceding global references and coordinate returns. BEN supports
       single-axis local RFL/RFH mirror bends; mixed-axis/global bends and bends
       relying on unmapped TIR-controlled reflection are rejected.
   * - ``PK CV/CVM/TH/THM/LN/LNM/AP/GLA/TD/TDM``
     - Static preceding-surface pickups, relative references and chains.
       Curvature and length constants are additive. Forward/self references are
       rejected. The result is an imported prescription, not live OSLO constraints.
       Solved values feed downstream pickups. TD/TDM retain local pivot data;
       pickups involving global references, coordinate returns or bends are rejected.
   * - ``PY``, ``PYC``, ``PU``, ``PUC``, ``EC``
     - Targeted marginal/chief height, outgoing slope and edge-contact solves.
       Targets are rechecked after rebuilding dependent pickups, pupils and fields;
       later solves must also preserve earlier accepted targets. Unsupported or
       unsatisfied solves restore saved values with a warning in permissive mode;
       strict mode rejects. General simultaneous constraint solving is not provided:
       a coupled case can be rejected even if a joint solution exists in OSLO.
       EC verifies physical contact at the first surface's local meridional edge
       after positioning both surfaces. Relative transforms that prevent this
       contact restore the saved prescription rather than accepting TH alone.
       Telecentric PYC/PUC solves are not mapped because the native paraxial chief
       ray used by those solves does not implement the telecentric launch.
   * - ``GSP``, ``GOR``
     - Ruled gratings through the existing phase model, with grooves parallel
       to local X. Lens-unit spacing is converted to millimeters. Blaze efficiency
       is not inferred.
   * - ``TCE``
     - Finite expansion coefficients retained in the intermediate model.
       Nominal reference-temperature geometry is unchanged; temperature-dependent
       dimensions and thermal studies are not implemented.
   * - ``JAA/B/C/D``, ``JPA/B/C/D``
     - Local Cartesian 2x2 Jones matrices, identity defaults, nonnegative
       amplitudes and phases in degrees. The local Z field is unchanged.
       Surface coordinate rotations transport the electric field as well as the
       ray. Object-surface elements are rejected. Choose an explicit input
       polarization state before tracing; the element does not define the source.
       Geometrical refraction/reflection precedes the Cartesian element; it does
       not add Fresnel coefficients. Native save/reload preserves the element
       and source state, while OSLO export rejects the unsupported coating
       before writing the destination.
   * - ``CFG NEW`` with ``TH``, ``WVn``, ``WWn``; ``CFWT``, ``CFAC``
     - Independent one-based configuration snapshots. Thickness and indexed
       spectrum overrides apply to a copy of configuration 1 before pickups and
       coordinates are evaluated. Weights and active flags remain in the raw model;
       inactive configurations can still be selected explicitly. Alternate
       configurations with solves, and thickness overrides on pickup-controlled
       targets, are rejected pending verified constraint precedence.
   * - ``ATD``, ``CXD``, ``APD``, ``GCD``, ``RCD``, ``BED``, ``PFD``,
       ``TDD``, ``CSD``, ``TSD``
     - Clear the corresponding previously entered surface data/constraints.
   * - ``DES``, ``SNO<n>``, ``NOT``; drawing/group records
     - Names and notes retained in the intermediate model. Drawing records
       DRW/LDP/CBK/ELMDF1/2/BDI/BDD/VX/PF and sequential LMO ELE/EGR, LMN,
       LME do not affect ray tracing. Non-sequential LMO groups are diagnosed.

Catalog handling and configuration selection
--------------------------------------------

OSLO's catalog, direct-index and model glass definitions are different formats.
``GLA name`` refers to an external catalog entry. OSLO requires unique glass names
across the installed catalogs, so an unqualified name is normal in a lens file.
It does not embed the catalog's dispersion, thermal or transmission data. Catalogs
include manufacturer, Misc, Obsolete, Shared and Private entries; the documented
Obsolete fallback emits a warning. Names of replacement glasses are distinct
identities, and a literature reference is not a manufacturer catalog.

The ``.glc`` files contain text glass records: a header includes the record count
and each subsequent line describes a glass. ``.cdb`` is a separate database used
for catalog browsing. See the Program Reference, pp. 52-54 and 184-190, and Lambda's
`glass catalog editing guidance <https://lambdares.com/support-posts/editing-glass-catalogs>`_.
Supply the text catalogs explicitly, independently of where the lens is stored::

    optic = load_oslo_file(
        "design.len",
        strict=True,
        glass_catalogs=["catalogs/vendor.glc", "catalogs/custom.glc"],
        configuration=2,
    )

Resolution order is saved direct indices, ``material_overrides``, exact
case-insensitive GLC names, then permissive database/fallback lookup. Supplied
GLC data wins even when the built-in database has the same name. No global aliases
are registered. Equal duplicate definitions coalesce their provenance; differing
definitions fail with both locations and require an explicit override for that
name. Every selected file is validated, including unused or overridden records.
Malformed or unsupported selected data always fails, regardless of strictness.

With ``strict=True``, missing catalog names are reported together and built-in
substitution is prohibited. Omitting ``glass_catalogs``, passing ``None`` or
passing ``[]`` all mean no supplied catalogs. With ``strict=False`` (the default),
each missing name warns before existing database lookup and fallback behavior;
fuzzy matches, manufacturer-prefix substitutions and historical Abbe approximations
retain their warnings. Successful fallback does not establish agreement with the
original catalog edition.

Explicit per-import definitions can also bind a verified native material::

    from optiland.materials import Material, MatchPolicy

    verified_glass = Material("N-F2", catalog="schott", match_policy=MatchPolicy.STRICT)
    optic = load_oslo_file(
        "design.len",
        strict=True,
        configuration=2,
        material_overrides={"GLASS_A": verified_glass},
    )

The example assumes the caller has verified that GLASS_A denotes that exact entry.
Bindings are case insensitive and copied per import. Saved direct-index glass
data takes precedence over a name binding. Bindings must be supplied by the caller.
Configuration selection defaults to 1 and never modifies another import.

GLC optical data and native persistence
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The reader accepts headers 5.00, 5.11, 5.32, 6.31, 6.4, 6.63, 6.65, 7.02, 24.1
and 26.1, with variable-length groups checked against the record counts. It reads
GLC text only; CDB browsing databases are not optical definitions. Selected files
are limited to 16 MiB, 50,000 records and 64 KiB per record.

Supported dispersion laws are Laurent (1), Sellmeier (2), Conrady (3), Hikari (6)
and Herzberger (7). They become :class:`~optiland.materials.data.DataMaterial`
coefficient models, rather than reconstructed Abbe values or sampled dispersion.
Optional wavelength limits are enforced; reference-data identifiers and descriptive
summary fields remain metadata. Unknown versions, optical models or trailing groups
raise source-located errors.

Thermal models are Schott absolute-index (1), constant relative dn/dT (2), and
Corning relative dn/dT (3), referenced to 20 degrees Celsius. Single-value and
Corning coefficients are converted from 1e-6/K to native index units/K. An explicit
no-model flag (0), including its legacy unused coefficient, does not imply a
thermal law. Missing thermal data returns the catalog index without correction.
Material evaluation accepts temperature and pressure; importing a catalog does
not implement an OSLO thermal analysis or expand surface geometry.

Internal-transmission tables retain their original 5 mm samples. Evaluation uses
a local quadratic through the three nearest wavelengths (ties prefer the shorter
wavelength), then applies Beer's law for thickness. The extinction coefficient is
``k = -log(T) * wavelength_um / (4*pi*thickness_mm*1000)``. No table means zero
extinction. Tables require at least three distinct positive wavelengths and
transmittance within [0, 1]. Outside-table evaluation and unphysical quadratic
overshoot raise instead of extrapolating or clipping. A zero-transmission sample
is retained, but evaluating its extinction raises because it has no finite k.
These conservative bounds and the chosen quadratic stencil have independent
analytic tests; numerical parity with a running OSLO installation has not been
established for its boundary/interpolation behavior.

Native JSON embeds coefficients, thermal and transmission data, and catalog
label/version/content hashes. It needs no original catalog path, temporary YAML,
or material registry when reopened, including in a fresh process. The original
catalog edition remains a provenance consideration. OSLO export of these embedded
coefficient materials is rejected; save as native JSON to retain their full data.

GUI import and catalog selection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Choose **File > Import > From OSLO (.len)...**, select the lens, then review the
import options. **Configuration** lists one-based indices, active/inactive state
and weight, and initially selects configuration 1. Inactive configurations remain
selectable. Import creates one independent lens snapshot. **Reload lens** refreshes
the configuration list after a source-file correction, keeping the previous choice
when it still exists. Malformed or unreadable configuration metadata disables
import until corrected. Previewing metadata does not convert the lens or resolve
materials; the final import enforces the selected strictness.

**Strict mode** starts unchecked. **Choose glass catalogs...**
opens a directory chooser initially at the lens directory; choose a directory
explicitly to search it and its descendants for GLC files.

Every discovered catalog starts checked. The list shows filenames and relative
directories, supports individual toggles, **Check all** and **Uncheck all**, and
displays selected/total counts. Choices survive new result batches; changing the
directory starts a fresh selection. Only checked paths are passed to the loader.

The search runs in a managed helper process with a 10-second GUI deadline,
100,000-entry limit, 1,000-catalog limit and maximum depth 64. It skips symlinks,
junctions and reparse directories, suppresses duplicate files and batches results.
Inaccessible entries or a reached limit produce **Search incomplete** with a reason.
Review and explicitly accept the available subset or select another directory.
An empty search can be accepted with no catalogs. Cancellation terminates the
worker and ignores late results; the application reaps its child processes.

Cancel, Escape or window-close in catalog selection, or cancellation of its
directory chooser, discards all selected catalogs and returns to the import options
with ``[]``. Strict mode and configuration are preserved. Cancelling the primary lens chooser or the
overall options cancels the import. After an error the options remain open for
revision; the previous optic, native filename, modified state and undo history are
preserved, including when a selected configuration requires an unsupported solve
or references a configuration removed from the source file. Successful import
installs the candidate, clears the native destination
and undo history, and marks the document modified. Warnings appear as notifications.

Polarization and transmission settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Jones elements require a deliberate source state, including when the desired
input is unpolarized::

    from optiland.rays import PolarizationState

    optic.updater.set_polarization(PolarizationState())

The default ``ignore`` mode rejects tracing a system with polarization coatings.
Native JSON retains the local Jones matrices and an explicitly configured source
state. Native propagation accounts for a supplied material's extinction coefficient;
OSLO's polarization internal-transmission setting (PZIT, Program Reference p. 54)
and the original analysis settings are not inferred from Jones commands.

Clipped or fully attenuated rays retain exactly zero intensity and exit fields
even if they later miss a surface and acquire nonfinite geometry or polarization
data. Their missed-ray coordinates remain invalid. Nonfinite results on rays
with nonzero incident power remain visible to the audit. Evaluating a different
source polarization can still transmit a ray that was dark only for the previous
state; scalar clipping and absorption losses are retained.

Limits and deferred commands
----------------------------

OSLO is a complete optical-design environment. This importer handles a sequential
snapshot. It does not implement general multi-configuration execution, non-sequential
NSS/NAC/ELI traversal, GRIN/GRADIUM propagation, user DLL/CCL surfaces, eikonal
models, spline/ISO/Zernike surfaces, holograms/general diffractive phase polynomials,
polarization/coating libraries beyond explicit Jones elements, thermal expansion,
lens arrays, optimization and
general ray-aiming/field-depth controls. Unknown prescription commands produce
diagnostics rather than disappearing silently. Analysis/optimization footer blocks
are ignored except for supported declarative field/configuration tables and their
diagnostics. Unknown configuration overrides remain visible.

``PFL`` describes an OSLO perfect-imaging surface whose exact off-axis behavior
differs from a paraxial thin lens. Permissive import retains the existing thin-lens
approximation and warns; strict mode rejects it. ``PFM`` does not restore exact
perfect imagery. Do not interpret successful imports of GRIN, non-sequential or
user-surface examples as faithful optical models.
Export of native thin-lens interactions is rejected because PFL would change
their off-axis physics into OSLO's perfect-imaging model.

Import coverage is broader than OSLO export coverage. Native Optiland JSON is the
preferred way to retain imported general geometry, aperture composition and poses.
The OSLO writer supports its documented surface subset, direct spectral samples,
correct even-asphere powers, explicit angular/object-height fields and radial
aperture checking flags, and object-space telecentricity. Unsupported field
definitions, transformed surfaces, phase profiles, offset/non-radial apertures,
custom interaction, material or propagation models, ideal-material absorption,
coatings and scattering raise before the destination file is opened. Names and
notes must fit on a single line. Use native JSON for those systems. Finite-object
and floating-stop apertures export the actual axial beam radius at surface 1.
The standard and even-asphere handlers also validate the concrete geometry;
a surface label cannot authorize dropping or changing its actual sag terms.
The writer derives spacings from the actual axial coordinates, including lenses
built with absolute positions or edited coordinates. A common axial translation
is removed by anchoring the first surface at zero. It rejects finite object or
interior distances that import would interpret as infinite and preserves thickness
precision to avoid rounding across those boundaries.
It writes the physical final gap with zero image defocus, so exporting an imported
focus shift does not apply it twice. Unused native image thickness is not defocus.
Constant refractive indices retain their saved precision, including small
differences from unity. Invalid object-NA launches are rejected before writing.
Field references, object NA, wavelengths and sampled indices preserve floating-point
precision, avoiding rounded hemisphere boundaries or collapsed spectral samples.
Explicit surface aperture radii must be finite and positive: zero cannot retain
a native zero-radius clipping boundary, and infinity is not a finite OSLO radius.

Specification and real-file validation
--------------------------------------

Mappings were checked against Lambda Research's
`OSLO Program Reference (10 March 2021) <https://lambdares.com/hubfs/Support/support/oslo/oslo_releases/OSLOProgramReference.pdf>`_
(printed pp. 18-25: configurations; 43-50: solves/apertures; 51-68: media/coordinates;
69-85: surfaces/gratings; 106-107: Jones elements; 120-123: system setup/wavelengths;
184-190: glass catalogs; 208-210: fields; 215: telecentricity;
508-514: commands),
the `Optics Reference <https://lambdares.com/hubfs/Support/support/OSLOOpticsReference_Sep21.pdf>`_
(pp. 105: special-aperture shapes and rotation; 142-145: coordinate transforms;
151: object conjugates/telecentric launch;
170-171: grating equation), and the
`official demo library <https://lambdares.com/support-posts/lens-demos>`_.
The `current release page <https://lambdares.com/support-posts/oslo-current-release>`_
links the reference editions used during research.

Download the official ``OSLOLensDemos.zip`` separately and run from the repository::

    python scripts/audit_oslo_examples.py OSLOLensDemos.zip oslo-audit.json
    python scripts/audit_oslo_examples.py OSLOLensDemos.zip oslo-audit-torch.json --backend torch

The audit records archive/file SHA-256 hashes, source URL, filenames, backend,
permissive warnings/errors, strict errors and an on-axis trace smoke check.
It performs no network access and does not vendor the proprietary example archive.
Smoke checks do not establish agreement with OSLO ray intercepts. Original minimal
test prescriptions provide independent numerical assertions for units, sag,
indices, poses, aperture clipping, grating directions and solve targets.

The 2026-09-09 audit used archive SHA-256
``d5d43924d945a0ef5a200a0e5f12e459095b7504c59c946770fe75711814f8cc``.
With the current explicit-catalog policy and no supplied catalogs, both NumPy and
Torch import 97 of 101 files permissively. NumPy has 6 without warnings and 91
with warnings; Torch has 5 and 92 respectively. Strict import passes 6 on NumPy
and 5 on Torch. Each backend has one fewer strict success than before explicit
GLC resolution: ``demos/edu/anamorph.len`` now requires a supplied definition for
its named glass. This change reflects catalog verification rather than a changed
ray trace. The remaining backend
difference is the PY solve in ``demos/premium/gedemo.len``: Torch's residual
of approximately -2.38e-7 misses the required zero-height tolerance.
The on-axis smoke trace
transmitted at least one finite ray in 78 files, transmitted none in 19, and
raised no errors among imported files. These figures include deliberately
unsupported examples and are compatibility results,
not 101 validated optical designs. The audit JSON lists every filename and reason.
The rejected ``demos/edu/prismirr.len`` has a BEN bend that relies on
TIR-controlled reflection, which is not mapped. Earlier permissive imports
accepted this file while tracing the affected surface with a refracting model.
Two other rejected files, ``demos/edu/ebert.len`` and
``demos/premium/nonseq/cherryns.len``, require finite-object EBR conversion on
geometry outside the native scalar paraxial model. Earlier imports treated EBR
as an entrance-pupil radius without accounting for their finite object distance;
the smoke trace failed for ebert and transmitted no rays for cherryns.
The fourth rejection is ``demos/edu/xarmdemo.len``: its NAO=1 and XARM mode
require extended ray aiming. The earlier import's finite smoke rays did not
represent that specified source cone.
Legacy ``RCO 0`` records in the demo archive are interpreted as the default undo
of the current local transform, consistently with the examples' surface placement.

Reproducible validation without OSLO
----------------------------------------

Development and CI require no OSLO installation or license key. The committed
``tests/test_fileio/fixtures/oslo_glc/`` corpus contains ten valid catalogs covering
the accepted header versions and nine deliberately invalid examples. All names,
coefficients, summaries and tables in these files are invented for these tests;
no installer or manufacturer optical records are copied or altered. Additional
temporary fixtures cover malformed input, encodings, truncation, resource bounds,
duplicate definitions, interpolation ties and backend precision. The fixture
README describes the coverage matrix and its limits.

Independent equations verify dispersion, thermal units, transmission and optical
attenuation on NumPy and Torch. A synthetic folded lens combines catalog import,
configuration changes, in-glass reflection, holes, Jones matrices and clipping;
its native snapshot is also traced in a fresh process after removing the temporary
catalog. These checks validate the documented mapping, not undocumented historical
formats or numerical agreement with running OSLO. Historical catalog acquisition,
correction of proprietary source data and live OSLO comparisons are optional
external validation, not prerequisites for building or testing this importer.

For a local prescription, audit every declared configuration, including inactive
ones, with explicit catalogs::

    python scripts/audit_oslo_configurations.py design.len audit.json \
        --catalog vendor.glc --catalog misc.glc \
        --backend numpy --backend torch --polarization unpolarized

Each backend uses CPU float64. The audit tests strict import, all normalized field
coordinates and declared wavelengths with five ``line_y`` pupil rays, same-process
native reload and fresh-process reload with external material reads disabled.
It compares positions in millimeters, direction cosines and intensities with
``rtol=1e-10`` and ``atol=1e-12``. This is a native-persistence comparison, not an
OSLO-reference tolerance. Finite transmitted rays, zero intensity, nonfinite
surviving coordinates and nonfinite intensity have separate counts. A ray with
zero intensity is not necessarily aperture-clipped; absorption can also extinguish
it. Matching native snapshots can reproduce an invalid trace, so nonfinite
surviving coordinates or intensity fail the audit even when reload matches.

``--polarization`` is an explicit audit assumption. Its default, ``ignore``, keeps
the source unchanged and exposes a trace failure when Jones elements need a source
state. Available explicit states are ``unpolarized``, ``H``, ``V``, ``L+45``,
``L-45``, ``RCP`` and ``LCP``. The option does not recover an original OSLO session.
``--no-fresh-process`` skips that check and records it as untested.

The report records source/catalog hashes, code revision and working-tree status,
backend/configuration identifiers, activity, counts and separate import/trace/
reload outcomes. It omits filenames, glass names, prescription values, raw ray
arrays and error text. Failures retain their phase and exception type; inspect
the ordinary loader locally when a detailed source diagnostic is needed. The
report includes an overall ``passed`` result, and the command returns a nonzero
exit status on failed required checks. Keep private
inputs and audit outputs outside version control.
