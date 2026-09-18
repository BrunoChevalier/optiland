Portable GlassExpert replay
===========================

Use these public core functions to inspect or replay an exported desktop setup
without importing the GUI. The input is a bounded manifest, not executable Python
and not authority to modify any existing application document.

Input validation and records
----------------------------

``decode_glass_expert_manifest(data)`` accepts nonempty ``bytes`` of at most
64 MiB. It requires the current manifest format and validates finite values,
types, collection limits, exact target/pool identities, bounded definitions and
the correspondence between original and resolved variable/operand definitions.
It returns ``ReplayInputs(data, digest)``. The ``payload`` property returns a
fresh dictionary for inspection; mutating it does not change the retained bytes.
``encode_glass_expert_manifest(payload)`` validates and encodes the same current
format. Invalid or unsupported input raises ``ValueError``; there is no implicit
migration of an earlier development format.

Restore, replay and backend state
---------------------------------

``restore_replay_baseline(inputs, checkpoint=None)`` validates and restores the
stored baseline as a new ``Optic``. It does not run optimization. It selects the
recorded Optiland backend, including its recorded precision/device settings, in
the calling process. Install that optional backend when needed and use a fresh
process if the surrounding program must keep its own backend state unchanged.

``replay_glass_expert(inputs, checkpoint=None, progress=None)`` restores the
baseline, reconstructs and checks the frozen objective and bounds, validates the
embedded candidates, and runs the typed GlassExpert algorithm. It returns a
``ReplayResult`` with ``report`` and ``optic``. The optic is the newly created
complete result, never an open desktop document. Inspect
``report.candidate_eligible`` and handle ``report.candidate is None`` before
assuming a usable candidate. Neither function writes output files.

The optional ``checkpoint`` is a no-argument callback called at bounded work
boundaries. It can raise ``GlassExpertCancelled`` to stop work; cancellation is
propagated rather than returned as an apparently completed result. ``progress``
receives immutable ``GlassExpertProgress`` records. Exceptions from a callback
are not suppressed. Do not mutate optical state from progress callbacks.

Portable component limits
-------------------------

Native materials needed by the baseline, candidate pools and supported coatings
must be embedded as owned definitions. Missing catalog references are not
replaced by name. Unsupported custom/plugin constructors, external
``FileAperture`` resources and component bindings that cannot be faithfully
restored are rejected before they can become a portable replay. The supported
built-in optical classes and their native structure are validated before native
restoration; a module-looking name alone does not authorize a plugin.

Example
-------

.. code-block:: python

   from pathlib import Path
   from optiland.optimization.glass_expert_replay import (
       decode_glass_expert_manifest, replay_glass_expert,
   )
   from optiland.optimization.glass_expert_records import GlassExpertCancelled

   try:
       inputs = decode_glass_expert_manifest(
           Path("glass-expert-setup.json").read_bytes()
       )
       result = replay_glass_expert(inputs)
   except (ValueError, GlassExpertCancelled) as error:
       print(f"Replay did not complete: {error}")
   else:
       candidate = result.report.candidate
       if candidate is None or not result.report.candidate_eligible:
           print("No complete eligible candidate; inspect the report.")
       else:
           print("Candidate merit:", candidate.merit)
           candidate_optic = result.optic

Public API
----------

.. automodule:: optiland.optimization.glass_expert_replay
   :members:
   :undoc-members:

For the desktop export workflow, see :doc:`../../gui_glass_expert_tutorial`.
Loaded data never carry current-document acceptance authority.
