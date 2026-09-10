# Surface placement

In **Lens Data Editor**, click the tool icon in a surface's **Type** cell and
open **Placement**. The controls are available for standard surfaces, mirrors,
object and image. The **Shape** tab retains the existing surface coefficients.

- **Global** shows position and orientation relative to the global axes.
- **Reference frame** shows values relative to that surface's existing parent
  coordinate frame. Without a parent, it is the global frame.
- **X / Y / Z** are absolute position components in the chosen frame. X/Y are
  also the decenter components; they are not an additional offset. Lengths use
  the prescription's units, normally millimetres.
- **Tilt X / Y / Z** are angles in degrees, applied around fixed frame axes in
  X, Y, Z order. At a 90-degree Y tilt, different Euler triplets can describe
  the same physical orientation; switching frame does not change the design.

Enter one or more values and click **Apply Placement**. **Reload** discards a
draft. Apply prepares the model and its solves in a background calculation;
progress and **Cancel** remain available. An intervening design edit prevents
an old result from overwriting the current design. A successful Apply produces
one undoable change and refreshes the visible layouts. The chosen frame,
expanded row and current selection are retained.

Only the selected surface is repositioned. Move both faces explicitly if you
intend to reposition a complete lens. A surface used as another frame's parent
is replaced for this surface alone, keeping the other surface in place. Existing
pickups and solves still run and can impose their own constraints.

For an object at infinity, enter a finite Z to give it a finite position.
Submissions must have finite coordinates and angles. Unchanged fields keep their
stored precision, and an untouched Apply does not create an undo checkpoint.

The existing Thickness editor rebuilds positions along global Z with the first
optical surface at Z=0. It cannot reposition folded systems or systems with
explicit parent frames; after moving that first surface away from Z=0, use
Placement instead. The GUI rejects such a thickness edit before changing the
model rather than silently resetting an explicit position.
