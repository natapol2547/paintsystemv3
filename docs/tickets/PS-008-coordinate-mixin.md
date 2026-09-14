# PS-008 Coordinate and transform mixin for texture-driven layers

Epic A. Size L. Milestone M1 (AUTO/UV only) and M2 (remaining modes).

## v2 behaviour

`create_coord_graph` (`paintsystem/graph/basic_layers.py:139-217`) and
`_create_mapping_setup` (`:219-243`) build the vector input for IMAGE and
TEXTURE layers (`Layer.uses_coord_type`, `data.py:1432`):

| coord_type | Graph |
|---|---|
| AUTO | UV Map node forced to `PS_UVMap` (PS-009) |
| UV | UV Map node with `uv_map_name` |
| OBJECT / CAMERA / WINDOW / REFLECTION / GENERATED | Texture Coordinate (object = `empty_object`) |
| POSITION | Geometry.Position |
| DECAL | Texture Coordinate Object -> Mapping; optional depth clip (Separate XYZ, Math COMPARE on Z, multiplied into alpha) |
| PROJECT | `.PS Projection` driven by `projection_position/rotation/fov`, `projection_space`; Mask multiplied into alpha |
| PARALLAX | `.PS UV Parallax` (UV Map + Tangent + Normal) or `.PS Object Parallax`; `parallax_space`, `parallax_uv_map_name` |

Always followed by a `ShaderNodeMapping` (user-edited in the UI,
`layers_panels.py:379-437` "Transform" panel), `.PS Correct Aspect` when
`correct_image_aspect` and the image is non-square, and a centring add for
PROJECT/DECAL. `update_coord_type` sets image extension CLIP for
DECAL/PROJECT (`data.py:1155`).

## v3 design

- `nodes/layers/coord_mixin.py::CoordMixin` with properties: `coord_type`,
  `uv_map_name`, `empty_object`, `mapping_location`, `mapping_rotation`,
  `mapping_scale`, `correct_image_aspect`, `use_decal_depth_clip`,
  `decal_depth_clip`, `projection_position`, `projection_rotation`,
  `projection_fov`, `projection_space`, `parallax_space`,
  `parallax_uv_map_name`, `parallax_depth`. All `update=mark_tree_dirty`.
- `emit_coords(ctx, node, image=None) -> (vector_ref, alpha_multiplier_ref | None)`.
  Layer types call it from `emit_source` and multiply the returned alpha
  mask into their alpha. Roles are prefixed `coord_*` so they never collide
  with per-type roles.
- The Mapping node is IR-managed with values from the mapping props. This
  is the deliberate change from v2 (where the Mapping node was edited
  live): the values are now document data and survive recompiles and
  copies. The Transform panel draws the props with the same labels as the
  Mapping node.
- `draw_transform_settings(layout, context)` on the mixin reproduces the
  v2 "Transform" sub-panel including the quick-access row
  (`use_panel_quick_access`).
- Library groups come from PS-002.

## Acceptance

- Each coord type compiles on the factory cube; compiled node count is
  the v2 count or less.
- DECAL depth clip and PROJECT mask affect alpha (bake a pixel outside the
  projection frustum and assert transparency).
- Changing `mapping_location` patches the Mapping node input in place.
