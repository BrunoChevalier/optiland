"""Shared conic root arithmetic and a fused NumPy execution path.

The same candidate calculation runs on backend arrays and in a scalar Numba
loop. Only execution differs; the root and sheet selection policy is shared.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
from numba import njit
from numba.extending import register_jitable

import optiland.backend as be
from optiland.utils import machine_eps

_FLOAT64_EPS = np.finfo(np.float64).eps

if TYPE_CHECKING:
    from collections.abc import Callable

    from optiland.physical_apertures.base import BaseAperture
    from optiland.rays import RealRays


class _Candidates(NamedTuple):
    """Root values and masks shared by scalar and array execution."""

    first: Any
    second: Any
    first_valid: Any
    second_valid: Any
    pick_second: Any
    solvable: Any
    regular: Any


@register_jitable(inline="always")
def _conic_candidates(
    x: Any,
    y: Any,
    z: Any,
    L: Any,
    M: Any,
    N: Any,
    radius: Any,
    conic: Any,
    where: Callable,
    sqrt: Callable,
    copysign: Callable,
    epsilon: Callable,
) -> _Candidates:
    """Compute root values, admissibility, and selection masks.

    ``where``, ``sqrt``, ``copysign``, and ``epsilon`` operate on backend arrays,
    or scalars in the compiled loop. Factoring the coefficients
    avoids cancellation of the quadratic term at and near a parabola. The
    constant term is the implicit surface residual at the ray origin.

    A nonzero coefficient must not be discarded solely because it is below
    machine epsilon: ``a``, ``b``, ``c``, and the discriminant have different
    units and scale differently under a geometric rescaling. Zero roots are
    self-crossings. The smaller root is also excluded when both its residual
    and its displacement are within roundoff at the origin.
    """
    k1 = 1 + conic
    kz = k1 * z
    transverse = x * x + y * y
    transverse_direction = L * L + M * M
    N2 = N * N
    a = transverse_direction + k1 * N2
    b = 2 * (L * x + M * y + N * (kz - radius))
    c = transverse + z * (kz - 2 * radius)
    # Sag evaluation and propagation can leave a rounded point just off the
    # surface. Bound that residual in squared-length units, using the terms
    # before cancellation. Unlike a coordinate-based distance floor, this
    # scales with the equation and retains resolvable nearby intersections.
    residual_scale = transverse + abs(z) * (abs(kz) + 2 * abs(radius))
    roundoff = 4 * epsilon(c)
    resolved_c = abs(c) > roundoff * residual_scale
    d = b * b - 4 * a * c
    d_ok = d >= 0

    # Preserve every positive radicand. Replacing *inactive* inputs before
    # sqrt keeps its backward pass finite for misses and exact tangencies.
    positive_d = d > 0
    sqrt_d = where(positive_d, sqrt(where(positive_d, d, 1.0)), 0.0)
    q = -0.5 * (b + copysign(sqrt_d, b))
    # A magnitude comparison excludes infinities and NaNs in one predicate.
    # This also avoids Torch isfinite's separate NaN and infinity masks.
    a_ok = (a != 0) & (abs(a) < math.inf)
    q_ok = (q != 0) & (abs(q) < math.inf)
    t1 = q / where(a_ok, a, 1.0)
    t2 = c / where(q_ok, q, 1.0)
    # A small implicit residual alone does not imply a self-hit: a nearly
    # tangent ray can travel a resolved distance from such an origin. Require
    # the smaller root's displacement to be below position roundoff as well.
    resolved_step = t2 * t2 * (transverse_direction + N2) > (
        roundoff * roundoff * (transverse + z * z)
    )
    solvable1 = d_ok & a_ok & (abs(t1) < math.inf)
    solvable2 = d_ok & q_ok & (abs(t2) < math.inf)
    z1 = z + t1 * N
    z2 = z + t2 * N
    valid1 = solvable1 & (t1 > 0) & (1 - k1 * z1 / radius >= 0)
    # The stable q formula makes c/q the root with smaller magnitude. Only
    # that candidate is a possible rounded self-hit; retain the other root
    # and its full derivatives through the original, unmodified coefficients.
    valid2 = (
        solvable2
        & (resolved_c | resolved_step)
        & (t2 > 0)
        & (1 - k1 * z2 / radius >= 0)
    )

    # c/q is the smaller-magnitude root, so whenever both roots are forward,
    # it is the nearer one. Select its index directly, with vertex fallback.
    vertex2 = where(solvable2, abs(z2), math.inf) < where(solvable1, abs(z1), math.inf)
    pick2 = valid2 | where(valid1, False, vertex2)
    solvable = solvable1 | solvable2
    regular = solvable & (positive_d | (a == 0))
    return _Candidates(t1, t2, valid1, valid2, pick2, solvable, regular)


@register_jitable(inline="always")
def _scalar_where(condition: Any, left: Any, right: Any) -> Any:
    """Scalar selection used by the shared arithmetic in the compiled loop."""
    return left if condition else right


@register_jitable(inline="always")
def _float64_eps(value: Any) -> float:
    """Precision of the explicitly restricted compiled execution path."""
    return _FLOAT64_EPS


@njit(cache=True, error_model="numpy")
def _numpy_conic_distance(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    L: np.ndarray,
    M: np.ndarray,
    N: np.ndarray,
    radius: float,
    conic: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Fuse the no-aperture float64 calculation without fastmath."""
    distance = np.empty_like(x)
    regular = np.empty(x.shape, dtype=np.bool_)
    for i in range(x.size):
        roots = _conic_candidates(
            x[i],
            y[i],
            z[i],
            L[i],
            M[i],
            N[i],
            radius,
            conic,
            _scalar_where,
            math.sqrt,
            math.copysign,
            _float64_eps,
        )
        distance[i] = (
            (roots.second if roots.pick_second else roots.first)
            if roots.solvable
            else math.nan
        )
        regular[i] = roots.regular
    return distance, regular


@njit(cache=True, error_model="numpy")
def _numpy_conic_candidates(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    L: np.ndarray,
    M: np.ndarray,
    N: np.ndarray,
    radius: float,
    conic: float,
) -> _Candidates:
    """Expose both roots when an arbitrary aperture must choose between them.

    The no-aperture loop only allocates distance and regularity arrays; this
    variant retains the additional candidate arrays needed by aperture code.
    Both loops use the same scalar arithmetic and selection policy.
    """
    first, second = np.empty_like(x), np.empty_like(x)
    valid1 = np.empty(x.shape, dtype=np.bool_)
    valid2 = np.empty(x.shape, dtype=np.bool_)
    pick2 = np.empty(x.shape, dtype=np.bool_)
    solvable = np.empty(x.shape, dtype=np.bool_)
    regular = np.empty(x.shape, dtype=np.bool_)
    for i in range(x.size):
        roots = _conic_candidates(
            x[i],
            y[i],
            z[i],
            L[i],
            M[i],
            N[i],
            radius,
            conic,
            _scalar_where,
            math.sqrt,
            math.copysign,
            _float64_eps,
        )
        first[i], second[i] = roots.first, roots.second
        valid1[i], valid2[i] = roots.first_valid, roots.second_valid
        pick2[i], solvable[i], regular[i] = (
            roots.pick_second,
            roots.solvable,
            roots.regular,
        )
    return _Candidates(first, second, valid1, valid2, pick2, solvable, regular)


def _select_distance(
    roots: _Candidates, values: tuple, aperture: BaseAperture | None, where: Callable
) -> Any:
    """Apply aperture preference without changing geometric admissibility."""
    pick2 = roots.pick_second
    if aperture is not None:
        x, y, _, L, M, _ = values
        pref1 = roots.first_valid & aperture.contains(
            x + roots.first * L, y + roots.first * M
        )
        pref2 = roots.second_valid & aperture.contains(
            x + roots.second * L, y + roots.second * M
        )
        pick2 = pref2 | where(pref1, False, pick2)
    return where(roots.solvable, where(pick2, roots.second, roots.first), math.nan)


def _can_fuse_numpy(values: tuple, radius: Any, conic: Any) -> bool:
    """Restrict compiled execution to matching float64 arrays and scalars."""
    first = values[0]
    return (
        type(first) is np.ndarray
        and first.ndim == 1
        and all(
            type(value) is np.ndarray
            and value.dtype == np.float64
            and value.shape == first.shape
            for value in values
        )
        and np.ndim(radius) == 0
        and np.ndim(conic) == 0
        and np.asarray(radius).dtype == np.float64
        and np.asarray(conic).dtype == np.float64
    )


def conic_distance(
    rays: RealRays,
    radius: Any,
    conic: Any,
    aperture: BaseAperture | None = None,
) -> Any:
    """Select the physical root of a finite-radius conic.

    Args:
        rays: Ray coordinates and directions in the surface's local frame.
        radius: Finite radius, scalar or broadcastable backend array.
        conic: Conic constant, scalar or broadcastable backend array.
        aperture: Optional aperture used to prefer the physical surface patch.

    Returns:
        Propagation distance, with a signed vertex fallback when no forward
        root is admissible, or NaN if the equation has no finite solution.
        Exact double roots retain their forward value but contribute zero
        Torch gradient: their intersection derivative is singular.
    """
    values = (rays.x, rays.y, rays.z, rays.L, rays.M, rays.N)
    if be.get_backend() == "numpy" and _can_fuse_numpy(values, radius, conic):
        if aperture is None:
            return _numpy_conic_distance(*values, float(radius), float(conic))[0]
        roots = _numpy_conic_candidates(*values, float(radius), float(conic))
        return _select_distance(roots, values, aperture, be.where)

    if be.get_backend() == "torch":
        import torch

        from optiland.geometries._conic_torch import can_fuse_cpu, conic_distance_cpu

        if can_fuse_cpu(values, radius, conic):
            return conic_distance_cpu(*values, radius, conic, aperture=aperture)

        # Every selection pairs the scalar constant with an existing tensor,
        # so native where preserves that tensor's dtype/device. In particular,
        # it does not construct and copy a new host scalar to CUDA per guard.
        where, sqrt, copysign = torch.where, torch.sqrt, torch.copysign
    else:
        where, sqrt, copysign = be.where, be.sqrt, np.copysign
    roots = _conic_candidates(
        *values, radius, conic, where, sqrt, copysign, machine_eps
    )
    distance = _select_distance(roots, values, aperture, where)
    if be.get_backend() == "torch" and distance.requires_grad:
        distance = where(roots.regular, distance, distance.detach())
    return distance
