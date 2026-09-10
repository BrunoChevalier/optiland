"""Shared conic root arithmetic and a fused NumPy execution path.

The same candidate calculation runs on backend arrays and in a scalar Numba
loop. Only execution differs; the root and sheet selection policy is shared.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
from numba import njit
from numba.extending import register_jitable

import optiland.backend as be

if TYPE_CHECKING:
    from collections.abc import Callable

    from optiland.physical_apertures.base import BaseAperture
    from optiland.rays import RealRays


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
) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    """Compute roots, validity, fallback choice, and derivative regularity.

    ``where`` and ``sqrt`` are backend operations for arrays,
    or scalar operations in the compiled loop. Factoring the coefficients
    avoids cancellation of the quadratic term at and near a parabola. The
    constant term is the implicit surface residual at the ray origin.

    A nonzero coefficient must not be discarded solely because it is below
    machine epsilon: ``a``, ``b``, ``c``, and the discriminant have different
    units and scale differently under a geometric rescaling. Zero roots are
    self-crossings; a strictly positive root remains eligible however close.
    """
    k1 = 1 + conic
    a = L * L + M * M + k1 * N * N
    b = 2 * (L * x + M * y + N * (k1 * z - radius))
    c = x * x + y * y + z * (k1 * z - 2 * radius)
    d = b * b - 4 * a * c
    d_ok = d >= 0

    # Preserve every positive radicand. Replacing *inactive* inputs before
    # sqrt keeps its backward pass finite for misses and exact tangencies.
    positive_d = d > 0
    sqrt_d = where(positive_d, sqrt(where(positive_d, d, 1.0)), 0.0)
    q = -0.5 * (b + where(b >= 0, sqrt_d, -sqrt_d))
    # A magnitude comparison excludes infinities and NaNs in one predicate.
    # This also avoids Torch isfinite's separate NaN and infinity masks.
    a_ok = (a != 0) & (abs(a) < math.inf)
    q_ok = (q != 0) & (abs(q) < math.inf)
    t1 = q / where(a_ok, a, 1.0)
    t2 = c / where(q_ok, q, 1.0)
    solvable1 = d_ok & a_ok & (abs(t1) < math.inf)
    solvable2 = d_ok & q_ok & (abs(t2) < math.inf)
    z1 = z + t1 * N
    z2 = z + t2 * N
    valid1 = solvable1 & (t1 > 0) & (1 - k1 * z1 / radius >= 0)
    valid2 = solvable2 & (t2 > 0) & (1 - k1 * z2 / radius >= 0)

    # Choose an index before materializing the selected distance. The XOR
    # form works identically for scalar, NumPy, and Torch booleans.
    nearest1 = valid1 & ((valid1 ^ valid2) | (t1 <= t2))
    vertex1 = where(solvable1, abs(z1), math.inf) <= where(solvable2, abs(z2), math.inf)
    pick1 = where(valid1 | valid2, nearest1, vertex1)
    solvable = solvable1 | solvable2
    regular = solvable & (positive_d | (a == 0))
    return t1, t2, valid1, valid2, pick1, solvable, regular


@register_jitable(inline="always")
def _scalar_where(condition: Any, left: Any, right: Any) -> Any:
    """Scalar selection used by the shared arithmetic in the compiled loop."""
    return left if condition else right


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
) -> np.ndarray:
    """Fuse the no-aperture float64 calculation without fastmath."""
    distance = np.empty_like(x)
    for i in range(x.size):
        t1, t2, _, _, pick1, solvable, _ = _conic_candidates(
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
        )
        distance[i] = (t1 if pick1 else t2) if solvable else math.nan
    return distance


def _can_fuse_numpy(values: tuple, radius: Any, conic: Any) -> bool:
    """Restrict compiled execution to matching float64 arrays and scalars."""
    first = values[0]
    return (
        isinstance(first, np.ndarray)
        and first.ndim == 1
        and all(
            isinstance(value, np.ndarray)
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
    if (
        be.get_backend() == "numpy"
        and aperture is None
        and _can_fuse_numpy(values, radius, conic)
    ):
        return _numpy_conic_distance(*values, float(radius), float(conic))

    if be.get_backend() == "torch":
        import torch

        # Every selection pairs the scalar constant with an existing tensor,
        # so native where preserves that tensor's dtype/device. In particular,
        # it does not construct and copy a new host scalar to CUDA per guard.
        where, sqrt = torch.where, torch.sqrt
    else:
        where, sqrt = be.where, be.sqrt
    t1, t2, valid1, valid2, pick1, solvable, regular = _conic_candidates(
        *values, radius, conic, where, sqrt
    )
    if aperture is not None:
        pref1 = valid1 & aperture.contains(rays.x + t1 * rays.L, rays.y + t1 * rays.M)
        pref2 = valid2 & aperture.contains(rays.x + t2 * rays.L, rays.y + t2 * rays.M)
        preferred1 = pref1 & ((pref1 ^ pref2) | (t1 <= t2))
        pick1 = where(pref1 | pref2, preferred1, pick1)
    distance = where(solvable, where(pick1, t1, t2), math.nan)
    if be.get_backend() == "torch" and distance.requires_grad:
        distance = where(regular, distance, distance.detach())
    return distance
