OSLO import compatibility
=========================

Use ``load_oslo_file("design.len")`` to read a sequential OSLO prescription.
Unsupported optical commands emit warnings; inspect them before relying on the
result. To reject unsupported commands and known approximations, use::

    from optiland.fileio import load_oslo_file

    optic = load_oslo_file("design.len", strict=True)

Malformed data raises ``ValueError``. Parser errors and unsupported-command
warnings identify the file, line and surface. The intermediate
``OsloDataParser(...).parse()`` model also exposes structured ``diagnostics``.
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
       determine the entrance pupil via a paraxial trace; TELE sets object-space
       telecentricity. These specifications require valid paraxial geometry.
   * - ``ANG``, ``OBH``, ``GIH``; ``RST NEW`` / ``F``
     - Maximum field or explicit fractional X/Y positions, weights and symmetric
       pupil vignetting. Fractional object positions are converted through tangent
       space for angular fields. GIH refers to the Gaussian focal plane.
       Without a table, generate on-axis, 0.7 and full-field points.
   * - ``WV``, ``WVn``, ``WW``, ``WWn``
     - Replacement and indexed wavelength/weight assignments. Default d/F/C
       wavelengths are 0.58756/0.48613/0.65627 micrometers; WV1 is primary.
       Direct-index glass retains the wavelengths active when it was defined.
   * - ``RD``, ``RDF``, ``CV``, ``CVF``, ``TH``, ``THF``, ``CC``
     - Spheres/conics, planar RD=0, signed thickness and infinity sentinels.
       Fixed markers describe editing constraints and do not change the snapshot.
   * - ``AD`` through ``AG``; ``ASP ADO/ASR/ARA/ASX`` and ``ASn``
     - AD starts at r^4. ASR uses even radial powers, ARA all positive radial
       powers, ASX triangular-indexed XY monomials. Nonzero radial AS0 is rejected.
       Dimensional coefficients scale with their actual polynomial powers.
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
     - Ellipses, rectangles, triangles, quadrangles, rotation, obstructions and
       unions of intersecting aperture groups. Omitted legacy coordinates are
       zero. Transmitting/obstructing actions are supported; undeviated holes are
       diagnosed. APK copies a preceding special aperture.
   * - ``DCX/Y/Z``, ``TLA/B/C``, ``DT``, ``TOX/Y/Z``, ``GC``, ``RCO``, ``BEN``
     - OSLO intrinsic Euler rotations, signed X/Y tilts, translation order,
       pivots, preceding global references and coordinate returns. BEN supports
       single-axis local mirror bends; mixed-axis/global bends are rejected.
   * - ``PK CV/CVM/TH/THM/LN/LNM/AP/GLA/TD/TDM``
     - Static preceding-surface pickups, relative references and chains.
       Curvature and length constants are additive. Forward/self references are
       rejected. The result is an imported prescription, not live OSLO constraints.
       Solved values feed downstream pickups. TD/TDM retain local pivot data;
       pickups involving global references, coordinate returns or bends are rejected.
   * - ``PY``, ``PYC``, ``PU``, ``PUC``, ``EC``
     - Targeted marginal/chief height, outgoing slope and edge-contact solves.
       Targets are checked after solving. Unsupported or unsatisfiable solves
       retain saved values with a warning in permissive mode; strict mode rejects.
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

Import coverage is broader than OSLO export coverage. Native Optiland JSON is the
preferred way to retain imported general geometry, aperture composition and poses.
The OSLO writer supports its documented surface subset, direct spectral samples,
correct even-asphere powers, explicit angular/object-height fields and radial
aperture checking flags. Unsupported transformed surfaces, phase profiles and
non-radial apertures raise before the destination file is opened, preventing
silent loss of those features. Use native JSON for those systems.

Specification and real-file validation
--------------------------------------

Mappings were checked against Lambda Research's
`OSLO Program Reference (10 March 2021) <https://lambdares.com/hubfs/Support/support/oslo/oslo_releases/OSLOProgramReference.pdf>`_
(printed pp. 43-50: solves/apertures; 51-68: media/coordinates; 69-85:
surfaces/gratings; 120-122: system setup; 208-210: fields; 508-514: commands),
the `Optics Reference <https://lambdares.com/hubfs/Support/support/OSLOOpticsReference_Sep21.pdf>`_
(pp. 142-145: coordinate transforms; 170-171: grating equation), and the
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
Both NumPy and Torch imported all 101 files in permissive mode (5 without
warnings, 96 with warnings); 10 passed strict import. The on-axis smoke trace
transmitted at least one finite ray in 80 files, transmitted none in 20, and
raised an error in 1. None of the strict imports raised a trace error. These
figures include deliberately unsupported examples and are compatibility results,
not 101 validated optical designs. The audit JSON lists every filename and reason.
The remaining trace error is ``demos/edu/ebert.len``: its finite object-height
field and off-axis entry combination is unsupported by Optiland's field launcher.
Legacy ``RCO 0`` records in the demo archive are interpreted as the default undo
of the current local transform, consistently with the examples' surface placement.
