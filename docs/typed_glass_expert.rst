.. _typed-glass-expert:

Optimize an exact, owned glass pool
===================================

GlassExpert searches categorical material choices while optimizing continuous
lens variables. The public ``MaterialPool`` API keeps source-qualified identities
and owned native definitions, so two records can share a display name without
becoming interchangeable. This example uses only the Python API; it needs no GUI,
catalog registration or network connection. The established catalog-name API is
still demonstrated in
:doc:`examples/Tutorial_3e_Glass_Expert_Categorical_Optimization`.

For native desktop configuration, frozen review and explicit whole-candidate
acceptance, follow :doc:`gui_glass_expert_tutorial`. Exported desktop scripts use
the Qt-free :doc:`api/optimization/optimization.glass_expert_replay` API.

Download :download:`typed_glass_expert.py <samples/glassmap/typed_glass_expert.py>`
and run it in an environment containing this development revision:

.. code-block:: console

   python typed_glass_expert.py

The script constructs a synthetic singlet, captures two synthetic materials,
round-trips the pool through JSON, runs a bounded search with a recorded seed,
and prints the immutable report as finite JSON. The models are teaching data,
not manufactured glasses. It writes no files and does not modify a GUI document.

In the recorded NumPy run, five trials used 70 objective evaluations. The
reference squared merit was approximately 111.6808859; the complete candidate
was about 5.25 × 10⁻¹⁵, with a front radius of 119.088352 mm. Treat these as
reference values with numerical tolerance, not exact cross-platform output.

Capture identities and eligible definitions
-------------------------------------------

``MaterialKey(source_id, catalog, record_id)`` is the resolver identity.
``SourceStamp`` records that source's generation. A source adapter normally
supplies both; this self-contained example assigns explicit synthetic keys and
derives a generation from the captured definitions. The two labels are both
``Synthetic crown``, while their record IDs are ``crown-a`` and ``crown-b``.

.. literalinclude:: samples/glassmap/typed_glass_expert.py
   :language: python
   :pyobject: make_pool

``describe_material`` computes native-condition reference coordinates.
``prepare_material_candidate`` restores the actual owned definition, verifies
the computed d/F/C coordinates and validates the lens wavelengths before returning
a candidate. Do not replace computed coordinates with rounded supplier summary
values, or treat constructing/deserializing a record as proof of optical validity.
Construction canonicalizes finite native JSON structurally without evaluating it.
GlassExpert validates the pool again when it runs.

The pool rejects empty candidate lists, duplicate exact keys and conflicting
source generations. Equal names or coordinates are allowed. Each candidate
restores a fresh native material; targets do not share a mutable cached material
instance merely because they select the same candidate. Captured conditions and
unknown extinction remain attributed rather than silently normalized or turned
into a measured transmission claim.

Keep the reference baseline separate
------------------------------------

The singlet starts with ``Reference crown``, which is deliberately outside the
pool. Adding the typed material variable does not substitute a candidate into
the lens. Its captured reference remains available for baseline merit and
rollback, but does not gain membership through a matching name or coordinate.

.. code-block:: python

   problem.add_variable(
       lens, "radius", surface_number=1, min_val=20.0, max_val=150.0
   )
   problem.add_variable(
       lens, "material", surface_number=1, glass_selection=pool
   )
   problem.add_operand(
       "f2", target=60.0, weight=1.0, input_data={"optic": lens}
   )

The material follows surface 1, consistent with :doc:`conventions`. The radius
is a continuous variable with physical bounds. Categorical material keys use
identity scaling; they are not numbers passed to the continuous solver.
``variable_ids`` names every original variable in its original order, allowing
the report to identify physical continuous values and categorical assignments.

Run a reproducible bounded search
---------------------------------

.. literalinclude:: samples/glassmap/typed_glass_expert.py
   :language: python
   :pyobject: run_example

``global_samples=2`` explores two distinct representatives of each nontrivial
pool. Global sampling uses a run-local seeded farthest-point selection in scaled
n_d/V_d coordinates. It deliberately replaces GlassExpert's earlier internal
K-Means sampling. Canonical exact-key ordering and deterministic tie breaks keep
coincident records distinct; the general material-map K-Means helper is unchanged.

Both ``nd_scale`` and ``vd_scale`` must be finite and positive. They define the
distance ``hypot(Δn_d / nd_scale, ΔV_d / vd_scale)`` for global and local choices;
they are search geometry, not optical merit weights. The solver still evaluates
the frozen objective for each complete assignment. A seed controls discrete
sampling, not bitwise solver equality across machines or numerical backends.

``num_neighbours`` controls local alternatives and supplies the global sample
count when ``global_samples`` is omitted. Each nontrivial pool must contain at
least the requested global count, which must be at least two. Local search uses
the available distinct alternatives, so a two-entry pool with two global samples
is valid. A singleton is a fixed assignment; it is included in every complete
candidate but never sampled. GlassExpert requires at least one nontrivial pool
and one supported continuous variable. Use ordinary continuous optimization for
an all-fixed assignment.

Read merit, eligibility and outcome separately
----------------------------------------------

``result.glass_expert`` contains the immutable ``GlassExpertReport``.
``baseline`` describes the untouched reference, and ``candidate`` describes a
complete assignment only when one was validated. Its material choices include
exact keys and native fingerprints. A reference-only choice has no pool key.
``result.x`` remains the continuous solver vector in its solver/scaled coordinates;
the report's continuous values are unscaled physical values.

The primary merit is **sum of squared weighted residuals**, not RSS. Compare the
same quantity on both sides before computing an improvement percentage. A valid
in-pool candidate may be worse than the reference when the pool is restrictive;
that is ``no_improvement``, not an improved lens. Eligibility, improvement and
continuous-solver convergence are separate facts. Do not use ``result.success``
alone to decide whether to adopt a design.

An exactly zero reference merit returns ``already_reached`` without inventing an
eligible candidate or dividing by zero. ``no_candidate`` means the bounded search
found no valid sampled state; it does not prove that the entire optimization
problem is infeasible. Failed or nonfinite trials carry reasons and cannot replace
an earlier validated best. A failed or worse final polish preserves that best.

The report retains a bounded trial history: ``report_limit`` defaults to 1,000
and accepts 1–10,000. Total trial/evaluation counters remain complete when older
entries are omitted; ``omitted_trials`` makes the truncation explicit.

Direct-call state and cancellation
----------------------------------

A normal direct Python call leaves its optic at the validated complete candidate.
Run on a separately captured/restored optic when you need to review before
changing your working design. The original variable manager and descriptor order
are restored after every terminal path.

This categorical path requires the built-in optic updater. Custom updater
subclasses or patched update methods need a supported adapter; topology changes
during a trial cannot be made safe merely by checking them afterward.

The optional ``checkpoint`` callable is invoked at safe setup, sampling, trial,
continuous-objective and final-capture boundaries. It must raise to interrupt.
A truthy return from the ordinary solver ``callback`` also requests cancellation.
Cancellation raises ``GlassExpertCancelled`` with its bounded ``report``;
``optimizer.last_report`` also retains the most recent terminal report.
Cancellation and setup errors restore the direct caller's captured starting
material and continuous state. ``progress`` receives bounded typed events;
it is distinct from an optional
solver callback and does not itself authorize applying anything to a GUI document.

For implementation details and the supported catalog-name call shape, see
:doc:`developers_guide/optimization_framework` and
:doc:`api/optimization/optimization.optimizer.scipy.glass_expert`.
