"""Material Variable Module

This module contains the MaterialVariable class, which represents a variable for
the material at a specific surface such as a glass.
The variable can be used in optimization problems to optimize the material
at a specific surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from optiland.materials.abbe import AbbeMaterial
from optiland.materials.material_utils import find_closest_glass, get_nd_vd
from optiland.materials.portability import capture_material
from optiland.optimization.material_candidates import MaterialPool, MaterialReference
from optiland.optimization.scaling.identity import IdentityScaler
from optiland.optimization.variable.base import VariableBehavior
from optiland.surfaces.factories.material_factory import MaterialFactory

if TYPE_CHECKING:
    from optiland.materials.base import BaseMaterial
    from optiland.materials.properties import MaterialKey
    from optiland.optimization.scaling.base import Scaler


class MaterialVariable(VariableBehavior):
    """Represent the outgoing material of one retained surface.

    Args:
        optic (Optic): The optic object associated with the variable.
        surface_number (int): Surface whose outgoing medium is varied.
        glass_selection: Supported catalog-name strings or a frozen
            :class:`~optiland.optimization.material_candidates.MaterialPool`.
            Typed construction captures a reference without changing the optic.
            The string API retains its existing Abbe-to-catalog initialization.
        scaler: Typed pools require the identity scaler; defaults to identity.
        **kwargs: Additional keyword arguments.

    """

    def __init__(
        self,
        optic,
        surface_number: int,
        glass_selection: list[str] | MaterialPool,
        scaler: Scaler = None,
        **kwargs,
    ):
        if scaler is None:
            scaler = IdentityScaler()
        super().__init__(optic, surface_number, scaler=scaler, **kwargs)
        self.glass_selection = glass_selection
        self.surface = self.optic.surfaces[self.surface_number]
        self._assigned_key = None
        self.reference = None
        self._reference_material = self.surface.material_post

        if isinstance(glass_selection, MaterialPool):
            if type(scaler) is not IdentityScaler:
                raise ValueError("Exact material pools require identity scaling")
            self.reference = MaterialReference.capture(self._reference_material)
            return

        if isinstance(self.surface.material_post, AbbeMaterial):
            nd_vd = (
                float(self.surface.material_post.index[0].item()),
                float(self.surface.material_post.abbe[0].item()),
            )
            glass = find_closest_glass(nd_vd=nd_vd, catalog=glass_selection)
            new_nd, new_vd = get_nd_vd(glass)
            self.update_value(new_value=glass)
            print(
                f"The material of surface {surface_number:<2} is defined "
                f"by its AbbeMaterial {nd_vd}. GlassExpert converted it "
                f"to real material {glass:<7} ({new_nd:.4f}, {new_vd:.2f}) "
                f"to proceed with optimization."
            )

    def get_value(self) -> str | MaterialKey | MaterialReference:
        """Read the current string value or verified exact typed assignment.

        Returns:
            In string mode, the material name. In typed mode, an assigned
            :class:`~optiland.materials.properties.MaterialKey`, or this
            variable's original :class:`MaterialReference` before assignment.

        Raises:
            ValueError: The live target no longer matches the recorded native
                typed assignment, for example after an external mutation.

        """
        if isinstance(self.glass_selection, MaterialPool):
            expected = (
                self.reference.owned_data
                if self._assigned_key is None
                else self.glass_selection.resolve(self._assigned_key).owned_data
            )
            if capture_material(self.surface.material_post) != expected:
                raise ValueError(
                    "The target material no longer matches its recorded assignment"
                )
            return self.reference if self._assigned_key is None else self._assigned_key
        return self.surface.material_post.name

    def update_value(self, new_value: str | MaterialKey | MaterialReference) -> None:
        """Assign a material through the optic updater.

        Args:
            new_value: A supported string name in string mode. In typed mode,
                an exact pool key restores a fresh native material; this
                variable's own original reference restores its retained material.
                Foreign references and out-of-pool keys fail before assignment.

        """
        if isinstance(self.glass_selection, MaterialPool):
            if new_value is self.reference:
                material_post = self._reference_material
                key = None
            else:
                candidate = self.glass_selection.resolve(new_value)
                material_post = candidate.restore()
                key = candidate.key
            self.optic.updater.set_material(material_post, self.surface_number)
            self._assigned_key = key
            return
        material_post: BaseMaterial = MaterialFactory._configure_post_material(
            new_value
        )
        self.optic.updater.set_material(material_post, self.surface_number)

    def __str__(self) -> str:
        """Return a string representation of the variable.

        Returns:
            str: A string representation of the variable.

        """
        return f"Material, Surface {self.surface_number}"
