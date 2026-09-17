Optimization Framework
======================

Optimization in Optiland allows users to improve the performance of optical systems by adjusting design parameters to minimize
(or maximize) a merit function. The framework supports a wide range of optimizers and a flexible system for defining operands and
variables. The framework integrates tightly with the `Optic` class.

The Optiland optimization framework includes the following components:

- **Operands**: Quantitative metrics for evaluating optical system performance or properties (e.g., RMS spot size, wavefront error, etc.).
- **Variables**: System parameters that can be adjusted (e.g., surface curvatures, separations).
- **Scaling**: Methods to scale optimization variables to a (roughly) common range for improved convergence and performance.
- **Optimization Problem Class**: Encapsulates the problem definition, linking operands and variables.
- **Optimizers**: Algorithms for solving optimization problems, such as gradient-based methods or evolutionary strategies.

.. figure:: ../_static/cooke_triplet_lens_optimization_evolution.gif
  :width: 60%
  :align: center

.. figure:: ../_static/cooke_triplet_merit_function_evolution.gif
  :width: 60%
  :align: center

.. raw:: html

   <div style="margin-top: 5em;"></div>



Components Explained
--------------------

1. **Optimization Problem**:

   - The `OptimizationProblem` class orchestrates the optimization process.
   - Key responsibilities include:

     - Adding **operands** to define the merit function.
     - Adding **variables** to define the parameters to optimize.
     - Computing the overall objective function value.
   - Optionally batching compatible ray operands to avoid redundant traces.
   - Providing both squared terms (`fun_array`) and residual vectors (`residual_vector`) for least-squares optimizers.

2. **Optimizers**:

   - A base `Optimizer` class wraps `scipy.optimize.minimize` and provides a unified interface.
   - Built-in optimizers include:

     - **Dual Annealing** (global)
     - **Differential Evolution** (global)
     - **Basin Hopping** (global)
     - **SHGO** (global)
     - **Least Squares** (local)
     - **Orthogonal Descent** (local)
     - **Nelder-Mead**, **Powell**, **BFGS**, **L-BFGS-B**, **COBYLA**, etc. (local optimization, from `scipy.optimize.minimize`)
     - **CMA-ES** and **Particle Swarm** (custom, gradient-free global optimizers in `optiland.optimization.optimizer.custom`)
     - **Adam** and **SGD** (PyTorch-native gradient-based optimizers in `optiland.optimization.optimizer.torch`, useful for differentiable/autograd workflows)
     - **GlassExpert** (mixed continuous/categorical optimizer for glass selection; see below)
   - Users can subclass the base optimizer for custom methods.

3. **Operands and Variables**:

   - **Operands**: Define individual contributions to the merit function. Examples:

     - RMS Spot Size
     - Wavefront Error
     - Focal Length
   - **Variables**: Define the parameters to optimize, such as:

     - Radius of curvature
     - Conic constants
     - Material refractive indices
     - Surface tilts and decenters
     - Geometry parameters (e.g., freeform coefficients)

4. **Scaling**:

   - Scaling methods help improve optimization performance by normalizing variable ranges.
   - Built-in scalers include:

     - Linear Scaling
     - Logarithmic Scaling
     - Power Scaling
     - Reciprocal Scaling

.. note::
   The optimization framework is written in a modular way, allowing users to easily extend the framework with custom optimizers, operands, and variables.

.. raw:: html

   <div style="margin-top: 5em;"></div>




Typical Optimization Process
----------------------------

1. **Set Up the Problem**. Create an instance of `OptimizationProblem`:

.. code:: python

   from optiland.optimization import OptimizationProblem
   problem = OptimizationProblem()

2. **Add Operands**. Add operands to define the merit function:

.. code:: python

   input_data = {'optic': lens}

   # Add focal length operand
   problem.add_operand(operand_type='f2', target=50, weight=1, input_data=input_data)

3. **Add Variables**. Define the parameters to optimize:

.. code:: python

   # Add radius of curvature variable for second surface
   problem.add_variable(lens, 'radius', surface_number=2)

4. **(Optional) Configure Batched Ray Evaluation**. Batching is enabled by default. If you need to compare with legacy per-operand behavior, disable it explicitly:

.. code:: python

   # Squared weighted deltas (used by many optimizers)
   merit = problem.sum_squared()

   # Unsquared weighted deltas (useful for least-squares style methods)
   residuals = problem.residual_vector()

   # Disable batching to opt out (legacy per-operand evaluation)
   problem.disable_batching()

5. **Choose an Optimizer**. Select an optimizer and run the optimization:

.. code:: python

   from optiland.optimization import OptimizerGeneric
   optimizer = OptimizerGeneric(problem)
   result = optimizer.optimize()

6. **Review Results**. Print optimization results and visualize performance:

.. code:: python

   problem.info()  # print optimization problem details
   print(result)   # standard output from scipy.optimize.minimize
   lens.draw()     # Plot the lens in 2D


Batched Ray Evaluation Internals
--------------------------------

The batching path is implemented by `optiland.optimization.batched_evaluator.BatchedRayEvaluator` and is integrated into `OptimizationProblem` by default. You can opt out with `disable_batching()` and re-enable with `enable_batching()`.

When enabled, the evaluator performs three steps:

1. Group compatible operands that can share the same trace call.
2. Execute the minimum required set of `trace_generic` and `trace` calls.
3. Extract per-operand values from shared traced arrays while preserving backend behavior and autograd.

Current batching support includes:

- **Single-ray (`trace_generic`) operands** such as `real_x_intercept`, `real_y_intercept`, `real_z_intercept`, local-coordinate intercept variants, direction cosines (`real_L`, `real_M`, `real_N`), and `AOI`.
- **Distribution (`trace`) operands** for `rms_spot_size` when trace parameters match.

Operands that are not currently batchable are evaluated through the standard direct path, so mixed merit functions are fully supported.

For PyTorch workflows, this design keeps gradients valid because values are extracted by tensor indexing from traced data (without detaching). For NumPy workflows, behavior remains numerically equivalent to the standard per-operand evaluation path.


.. figure:: ../_static/cooke_triplet_starting_point.png
  :width: 80%
  :align: center

.. figure:: ../_static/cooke_triplet_optimized.png
  :width: 80%
  :align: center

.. raw:: html

   <div style="margin-top: 5em;"></div>




Understanding Operands
----------------------

Operands represent individual components of the merit function. To find the inputs required for a specific operand:

- Refer to the operand registry in the Operand module, or the API documentation.
- Use operand-specific documentation for parameter details. For example, the RMS spot size requires a field as an input, while the focal length does not. All operands require a target value, weight, and an `Optic` instance. Note that the ``weight`` of an operand dictates its contribution relative to other operands in the merit function. For operands evaluated across the pupil or across a spectrum (e.g., standard RMS Spot Size), the merit function also automatically accounts for the intrinsic ``weight`` assigned to the ``Field`` and ``Wavelength`` objects defined in the ``Optic``.

.. raw:: html

   <div style="margin-top: 5em;"></div>



Extending Optimization
----------------------

Custom operands, variables and optimization algorithms can be added by subclassing the appropriate base classes. For example:

- Subclass VariableBehavior to create a new variable type, then register it within the Variable class.
- Define a new operand function and register it within the Operand module.
- Subclass OptimizerGeneric to create a new optimization algorithm.

.. tip::
   See the :ref:`Learning Guide <example_gallery>` for demonstrations of custom optimization algorithms and user-defined operands.

How to Extend This
------------------

**Scenario:** Add a new optimization operand to Optiland.

**Step 1:** Add a new function to ``optiland/optimization/operand/operand.py`` (or the relevant
``optiland/optimization/operand/*.py`` module, e.g. ``ray.py``, ``aberration.py``, ``paraxial.py``).
**Step 2:** Register it either by adding an entry to the ``METRIC_DICT`` dict near the top of
``operand.py``, or at runtime via ``operand_registry.register(name, func)``.
**Step 3:** Add tests in ``tests/test_optimization/test_operand.py``.

To add a new **variable type**: subclass ``VariableBehavior`` in
``optiland/optimization/variable/base.py`` and register the new type in the ``Variable`` class
(``optiland/optimization/variable/variable.py``).

To add a new **optimizer**: subclass ``OptimizerGeneric`` in
``optiland/optimization/optimizer/scipy/base.py``.

For step-by-step guidance, see :ref:`extension_recipes`.
For a worked example, see :doc:`../examples/Tutorial_3c_User_Defined_Optimization`.


.. raw:: html

   <div style="margin-top: 5em;"></div>



Categorical Optimization with Glass Expert
------------------------------------------

GlassExpert searches categorical material assignments while optimizing continuous
lens parameters. Its search is bounded and heuristic; it does not prove a global
optimum. The supported catalog-name API remains available. The typed
``MaterialPool`` API additionally preserves exact source/catalog/record identities
and owned native definitions, including same-name records and custom data.
Follow :doc:`../typed_glass_expert` for a runnable, catalog-independent example.

**Architecture**

The GlassExpert class inherits from `OptimizerGeneric` and extends its capabilities to manage material variables defined by their refractive index (n_d) and Abbe number (V_d). The core algorithm operates in phases:

1.  **Initialization**

    - The optimization problem is set up with both continuous variables (e.g., radii, thicknesses) and categorical glass variables.
    - Each glass variable is associated with a list of candidate glasses from a catalog (e.g., Schott, Ohara):

      .. code:: python

         glasses = material_utils.glasses_selection(0.4, 0.7, catalogs=["schott", "ohara"])

         problem.add_variable(lens, "material", surface_number=1, glass_selection=glasses)

    - Then the optimizer is setup with:

      .. code:: python

         optimizer = optimization.GlassExpert(problem)
         res = optimizer.run(
            num_neighbours=7,
            maxiter=100,
            tol=1e-6,
            callback=None,
            verbose=True,
            plot_glass_map=False,
         )

2.  **Global exploration**

    - A complete eligible seed is established separately from the untouched
      reference baseline. A reference outside the eligible pool never acquires
      membership through its name or nearby coordinates. Singleton pools are
      fixed assignments and must also be present in every feasible candidate.
    - Each nontrivial pool is sampled with deterministic, seeded farthest-point
      selection in scaled n_d/V_d coordinates. ``global_samples`` defaults to
      ``num_neighbours`` and must be between two and the size of every nontrivial
      pool. Distinct identities remain distinct at coincident coordinates.
    - This deliberately replaces GlassExpert's former internal K-Means sampling.
      The public material-map K-Means helper is unchanged. A run-local RNG and
      canonical exact-key tie breaks preserve discrete reproducibility without
      changing the application's global random state.

    .. figure:: ../_static/glass_map_global_exploration_space.png
      :width: 60%
      :align: center

      Illustrative glass-map distribution from the earlier implementation;
      current GlassExpert sampling uses the seeded farthest-point rule above.

3.  **Local exploration**

    - After the global exploration, a focused search is conducted around the current best-performing glasses.
    - For each glass variable, at most ``num_neighbours`` distinct alternatives
      are trialed around its current assigned key. The metric is
      ``hypot(Δn_d / nd_scale, ΔV_d / vd_scale)``; both scales must be finite and
      positive. They affect search geometry, not merit-function weights.

    .. figure:: ../_static/glass_map_local_exploration_space.png
      :width: 60%
      :align: center

      Example of map of the (nd,vd) glass space and selected candidates for local search.

4.  **Evaluation and refinement**

    - For every candidate glass tested (whether from global or local exploration), a continuous local optimization is performed on all continuous variables in the system (e.g., radii, thicknesses).
    - The merit function value achieved after this local optimization determines the performance of that particular glass choice.
    - Every trial starts from the same complete incumbent state, including all
      material assignments. Actual final merit, continuous bounds and exact
      pool membership are checked independently from the solver's success flag.
      Failed or nonfinite trials cannot replace a validated incumbent.

5.  **Final polish**

    - A final continuous refinement holds the complete material assignment fixed.
      A failed or worse refinement retains the prior validated best. A feasible
      result can still be worse than the reference: ``no_improvement`` records
      this explicitly, without rejecting eligibility or claiming improvement.


The merit function value during a GlassExpert run can look as follows (for 7 neighbours):

   .. figure:: ../_static/glass_expert_error_function.png
      :width: 60%
      :align: center

      Historical illustrative merit trace, not a numeric reference for the
      current sampler or a promise of convergence.

The error function jumps are normal and correspond to the optic being restored to its previous best state, or the evaluation of glasses far from the current glass. 
Also please note that the run duration scales with the number of lenses and the number of glass neighbours.


**Key Code Aspects**

*   **optiland.optimization.optimizer.scipy.glass_expert.GlassExpert**: The main class implementing the algorithm.
*   **Material representation**: ``MaterialCandidate`` stores a qualified key,
    source stamp, owned native bytes, computed n_d/V_d and reference conditions.
    Pool restoration uses the existing material codec, without catalog aliases
    or approximate dispersion fitting. Candidate construction is structural;
    ``prepare_material_candidate`` and run preflight verify native restoration,
    computed coordinates and configured spectral support.
*   **Variable handling**: Typed material variables preserve a reference-only
    baseline without substitution during construction. The search temporarily
    activates only continuous variables, retaining every original descriptor and
    manager. All terminal paths restore that manager/order. Failure and
    cancellation restore starting optical state; normal direct calls leave the
    optic at the complete validated candidate.
*   **Reports**: ``OptimizeResult.glass_expert`` is an immutable typed report with
    baseline/candidate physical values, exact choices and bounded trial history.
    Its merit is sum-squared, not RSS. Eligibility, improvement and convergence
    are distinct. ``result.x`` retains the continuous solver coordinate system.
*   **Cancellation/progress**: Explicit checkpoints can raise during setup,
    sampling, trials and continuous evaluation. A truthy ordinary callback return
    also requests cancellation. Typed progress contains counters and merit, not
    live optics.

**Use Case for Developers**

Developers might interact with or extend the GlassExpert in several ways:

*   **Customizing Search Strategy**: Preserve complete-state rollback, exact
    membership checks, cancellation boundaries and deterministic tie rules when
    changing global or local search. A low finite penalty returned by a solver
    wrapper is not proof of a valid actual objective.
*   **Integrating New Material Properties**: If optimization based on other material properties (beyond n_d and v_d) is desired, the underlying material property functions and distance metrics within GlassExpert would need to be adapted.
*   **Performance Tuning**: The number of local optimizations can be significant. Developers might explore ways to reduce this, perhaps by more sophisticated candidate selection or by using surrogate models if the optimization landscape is complex.

Refer to :doc:`../examples/Tutorial_3e_Glass_Expert_Categorical_Optimization` for
the supported list-of-names workflow, and :doc:`../typed_glass_expert` for exact
owned pools, small-pool rules and terminal outcome interpretation.
