.. _material_discovery_queries:

Exact material discovery and property queries
=============================================

The core discovery and property APIs support material browsers and scripts.
Discovery identifies an exact registered entry; a summary captures optical
properties at a moment in time; a query filters those summaries. None of these
operations edits an optical system.

This is the first core installment for the Materials workspace. The desktop
browser, worker orchestration, portable conversion of file-backed materials,
and Apply/Undo integration are not part of this installment. Full desktop usage
guides and worked tutorials will accompany those workflows when implemented.

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
generation. The caller owns that generation: the core service does not watch
files, listen to document edits or increment a global registry generation.

``MaterialSnapshot`` stores immutable summaries and source stamps. A result can
therefore be attributed to the captured data rather than a later live registry
or UI selection. The receiving application must compare its request identifier
and source stamps with the active request before publishing the result.

For an unmodified, exact ``DataMaterial`` instance with ordinary
``HomogeneousPropagation``,
``MaterialSummary.owned_data`` contains the self-contained native JSON definition
as immutable bytes. Reconstruct it through ``reconstruct_material`` to obtain an
independent material, preserving its optical definition, bounds, metadata and
homogeneous propagation. A custom propagation model is not supported by this
initial owned-payload adapter. The bytes are data, not an external pickle or
executable script.

This installment does not convert ``Material`` or ``MaterialFile`` into owned
data. Their native persistence can refer to files or registry entries, and their
live coefficients may differ from disk. Such a summary has no portable owned
payload. Custom subclasses also require an explicit supported conversion;
serializing their display name is insufficient. Inspect ``owned_data`` before
requesting reconstruction and keep unsupported materials inspectable.

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
cancellation nor suppresses stale results itself. A desktop integration must
freeze sources before dispatch, use the shared job lifecycle, and check request
and source freshness when receiving a result. A complete material payload for
every eligible source, source-level read diagnostics and GUI integration remain
subsequent implementation work.
