"""Bounded greedy substitution of exact materials with continuous refinement.

The original problem descriptors survive every run. Seeded farthest-point
sampling and local neighbors explore complete eligible assignments; the baseline
is a separate reference. This search does not guarantee a global optimum.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import optiland.backend as be
from optiland.materials import Material
from optiland.materials.portability import capture_material
from optiland.materials.properties import MaterialKey, SourceStamp, describe_material
from optiland.optic.optic_updater import OpticUpdater
from optiland.optimization.glass_expert_records import (
    ContinuousValue,
    GlassExpertCancelled,
    GlassExpertProgress,
    GlassExpertReport,
    GlassExpertState,
    GlassExpertTrial,
    MaterialChoice,
)
from optiland.optimization.material_candidates import (
    MaterialPool,
    MaterialReference,
    prepare_material_candidate,
)
from optiland.optimization.variable.material import MaterialVariable
from optiland.optimization.variable.variable_manager import VariableManager
from scipy.optimize import OptimizeResult, minimize

from ..live_plotter import LiveOptimizationPlotter
from .base import OptimizerGeneric

if TYPE_CHECKING:
    from collections.abc import Callable

    from optiland.optimization.problem import OptimizationProblem


class _NonfiniteMerit(ValueError):
    pass


@dataclass
class _Target:
    variable: object
    variable_id: str
    pool: MaterialPool
    reference: MaterialReference
    original_material: object
    original_value: object
    key: MaterialKey | None = None


@dataclass(frozen=True)
class _State:
    values: tuple[float, ...]
    keys: tuple[MaterialKey, ...]
    merit: float | None = None
    converged: bool = False
    message: str = ""
    nit: int = 0


def _scalar(value, label):
    values = be.to_numpy(value).reshape(-1)
    if (
        values.size != 1
        or values.dtype.kind not in "fiu"
        or not math.isfinite(float(values[0]))
    ):
        raise ValueError(f"{label} must be one finite real scalar")
    return float(values[0])


def _integer(value, label, minimum, maximum=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise ValueError(
            f"{label} must be an integer from {minimum}"
            + (f" through {maximum}" if maximum is not None else " upwards")
        )
    return value


def _distance(first, second, scales):
    distance = math.hypot(
        *((a - b) / s for a, b, s in zip(first, second, scales, strict=True))
    )
    if not math.isfinite(distance):
        raise ValueError("Material coordinate distance overflowed; use suitable scales")
    return distance


def _sample(candidates, count, rng, scales, checkpoint):
    """Select distinct identities even when reference coordinates coincide."""
    remaining = sorted(candidates, key=lambda item: item.key)
    chosen = [remaining.pop(rng.randrange(len(remaining)))]
    nearest = {candidate.key: math.inf for candidate in remaining}
    while remaining and len(chosen) < count:
        checkpoint()
        distances = []
        center = chosen[-1]
        for candidate in remaining:
            checkpoint()
            nearest[candidate.key] = min(
                nearest[candidate.key],
                _distance((candidate.nd, candidate.vd), (center.nd, center.vd), scales),
            )
            distances.append(nearest[candidate.key])
        chosen.append(
            remaining.pop(max(range(len(remaining)), key=distances.__getitem__))
        )
    checkpoint()
    return tuple(chosen)


def _neighbors(candidates, center, scales, checkpoint):
    """Rank exact identities with a cancellation point for every distance."""
    ranked = []
    for candidate in candidates:
        checkpoint()
        ranked.append(
            (
                _distance((candidate.nd, candidate.vd), center, scales),
                candidate.key,
                candidate,
            )
        )
    checkpoint()
    return tuple(item[2] for item in sorted(ranked))


class GlassExpert(OptimizerGeneric):
    """Greedy exact-material search preserving original variable ownership.

    Existing ``glass_selection=list[str]`` callers remain supported. A
    :class:`~optiland.optimization.material_candidates.MaterialPool` supplies
    qualified owned candidates without string lookup. Public callers receive
    their complete candidate in place; GUI callers use detached worker optics.
    """

    def __init__(self, problem: OptimizationProblem):
        self.problem = problem
        self.verbose = True
        self.plot_glass_map = False
        self.last_report: GlassExpertReport | None = None
        self._running = False
        self._x = []

    def vprint(self, *args, **kwargs):
        """Print progress only when the public verbose option is enabled."""
        if self.verbose:
            print(*args, **kwargs)

    def _check(self):
        if self._checkpoint is not None:
            try:
                self._checkpoint()
            except GlassExpertCancelled:
                raise
            except Exception as error:
                raise GlassExpertCancelled(str(error)) from error

    def _update(self):
        self._check()
        self.problem.update_optics()
        for optic, surfaces in self._optics:
            if not any(variable.optic is optic for variable in self._continuous):
                optic.updater.update()
            current = tuple(optic.surfaces)
            if len(current) != len(surfaces) or any(
                before is not after
                for before, after in zip(surfaces, current, strict=True)
            ):
                raise ValueError(
                    "An updater changed surface identity/order during GlassExpert"
                )
        self._check()

    def _merit(self, *, checkpoints=True):
        if checkpoints:
            self._check()
        self._evaluations += 1
        value = self.problem.sum_squared()
        if checkpoints:
            self._check()
        try:
            merit = _scalar(value, "Sum-squared merit")
        except ValueError as error:
            raise _NonfiniteMerit(str(error)) from error
        if merit < 0:
            raise _NonfiniteMerit("Sum-squared merit cannot be negative")
        return merit

    def _physical(self):
        return tuple(
            ContinuousValue(
                identifier, _scalar(variable.variable.get_value(), "Continuous value")
            )
            for identifier, variable in self._continuous_entries
        )

    def _state_report(self, merit, *, baseline=False):
        choices = tuple(
            MaterialChoice(
                target.variable_id,
                None if baseline else target.key,
                hashlib.sha256(
                    capture_material(target.variable.variable.surface.material_post)
                ).hexdigest(),
            )
            for target in self._targets
        )
        return GlassExpertState(self._physical(), choices, merit, not baseline)

    def _validate_candidate(self):
        for variable in self._continuous:
            value = _scalar(variable.value, "Continuous solver value")
            lo, hi = variable.bounds
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                raise ValueError(
                    "A continuous result lies outside its configured bounds"
                )
            _scalar(variable.variable.get_value(), "Continuous physical value")
        for target in self._targets:
            candidate = target.pool.resolve(target.key)
            material = target.variable.variable.surface.material_post
            if capture_material(
                material
            ) != candidate.owned_data or material.to_dict() != json.loads(
                candidate.owned_data
            ):
                raise ValueError(
                    f"Material assignment for {target.variable_id} "
                    "no longer matches its pool"
                )

    def _assign(self, target, key):
        self._check()
        candidate = target.pool.resolve(key)
        behavior = target.variable.variable
        if isinstance(behavior.glass_selection, MaterialPool):
            target.variable.update(key)
        else:
            target.variable.optic.updater.set_material(
                candidate.restore(), behavior.surface_number
            )
        target.key = key
        self._check()

    def _restore(self, state):
        for target, key in zip(self._targets, state.keys, strict=True):
            self._assign(target, key)
        for variable, value in zip(self._continuous, state.values, strict=True):
            variable.update(be.array(value))
        self._update()

    def _restore_original(self):
        # Rollback deliberately does not call a cancelled checkpoint.
        for target in self._targets:
            behavior = target.variable.variable
            target.variable.optic.updater.set_material(
                target.original_material, behavior.surface_number
            )
            if isinstance(behavior.glass_selection, MaterialPool):
                behavior._assigned_key = (
                    target.original_value
                    if isinstance(target.original_value, MaterialKey)
                    else None
                )
            target.key = None
        for variable, value in zip(self._continuous, self._initial_values, strict=True):
            variable.update(be.array(value))
        for optic, _surfaces in self._optics:
            optic.updater.update()

    def _notify(self):
        self._check()
        if self._progress is not None:
            self._progress(
                GlassExpertProgress(
                    self._phase,
                    self._total_trials,
                    self._evaluations,
                    self._baseline.merit,
                    self._best.merit if self._best is not None else None,
                    self._current_target.variable_id
                    if self._current_target is not None
                    else None,
                    self._current_key,
                )
            )
        self._check()

    def _fun(self, x):
        self._check()
        try:
            for variable, value in zip(self._continuous, x, strict=True):
                variable.update(be.array(value))
            self._update()
            value = self._merit()
        except (ValueError, ArithmeticError):
            # Invalid intermediate steps can be penalized. Final native state
            # still requires an independent finite evaluation, never this penalty.
            value = 1e100
        self._notify()
        return value

    def _callback(self, *args, **kwargs):
        self._check()
        if self._user_callback is not None and self._user_callback(*args, **kwargs):
            raise GlassExpertCancelled(
                "The optimization callback requested cancellation"
            )
        self._check()
        if self._plotter is not None:
            self._plotter.update()
        self._notify()

    def _optimize_continuous(self):
        result = minimize(
            self._fun,
            [
                _scalar(variable.value, "Continuous value")
                for variable in self._continuous
            ],
            bounds=tuple(variable.bounds for variable in self._continuous),
            options={"maxiter": self._maxiter},
            tol=self._tol,
            callback=self._callback,
        )
        self._check()
        for variable, value in zip(self._continuous, result.x, strict=True):
            variable.update(be.array(value))
        self._update()
        return result

    def _trial(self, phase, base, target=None, candidate=None):
        self._phase, self._current_target = phase, target
        self._current_key = candidate.key if candidate is not None else None
        self._check()
        self._total_trials += 1
        start = self._evaluations
        status, merit, converged, improved, message = "failed", None, False, False, ""
        try:
            self._restore(base)
            if target is not None:
                self._assign(target, candidate.key)
                self._update()
            self._notify()
            result = self._optimize_continuous()
            self._check()
            self._validate_candidate()
            merit = self._merit()
            self._validate_candidate()
            self._check()
            status, converged, message = (
                "evaluated",
                bool(result.success),
                str(result.message),
            )
            if self._best is None or merit < self._best.merit:
                self._best = _State(
                    tuple(
                        _scalar(variable.value, "Continuous value")
                        for variable in self._continuous
                    ),
                    tuple(item.key for item in self._targets),
                    merit,
                    converged,
                    message,
                    int(result.nit),
                )
                improved = True
        except GlassExpertCancelled:
            status, message = "cancelled", "GlassExpert was cancelled"
            raise
        except _NonfiniteMerit as error:
            status, message = "nonfinite", str(error)
        except (ValueError, ArithmeticError) as error:
            message = f"{type(error).__name__}: {error}"
        finally:
            record = GlassExpertTrial(
                self._total_trials,
                phase,
                target.variable_id if target is not None else None,
                self._current_key,
                status,
                merit,
                self._evaluations - start,
                converged,
                improved,
                message[:2048],
            )
            if len(self._trials) < self._report_limit:
                self._trials.append(record)
        self._check()
        self._notify()

    def _legacy_pool(self, behavior):
        selection = behavior.glass_selection
        if (
            not isinstance(selection, list | tuple)
            or not selection
            or any(not isinstance(name, str) for name in selection)
        ):
            raise ValueError(
                "Glass selection requires a nonempty list of names or a MaterialPool"
            )
        records = []
        for name in selection:
            self._check()
            material = Material(name)
            summary = describe_material(
                material, MaterialKey("legacy:string", "", name)
            )
            records.append(replace(summary, owned_data=capture_material(material)))
        generation = hashlib.sha256(
            b"".join(record.owned_data for record in records)
        ).hexdigest()
        stamp = SourceStamp("legacy:string", generation)
        return MaterialPool(
            tuple(
                prepare_material_candidate(
                    record, stamp, wavelengths_um=(), checkpoint=self._check
                )
                for record in records
            )
        )

    def _prepare(self, variables, variable_ids, global_samples):
        identifiers = (
            tuple(variable_ids)
            if variable_ids is not None
            else tuple(f"variable:{index}" for index in range(len(variables)))
        )
        if (
            len(identifiers) != len(variables)
            or any(
                not isinstance(identifier, str) or not identifier
                for identifier in identifiers
            )
            or len(set(identifiers)) != len(identifiers)
        ):
            raise ValueError(
                "Variable IDs must be distinct and match the original descriptor order"
            )
        entries = tuple(zip(identifiers, variables, strict=True))
        self._continuous_entries = tuple(
            (identifier, variable)
            for identifier, variable in entries
            if not isinstance(variable.variable, MaterialVariable)
        )
        self._continuous = tuple(variable for _, variable in self._continuous_entries)
        if not self._continuous:
            raise ValueError("GlassExpert requires at least one continuous variable")
        self._initial_values = tuple(
            _scalar(variable.value, "Initial continuous value")
            for variable in self._continuous
        )
        for variable in self._continuous:
            _scalar(variable.variable.get_value(), "Initial physical value")
            lo, hi = variable.bounds
            if any(
                value is not None and not math.isfinite(value) for value in (lo, hi)
            ) or (lo is not None and hi is not None and lo > hi):
                raise ValueError("Continuous bounds must be finite and ordered")
        optics = []
        for variable in variables:
            if not any(optic is variable.optic for optic, _ in optics):
                updater = variable.optic.updater
                if type(updater) is not OpticUpdater or any(
                    callable(value) for value in vars(updater).values()
                ):
                    raise ValueError("GlassExpert requires the built-in optic updater")
                optics.append((variable.optic, tuple(variable.optic.surfaces)))
        self._optics = tuple(optics)
        exact_payloads, source_stamps, validated = {}, {}, set()
        targets = set()
        for identifier, variable in entries:
            behavior = variable.variable
            if not isinstance(behavior, MaterialVariable):
                continue
            self._check()
            row = behavior.surface_number
            if not 0 <= row < len(variable.optic.surfaces) - 1 or (
                variable.optic.surfaces[row] is not behavior.surface
                or getattr(behavior.surface.interaction_model, "is_reflective", False)
            ):
                raise ValueError(
                    "A material target must retain an editable outgoing medium"
                )
            target_id = (id(variable.optic), row)
            if target_id in targets or any(
                continuous.type == "index"
                and continuous.optic is variable.optic
                and continuous.surface_number == row
                for continuous in self._continuous
            ):
                raise ValueError(
                    "Material targets must be distinct and cannot overlap "
                    "index variables"
                )
            targets.add(target_id)
            pool = (
                behavior.glass_selection
                if isinstance(behavior.glass_selection, MaterialPool)
                else self._legacy_pool(behavior)
            )
            if len(pool.candidates) > 1 and global_samples > len(pool.candidates):
                raise ValueError(
                    f"Global sample count exceeds pool size for {identifier}"
                )
            wavelengths = tuple(
                float(w.value) for w in variable.optic.wavelengths.wavelengths
            )
            if not wavelengths:
                raise ValueError("Configure wavelengths before a material search")
            for candidate in pool.candidates:
                self._check()
                previous = exact_payloads.setdefault(
                    candidate.key, candidate.owned_data
                )
                if previous != candidate.owned_data:
                    raise ValueError(
                        "The same exact material key has conflicting payloads"
                    )
                if isinstance(behavior.glass_selection, MaterialPool):
                    previous_stamp = source_stamps.setdefault(
                        candidate.stamp.source_id, candidate.stamp
                    )
                    if previous_stamp != candidate.stamp:
                        raise ValueError(
                            "Material pools contain conflicting source generations"
                        )
                cache_key = (candidate, wavelengths)
                if cache_key not in validated:
                    self._warnings.extend(
                        candidate.validate(wavelengths, checkpoint=self._check)
                    )
                    validated.add(cache_key)
            self._targets.append(
                _Target(
                    variable,
                    identifier,
                    pool,
                    MaterialReference.capture(behavior.surface.material_post),
                    behavior.surface.material_post,
                    variable.value,
                )
            )
        if not self._targets or all(
            len(target.pool.candidates) == 1 for target in self._targets
        ):
            raise ValueError(
                "GlassExpert needs a variable material pool; "
                "use ordinary optimization for fixed assignments"
            )

    def _finish_report(self, outcome, candidate=None):
        improved = (
            candidate is not None
            and self._baseline is not None
            and candidate.merit < self._baseline.merit
        )
        percent = None
        if (
            candidate is not None
            and self._baseline is not None
            and self._baseline.merit > 0
        ):
            value = (
                (self._baseline.merit - candidate.merit) / self._baseline.merit * 100
            )
            if math.isfinite(value):
                percent = value
            else:
                self._warnings.append(
                    "Percentage improvement is not representable; "
                    "compare the finite merits directly."
                )
        self.last_report = GlassExpertReport(
            outcome,
            self._baseline,
            candidate,
            improved,
            percent,
            tuple(self._trials),
            self._total_trials,
            self._evaluations,
            self._total_trials - len(self._trials),
            tuple(dict.fromkeys(self._warnings)),
        )
        return self.last_report

    def _plot_pool(self, target, candidates):
        import matplotlib.pyplot as plt

        figure, axes = plt.subplots()
        axes.scatter(
            [c.vd for c in target.pool.candidates],
            [c.nd for c in target.pool.candidates],
            marker=".",
        )
        axes.scatter(
            [c.vd for c in candidates],
            [c.nd for c in candidates],
            facecolors="none",
            edgecolors="black",
            marker="s",
        )
        axes.set(
            xlabel="V_d", ylabel="n_d", title=f"Global candidates: {target.variable_id}"
        )
        axes.invert_xaxis()
        figure.show()

    def run(
        self,
        num_neighbours: int = 7,
        maxiter: int = 1000,
        tol: float = 1e-3,
        disp: bool = True,
        plot: bool = False,
        callback=None,
        verbose: bool = True,
        plot_glass_map=False,
        *,
        global_samples: int | None = None,
        seed: int = 1234,
        nd_scale: float = 1.0,
        vd_scale: float = 1.0,
        variable_ids: tuple[str, ...] | None = None,
        checkpoint: Callable[[], None] | None = None,
        progress: Callable[[GlassExpertProgress], None] | None = None,
        report_limit: int = 1000,
    ) -> OptimizeResult:
        """Search exact pools and return continuous solver values plus a report.

        ``global_samples`` defaults to ``num_neighbours`` and must be at least
        two and no larger than every nontrivial pool. Singletons are fixed; at
        least one nontrivial pool and one continuous variable are required. The
        local neighbor count excludes the currently assigned key. Positive
        ``nd_scale``/``vd_scale`` define the reference-coordinate distance.

        ``seed`` affects only a run-local diversity sampler. ``variable_ids``
        names original descriptors, in original order. ``progress`` receives
        immutable counters; ``checkpoint`` raises to cancel. A truthy ordinary
        callback return also cancels. Cancellation restores the starting state
        and raises :class:`GlassExpertCancelled` with its bounded report.

        The result's ``glass_expert`` report distinguishes eligibility, solver
        convergence and improvement. Merit is sum_squared. A complete eligible
        result may be worse than the reference (``no_improvement``). Zero
        baseline returns ``already_reached`` without a fabricated candidate.
        Both plotting flags default off for worker use; no GUI is required.
        Custom optic updater types or instance-patched updater methods are not
        supported: their mutations cannot be guaranteed to roll back safely.
        """
        if self._running:
            raise RuntimeError("This GlassExpert instance is already running")
        _integer(num_neighbours, "Neighbor count", 1)
        global_samples = num_neighbours if global_samples is None else global_samples
        _integer(global_samples, "Global sample count", 2)
        _integer(maxiter, "Maximum iterations", 1)
        _integer(seed, "Seed", 0, 2**64 - 1)
        _integer(report_limit, "Report limit", 1, 10000)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
            for value in (tol, nd_scale, vd_scale)
        ):
            raise ValueError(
                "Tolerance and material distance scales must be finite and positive"
            )
        self.verbose, self.plot_glass_map = verbose, plot_glass_map
        self._checkpoint, self._progress, self._user_callback = (
            checkpoint,
            progress,
            callback,
        )
        self._report_limit, self._maxiter, self._tol = report_limit, maxiter, tol
        self._phase, self._current_target, self._current_key = "seed", None, None
        self._evaluations, self._total_trials = 0, 0
        self._trials, self._warnings, self._targets = [], [], []
        self._optics, self._continuous, self._initial_values = (), (), ()
        self._best, self._baseline, self._plotter = None, None, None
        self.last_report = None
        self._running = True
        original_manager = self.problem.variables
        variables = tuple(original_manager)
        mutated, keep = False, False
        try:
            self._check()
            self._prepare(variables, variable_ids, global_samples)
            baseline_merit = self._merit()
            self._baseline = self._state_report(baseline_merit, baseline=True)
            self.problem.initial_value = baseline_merit
            if baseline_merit == 0:
                report = self._finish_report("already_reached")
                return OptimizeResult(
                    x=be.to_numpy(self._initial_values),
                    fun=0.0,
                    success=True,
                    message="Baseline objective already reached",
                    nfev=self._evaluations,
                    nit=0,
                    glass_expert=report,
                )
            active = VariableManager()
            active.variables = list(self._continuous)
            self.problem.variables = active
            if plot:
                self._plotter = LiveOptimizationPlotter(self)
                self._plotter.initialize()
            scales, rng = (nd_scale, vd_scale), random.Random(seed)
            keys = []
            for target in self._targets:
                self._check()
                candidates = sorted(target.pool.candidates, key=lambda item: item.key)
                if (
                    target.reference.nd.status
                    == target.reference.vd.status
                    == "available"
                ):
                    center = (target.reference.nd.value, target.reference.vd.value)
                    candidate = _neighbors(candidates, center, scales, self._check)[0]
                else:
                    candidate = candidates[0]
                    self._warnings.append(
                        f"Baseline coordinates unavailable for {target.variable_id}; "
                        "using a deterministic eligible seed."
                    )
                keys.append(candidate.key)
            seed_state = _State(self._initial_values, tuple(keys))
            mutated = True
            self._trial("seed", seed_state)
            for phase in ("global", "local"):
                self._phase = phase
                self._check()
                for target in self._targets:
                    if len(target.pool.candidates) == 1:
                        continue
                    self._check()
                    base = self._best or seed_state
                    if phase == "global":
                        candidates = _sample(
                            target.pool.candidates,
                            global_samples,
                            rng,
                            scales,
                            self._check,
                        )
                        if plot_glass_map:
                            self._plot_pool(target, candidates)
                    else:
                        center_candidate = target.pool.resolve(
                            base.keys[self._targets.index(target)]
                        )
                        center = (center_candidate.nd, center_candidate.vd)
                        candidates = _neighbors(
                            (
                                item
                                for item in target.pool.candidates
                                if item.key != center_candidate.key
                            ),
                            center,
                            scales,
                            self._check,
                        )[:num_neighbours]
                    for candidate in candidates:
                        self._trial(phase, base, target, candidate)
                    self._check()
            self._phase = "final"
            self._check()
            if self._best is not None:
                self._trial("final", self._best)
                self._restore(self._best)
                self._validate_candidate()
                merit = self._merit()
                self._validate_candidate()
                if merit != self._best.merit:
                    raise ValueError(
                        "The restored best candidate does not reproduce "
                        "its evaluated merit"
                    )
                candidate_report = self._state_report(merit)
                self._check()
                report = self._finish_report(
                    "completed" if merit < baseline_merit else "no_improvement",
                    candidate_report,
                )
                result = OptimizeResult(
                    x=be.to_numpy(self._best.values),
                    fun=merit,
                    success=self._best.converged,
                    message=self._best.message,
                    nit=self._best.nit,
                    nfev=self._evaluations,
                    glass_expert=report,
                )
                if disp:
                    self.vprint(
                        f"GlassExpert {report.outcome}: {baseline_merit:g} → {merit:g}"
                    )
                if self._plotter is not None:
                    self._plotter.update()
                    self._plotter.finalize()
                self._check()
                # User checkpoints, progress and plotting are callbacks. None
                # can run after this final proof and invalidate the returned
                # prescription while leaving an earlier report authoritative.
                self._validate_candidate()
                final_merit = self._merit(checkpoints=False)
                self._validate_candidate()
                if (
                    final_merit != merit
                    or self._physical() != candidate_report.continuous
                ):
                    raise ValueError("The candidate changed during final notification")
                result.nfev = self._evaluations
                result.glass_expert = self._finish_report(
                    report.outcome, candidate_report
                )
                keep = True
                return result
            report = self._finish_report("no_candidate")
            return OptimizeResult(
                x=be.to_numpy(self._initial_values),
                fun=baseline_merit,
                success=False,
                message="No valid candidate found in the bounded search",
                nfev=self._evaluations,
                nit=0,
                glass_expert=report,
            )
        except GlassExpertCancelled as error:
            error.report = self._finish_report("cancelled")
            raise
        except Exception:
            self._finish_report("failed")
            raise
        finally:
            self.problem.variables = original_manager
            try:
                if mutated and not keep:
                    self._restore_original()
            finally:
                self._running = False
