.. _material_discovery_queries:

Exact material discovery and property queries
=============================================

The discovery, source and property APIs support material browsers and scripts.
Discovery identifies an exact entry; a source adapter freezes its optical data;
a summary captures properties; a query filters those summaries. The GUI service
runs source loading and queries through the shared background job lifecycle.
None of these operations edits an optical system.

These APIs and the background service are the foundation for the Materials
workspace. The desktop browser, interactive map and Apply/Undo integration are
subsequent work. Full desktop usage guides and worked tutorials will accompany
those workflows; the examples here exercise the implemented core APIs.

Discover exact entries
----------------------

Use ``MaterialRegistry.entries(group=None, catalog=None)`` when the distinction
between equal names or references matters. It returns a tuple of frozen
``RegistryEntry`` records, each containing ``source_id``, ``record_id``,
``catalog``, ``group``, ``name``, ``reference`` and ``path``. The identifiers
distinguish entries; ``name`` and ``reference`` are descriptive fields.
The group and catalog filters compare complete names without case sensitivity;
they are not regular expressions. Entry identifiers remain stable when an
unrelated row is appended or a view is filtered. Identical repeated rows receive
distinct occurrence identifiers. These identifiers do not encode a file's
content revision.
Discovery retains installed entries even when a user entry shadows the same
catalog/name in ordinary lookup. The existing ``resolve`` shadowing policy is
unchanged; choose an exact entry explicitly when both sources must remain visible.

Catalog reference text may contain markup. Treat it as source data: display plain
text or sanitized content, and validate link targets in any desktop adapter.

.. code-block:: python

   from optiland.materials import MaterialRegistry
   from optiland.materials.properties import MaterialKey, describe_material

   registry = MaterialRegistry.instance()
   schott = registry.entries(catalog="schott")
   bk7_entries = tuple(entry for entry in schott if entry.name == "N-BK7")
   assert bk7_entries

   # Inspect every exact record; never infer a unique identity from the label.
   for entry in bk7_entries:
       material = entry.open_material(bounds="raise")
       key = MaterialKey(entry.source_id, entry.catalog, entry.record_id)
       summary = describe_material(material, key)
       print(entry.reference, summary.nd.value, summary.vd.value)

``open_material()`` reads the selected path directly with ``MaterialFile``.
It does not re-resolve a name, choose a different reference or perform fuzzy
matching. The optional bounds policy defaults to ``"clamp"``, matching the
existing file-material API; this example chooses ``"raise"`` explicitly.

A discovery entry is a local handle, not a snapshot of its file contents.
Editing, replacing or removing that file affects a later open. A programmatic
registration can refer to a process-created temporary file. Do not treat the
entry or its path as a portable material definition, and do not assume the same
registry exists in another process. Reading and parsing the file can be costly;
a desktop adapter must schedule that work away from the GUI thread.

Interpret optical properties
----------------------------

``describe_material(material, key)`` uses the material's shared ``n`` evaluator.
It computes the d-line index at 0.5875618 micrometers and the Abbe number from
the F and C lines at 0.4861327 and 0.6562725 micrometers:

.. math::

   V_d = \frac{n_d - 1}{n_F - n_C}.

The calculation does not call ``material.abbe``: some material families expose
a live parameter under that name instead of a method. Negative finite Abbe
numbers remain valid query coordinates. Zero dispersion produces an unavailable
Abbe value, rather than a point with an infinite coordinate.

Known n/k support and the evaluation bounds policy are separate fields. A table
configured to clamp may produce an endpoint index outside its measured range,
but that does not make the reference line supported. Summary calculations do
not use such clamped values as supported patent-query coordinates, and browsing
does not change the original bounds policy. Unknown spectral limits remain
unknown; they are not represented as an unlimited measured interval.

``has_extinction_data`` and ``has_transmission_data`` describe the presence of
optical data, independently of a numerical ``k()`` result. Missing extinction
can evaluate to zero as a modeling assumption. That is different from measured
zero absorption or a measured transmission sample of zero. For an unsupported
custom material family, availability can be unknown rather than guessed.

Summaries use the material's default evaluation conditions. They do not introduce
a common temperature or air/absolute-index conversion, and do not change the
environment of an ``Optic``. A caller combining sources must retain and explain
their conventions before interpreting small numerical differences.

Snapshot ownership
------------------

``MaterialKey(source_id, catalog, record_id)`` is the identity shared by queries
and displays. ``SourceStamp(source_id, generation)`` identifies a captured source
generation. Source adapters derive this token from the captured content. Callers
constructing ``MaterialSnapshot`` directly must supply their own generation.
The core APIs do not watch files or listen to document edits.

``MaterialSnapshot`` stores immutable summaries and source stamps. A result can
therefore be attributed to the captured data rather than a later live registry
or UI selection. The receiving application must compare its request identifier
and source stamps with the active request before publishing the result.

``MaterialSummary.owned_data`` is an optional portable optical payload, stored
as immutable native JSON bytes. ``reconstruct_material(summary)`` restores an
independent material from that payload. No file path, display name or catalog
lookup is used to find a replacement. The bytes are data, not a pickle or an
executable script. Inspect ``owned_data`` before requesting reconstruction;
unsupported materials still need visible diagnostic records.

Portable live material state
----------------------------

``capture_material(material)`` and ``restore_material(payload)`` in
:mod:`optiland.materials.portability` provide the same ownership contract for
scripts and source adapters. Supported exact types are ``DataMaterial``,
``MaterialFile``, ``Material``, ``IdealMaterial``, ``AbbeMaterial`` and
``AbbeMaterialE``, with their unmodified built-in homogeneous propagation bound
to the material itself. A restored propagation model belongs to the restored
material, not the original object.

File-backed materials become ``DataMaterial`` instances. Conversion copies
their current coefficients, independent n/k sample grids, bounds and metadata
without reopening the file. It also copies active Schott thermal state: both
a reference temperature and a nonzero thermal coefficient are required, as in
the file evaluator. All-zero or inactive thermal data stays inactive. Already
owned definitions retain their native thermal and internal-transmission data.
Ideal and Buchdahl d/e Abbe models retain their type and live optical parameters.
Polynomial ``AbbeMaterial`` instances become ``DataMaterial`` instances using
the exact small coefficient vector derived by the existing polynomial model,
expressed through the shared formula 5 evaluator on 0.380–0.750 micrometers.
This also captures changed live polynomial fit constants. It does not sample a
spectrum, perform a new fit or reopen the bundled fit file.

Equivalence for converted file materials is limited to their declared spectral
domain. ``DataMaterial`` enforces analytic formula support even where the
original file adapter allowed evaluation outside it. The conversion preserves
the table bounds policy; it does not make extrapolated or clamped values into
supported measurements. No formula or extinction spectrum is approximated with
new samples. Missing extinction stays missing, even when the model evaluates k
as zero.

Custom subclasses, instance-patched evaluators, custom formula callables,
custom propagation, modified Buchdahl constants and unsupported single-sample
tables raise ``ValueError`` during capture. An explicit adapter is needed for
those cases; storing a label or substituting a different model would change the
optical behavior. Capture is a numerical snapshot, not a retained autodifferentiation
graph.

Restore payloads produced by ``capture_material``. The portable decoder rejects
filename/catalog requests and native polynomial Abbe lookup payloads; captured
polynomial materials already contain their owned formula definition and require
no packaged fit data during restoration. These restrictions do not change the
ordinary native material persistence API.

``MaterialFile.from_data(mapping, name=..., bounds=...)`` parses an already
captured refractiveindex.info YAML mapping. It copies the mapping and requires
no file or registry. ``name`` and captured ``source_file`` metadata are diagnostic
labels. This constructor creates an exact ``MaterialFile``; it is not a catalog
resolution constructor for ``Material``. Its ordinary ``to_dict()`` still uses
the existing filename-based persistence format. Use ``capture_material`` for
portable transport, or ``to_owned_material()`` to obtain the owned object.

This self-contained example changes a live formula coefficient before capture.
It uses a synthetic formula and never creates the named file:

.. code-block:: python

   import numpy as np

   import optiland.backend as be
   from optiland.materials import MaterialFile
   from optiland.materials.portability import capture_material, restore_material

   material = MaterialFile.from_data(
       {
           "REFERENCES": "Synthetic documentation example",
           "DATA": [{
               "type": "formula 5",
               "wavelength_range": "0.4 0.8",
               "coefficients": "1.5 0.004 -2",
           }],
       },
       name="example-only.yml",
       bounds="raise",
   )
   material.coefficients[0] = 1.51
   frozen = capture_material(material)
   restored = restore_material(frozen)
   waves = be.array([0.45, 0.55, 0.75])
   np.testing.assert_allclose(
       be.to_numpy(restored.n(waves)), be.to_numpy(material.n(waves))
   )
   assert restored.bounds == "raise"
   assert restored.propagation_model.material is restored

   material.coefficients[0] = 1.60
   assert capture_material(material) != frozen
   np.testing.assert_allclose(
       be.to_numpy(restored.n(waves)),
       be.to_numpy(restore_material(frozen).n(waves)),
   )

Freeze registry, GLC and current-design sources
-----------------------------------------------

:mod:`optiland.materials.sources` separates source requests from their loaded
results. ``load_source(request, checkpoint=...)`` returns ``SourceResult`` with
an immutable ``snapshot``, a tuple of ``diagnostics`` and a ``failed`` flag.
Each diagnostic retains its source identity and, where available, record
identity. A diagnostic is data to present alongside the source, not permission
to resolve a different similarly named material.

The supported source requests are:

* ``RegistrySource(source_id, entries)`` captures the exact ``RegistryEntry``
  records selected by the caller. The loader reads each selected RII YAML
  buffer, parses that same captured content, and converts supported records
  to owned optical definitions. It does not resolve display names again.
* ``GlcSource(source_id, path)`` reads one OSLO GLC file and uses the existing
  GLC decoder. Records retain source and record identity. Equal glass names in
  different files remain distinct; the name-keyed OSLO lens-file resolver is
  not used to deduplicate browser entries. This adapter does not add AGF, CDB
  or arbitrary XML support.
* ``OwnedSource(source_id, entries)`` takes immutable ``OwnedEntry`` records
  containing native payloads, or a diagnostic reason when capture was
  unsupported. It requires no registry or source file in the receiving process.

Choose source identifiers that remain stable for the lifetime of the source in
your application. The resulting generation hashes describe the captured input;
they are separate from material identities. Reloading changed file contents or
capturing changed live optical parameters produces a new generation. A stamp
does not promise that a file cannot change after reading, and the loader does
not run a file watcher.

The following example restricts a registry source to exact Schott N-BK7 records
and freezes the result before querying it:

.. code-block:: python

   from optiland.materials import MaterialRegistry
   from optiland.materials.properties import MaterialQuery, query_materials
   from optiland.materials.sources import RegistrySource, load_source

   entries = tuple(
       entry for entry in MaterialRegistry.instance().entries(catalog="schott")
       if entry.name == "N-BK7"
   )
   source_id = entries[0].source_id
   entries = tuple(entry for entry in entries if entry.source_id == source_id)
   loaded = load_source(RegistrySource(source_id, entries))
   assert not loaded.failed
   assert loaded.snapshot.materials
   result = query_materials(loaded.snapshot, MaterialQuery(name="bk7"))
   assert len(result.matches) == len(loaded.snapshot.materials)
   assert result.stamps == loaded.snapshot.stamps
   for diagnostic in loaded.diagnostics:
       print(diagnostic.record_id, diagnostic.message)

GLC definitions preserve their native dispersion, thermal and measured internal
transmission data. A source path is provenance, not a later restoration
dependency. Cost and availability fields, when present, remain raw catalog
metadata; they do not establish current prices or stock. The decoded
``internal_transmittance`` behavior continues to interpolate measured
transmission before applying a thickness change. It is not reconstructed by
interpolating derived extinction coefficients.

This synthetic example gives the same glass name to records in two temporary
GLC files. The source identifiers keep both records addressable, and their owned
payloads survive removal of those files:

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory

   from optiland.materials.properties import reconstruct_material
   from optiland.materials.sources import GlcSource, load_source

   record = (
       "DEMO_GLASS 1.5 60 2.5 1.2 70 .9 1 300 12345 ? A "
       "3 3 1.4 .002 .0001 0 0 0 0"
   )
   with TemporaryDirectory() as directory:
       results = []
       for source_id in ("example:catalog-a", "example:catalog-b"):
           path = Path(directory) / (source_id.rsplit(":", 1)[1] + ".glc")
           path.write_text("7.02 1 Synthetic example\n" + record + "\n")
           results.append(load_source(GlcSource(source_id, str(path))))
       assert all(not result.failed for result in results)
       first, second = (result.snapshot.materials[0] for result in results)
       assert first.display_name == second.display_name
       assert first.key != second.key

   restored = reconstruct_material(first)
   assert restored.display_name == "DEMO_GLASS"

``capture_design(material_occurrences, document_id=...)`` captures live material
parameters into an ``OwnedSource`` identified by ``"design:" + document_id``.
Pass pairs of stable occurrence identifiers and material instances. In the GUI,
occurrence identifiers must follow retained surface identity rather than the
current row number. The material following a surface is ``material_post``;
inspecting it does not authorize changing it. The capture copies optical state
without sampling optical functions or reopening files. Polynomial capture
derives its small coefficient vector; subsequent source loading evaluates the
detached owned state at the reference lines.

Distinct occurrences remain distinct even if they share one material object or
display name. Replacing the document requires a new document identity. Within a
document, an updated capture reflects changes to live coefficients or material
definitions. Callers must invalidate an old current-design request when the
relevant document state changes, including history restoration and replacement.
Catalog-only requests need not be invalidated by unrelated optical edits.

.. code-block:: python

   from optiland.materials import IdealMaterial
   from optiland.materials.sources import capture_design, load_source

   glass = IdealMaterial(1.52)
   captured = capture_design(
       [("surface-a:after", glass), ("surface-b:after", glass)],
       document_id="documentation-design",
   )
   glass.index[0] = 1.60
   loaded = load_source(captured)
   assert len(loaded.snapshot.materials) == 2
   assert loaded.snapshot.materials[0].key != loaded.snapshot.materials[1].key
   assert all(
       abs(summary.nd.value - 1.52) < 1e-6
       for summary in loaded.snapshot.materials
   )
   assert loaded.snapshot.stamps[0].source_id == "design:documentation-design"

The optional ``checkpoint`` callback is invoked during source loading so a
caller can stop an obsolete operation by raising its cancellation exception.
The synchronous core loader does not own cancellation timers or publish GUI
state. Keep loading and coordinate evaluation off the Qt thread.

Registry requests must contain entries belonging to their declared source ID;
group a mixed discovery result by ``entry.source_id`` before loading it. A
registry source can contain healthy records alongside unreadable records. The
latter remain inspectable through unavailable summaries and diagnostics. An
entirely unreadable, nonempty registry source is marked failed. Malformed GLC
files fail as a whole under the existing decoder's validation rules. Unsupported
owned occurrences remain diagnostic records rather than disappearing from a
current-design capture. File and record limits reject oversized sources instead
of silently truncating them.

Query an owned snapshot
-----------------------

This example supplies synthetic measurements at the three reference lines. Its
expected coordinates follow directly from the samples: ``nd = 1.52`` and
``vd = (1.52 - 1) / (1.525 - 1.515) = 52``. The material is illustrative data,
not a claim about a commercial glass.

.. code-block:: python

   from optiland.materials import DataMaterial
   from optiland.materials.properties import (
       MaterialKey,
       MaterialQuery,
       MaterialSnapshot,
       SourceStamp,
       describe_material,
       query_materials,
       reconstruct_material,
   )

   measured = DataMaterial.from_samples(
       [0.4861327, 0.5875618, 0.6562725],
       [1.525, 1.520, 1.515],
       name="Synthetic measurement",
       metadata={"source": "documentation example"},
   )
   key = MaterialKey("example:measurements", "laboratory", "sample-001")
   summary = describe_material(measured, key)
   snapshot = MaterialSnapshot(
       stamps=(SourceStamp(key.source_id, "measurement-v1"),),
       materials=(summary,),
   )
   query = MaterialQuery(
       target_nd=1.520,
       nd_tolerance=0.002,
       target_vd=52.0,
       vd_tolerance=1.0,
       wavelength_range_um=(0.50, 0.65),
   )
   result = query_materials(snapshot, query, request_id=7)
   assert result.request_id == 7
   assert result.stamps == snapshot.stamps
   assert result.matches[0].key == key
   print(result.matches[0].delta_nd, result.matches[0].delta_vd)

   restored = reconstruct_material(summary)
   assert restored is not measured
   assert restored.definition == measured.definition
   assert restored.bounds == measured.bounds == "raise"

Each requested target needs its own finite positive tolerance. A material passes
only when each requested absolute difference is within its corresponding bound,
including the boundary. The signed differences are candidate minus target.
Passing records are ranked by the Euclidean length of the differences divided
by their individual tolerances; ties are ordered by the full ``MaterialKey``.
This ranking does not replace the separate pass/fail bounds.

``MaterialQuery`` also accepts source identifiers, catalog names and a
case-insensitive substring filter for display names or record identifiers.
Catalog names compare without case sensitivity; source identifiers compare
exactly. Empty filter tuples impose no restriction. With no coordinate targets,
records with unavailable coordinates remain inspectable and ties follow the
identity order.

An optional ``wavelength_range_um`` requires the entire interval to lie within
known n support. By default, records with unknown support are excluded when that
filter is active. ``unknown_policy="include"`` retains them explicitly; it does
not establish their validity. This filter does not test k or transmission
support. ``excluded_counts`` counts only the first failed predicate for each
record, so each excluded material contributes once.

The ``PropertyValue.status`` values are ``available`` (finite coordinate),
``missing`` (no value, including undefined zero-dispersion Abbe number),
``unsupported`` (such as a reference line outside declared support or a
non-scalar model), and ``invalid`` (such as a nonfinite or nonpositive glass-map
index). A missing value is ``None``, never a numeric zero sentinel. Evaluation
of a model with unknown support can still yield an available coordinate; consult
the separate support field before making claims about measured validity.

``query_materials`` runs synchronously and returns data only. It neither manages
cancellation nor suppresses stale results itself. The desktop service described
below supplies that lifecycle; scripts must manage their own dispatch and
freshness checks.

Background service ownership
----------------------------

``optiland_gui.services.materials_service.MaterialsService`` owns published source
snapshots for a future Materials panel. The connector exposes its shared instance
as ``connector.materials``, backed by ``connector.calculation_jobs`` and the
existing document state. Code constructing an isolated service supplies those
``CalculationJobs`` and ``DocumentState`` objects explicitly. It borrows that
queue; it does not create a competing process manager. Its worker handlers perform source
reads, parsing, property evaluation and query filtering. Only copying already
live optical parameters with ``capture_design`` belongs on the GUI thread.

.. list-table:: Service integration points
   :header-rows: 1
   :widths: 35 65

   * - API
     - Behavior
   * - ``load_source(request)``
     - Queues one source replacement and returns its job request, or ``None``
       if submission fails. Connect ``source_changed`` and ``source_failed``
       to update the view when publication succeeds or fails.
   * - ``source_ids``, ``snapshot``, ``source_result(id)``
     - Read accepted snapshots and diagnostics. No catalog evaluation or file
       reads occur while constructing these views.
   * - ``source_error(id)``
     - Read the latest load failure while still showing an earlier accepted
       snapshot, if one exists.
   * - ``query(MaterialQuery)``
     - Queues the newest filter request over its selected published sources.
       ``query_finished`` supplies a generation-bound ``MaterialQueryResult``;
       ``query_failed`` reports an execution error.
   * - ``material(MaterialKey)``
     - Retrieves the exact accepted summary, including its owned payload.
       Query worker inputs omit those potentially large payloads and carry
       only the properties needed for filtering.
   * - ``cancel_source(id)``, ``cancel_query()``
     - Revoke pending work. Canceling a reload keeps any already published
       source. Canceled or superseded results cannot later replace accepted data.
   * - ``remove_source(id)``
     - Removes its snapshot and error, cancels its pending load and invalidates
       dependent query ownership.
   * - ``close()``
     - Cancels only this service's targets, discards its cache and rejects
       subsequent requests. Other consumers of the shared queue remain active.

Publication is atomic: the service accepts a complete source result only while
it still owns that request and its backend/document context. A failed reload
retains the previous healthy snapshot and emits a visible failure. Capacity
errors behave the same way; the service defaults to at most 32 sources,
100,000 records and 128 MiB of retained native payload bytes, configurable at
construction through ``max_sources``, ``max_records`` and ``max_payload_bytes``.
The view must distinguish
retained data from a successful reload rather than hiding the failure behind
the old records.

Each new query supersedes its predecessor. Acceptance checks the request serial,
captured source generations, current accepted sources, backend and any relevant
document token. Successful source replacement or removal invalidates cached
query results; connect ``query_invalidated`` to clear obsolete result state.
``state_changed`` exposes source transitions such as loading, ready, failed and
cancelled, and ``busy`` reports pending source/query work.

An observed backend change invalidates pending or cached queries while retaining
accepted numeric catalog snapshots. A new query can filter those saved
coordinates; reload a source to evaluate its properties under the new backend
or precision. Stopping the shared job service closes the Materials service.

Current-design sources must use the current document identity. Document changes
that affect optical state, including document replacement, invalidate those
snapshots and mixed queries containing them. Metadata/presentation edits do not
advance the optical token. Catalog-only loads and queries remain usable across
unrelated document edits. Changes made
through the application must pass through its existing document notification
contract; direct unannounced mutation of a Python object cannot be detected by
this service.

Cancellation uses the shared queue's cooperative checkpoints and bounded process
teardown. It does not wait synchronously on the GUI thread; revoked results
remain ineligible for publication even if a worker finishes during cancellation.

These are data-service contracts. They do not yet supply a Materials dock,
selection/hover state, comparison pins, target-surface routing or an Apply
command. A future Apply command must independently verify its target and
material freshness and use the established undoable document mutation path.
