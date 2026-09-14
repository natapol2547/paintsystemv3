# PS-065 Projection view operators

Epic G. Size S. Milestone M2.

## v2 behaviour

`set_projection_view` (`layers_operators.py:1049`) stores the current
view as `projection_position` / `projection_rotation` / `projection_fov`
for PROJECT layers; `projection_view_reset` (`:1078`) rebuilds a matrix
from them, premultiplied by the object matrix for OBJECT space, and
writes it into `region_3d.view_matrix` (or moves the scene camera in
CAMERA perspective; refuses other perspectives). Transform panel buttons.

## v3 design

Port onto the `CoordMixin` properties (PS-008). No compile interaction
beyond the property updates.

## Acceptance

- Set projection from a view, orbit away, reset: view matrix equals the
  stored one within 1e-5.
