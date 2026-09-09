OSLO import compatibility
=========================

Use ``load_oslo_file("design.len")`` to read a sequential OSLO prescription.
Unsupported optical commands emit warnings; inspect them before relying on the
result. To reject unsupported commands and known approximations, use::

    from optiland.fileio import load_oslo_file

    optic = load_oslo_file("design.len", strict=True)

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
       data. Distinct saved indices use ``TabulatedMaterial``: exact at samples,
       linear between samples, with extrapolation rejected. Two-parameter model
       glass and historical fallback glasses use approximate Abbe dispersion.
   * - ``AP``, ``APF``, ``AP CHK/UNC``, ``APCK``, ``AST``
     - Ordinary AP retains drawing bounds; CHK enables clipping. APCK OFF
       disables clipping. Default import uses checked/special apertures for
       clipping, matching OSLO spot-diagram behavior. The default stop is surface 1.
   * - ``APN``, ``ATP``, ``AAC``, ``AGN``, ``AAN``, ``AX1/2``, ``AY1/2``,
       ``AVX1..4``, ``AVY1..4``, ``APK``
     - Ellipses, rectangles, triangles, quadrangles, centroid-based rotation,
       obstructions and unions of intersecting aperture groups. Omitted legacy
       coordinates are zero. Transmitting/obstructing actions are supported; undeviated holes are
       diagnosed. APK copies a preceding special aperture.
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
       Telecentric PYC/PUC solves are not mapped because the native paraxial chief
       ray used by those solves does not implement the telecentric launch.
   * - ``GSP``, ``GOR``
     - Ruled gratings through the existing phase model, with grooves parallel
       to local X. Lens-unit spacing is converted to millimeters. Blaze efficiency
       is not inferred.
   * - ``ATD``, ``CXD``, ``APD``, ``GCD``, ``RCD``, ``BED``, ``PFD``,
       ``TDD``, ``CSD``, ``TSD``
     - Clear the corresponding previously entered surface data/constraints.
   * - ``DES``, ``SNO<n>``, ``NOT``; drawing/group records
     - Names and notes retained in the intermediate model. Drawing records
       DRW/LDP/CBK/ELMDF1/2/BDI/BDD/VX/PF and sequential LMO ELE/EGR, LMN,
       LME do not affect ray tracing. Non-sequential LMO groups are diagnosed.

Limits and deferred commands
----------------------------

OSLO is a complete optical-design environment. This importer handles a sequential
snapshot. It does not implement multi-configuration execution (CFG), non-sequential
NSS/NAC/ELI traversal, GRIN/GRADIUM propagation, user DLL/CCL surfaces, eikonal
models, spline/ISO/Zernike surfaces, holograms/general diffractive phase polynomials,
polarization/coating libraries, thermal expansion, lens arrays, optimization and
general ray-aiming/field-depth controls. Unknown prescription commands produce
diagnostics rather than disappearing silently. Analysis/optimization footer blocks
are ignored except for the declarative field table and configuration diagnostics.

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
The writer rejects finite object distances that OSLO would interpret as infinite
and preserves thickness precision to avoid rounding across that boundary.
It writes the physical final gap with zero image defocus, so exporting an imported
focus shift does not apply it twice. Unused native image thickness is not defocus.
Constant refractive indices retain their saved precision, including small
differences from unity. Invalid object-NA launches are rejected before writing.
Explicit surface aperture radii must be finite and positive: zero cannot retain
a native zero-radius clipping boundary, and infinity is not a finite OSLO radius.

Specification and real-file validation
--------------------------------------

Mappings were checked against Lambda Research's
`OSLO Program Reference (10 March 2021) <https://lambdares.com/hubfs/Support/support/oslo/oslo_releases/OSLOProgramReference.pdf>`_
(printed pp. 43-50: solves/apertures; 51-68: media/coordinates; 69-85:
surfaces/gratings; 120-123: system setup/wavelengths; 208-210: fields; 215: telecentricity;
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

The 2026-09-08 audit used archive SHA-256
``d5d43924d945a0ef5a200a0e5f12e459095b7504c59c946770fe75711814f8cc``.
Both NumPy and Torch imported 97 of 101 files in permissive mode (5 without
warnings, 92 with warnings); 10 passed strict import. The on-axis smoke trace
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
