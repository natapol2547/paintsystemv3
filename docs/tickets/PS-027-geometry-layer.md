# PS-027 Geometry layer

Epic C. Size S. Milestone M2.

## v2 behaviour

`geometry_type` (`data.py:171`): WORLD_NORMAL, WORLD_TRUE_NORMAL, POSITION,
BACKFACING (Geometry node), OBJECT_NORMAL, OBJECT_POSITION (Texture
Coordinate), VECTOR_TRANSFORM (takes the stack colour), AMBIENT_OCCLUSION
(`graph/basic_layers.py:574-614`). `normalize_normal` remaps -1..1 to
0..1. Settings box exposes vector transform spaces, backface, AO samples
etc. from the live nodes (`layers_panels.py:262-288`). Menu "Geometry"
submenu; row icon MESH_DATA. `new_geometry_layer` passes
`normalize_normal` when the channel is VECTOR with `normalize_input`
(`layers_operators.py:335`).

## v3 design

- `PaintSystemGeometryLayerNode` with `geometry_type`, `normalize_normal`,
  and explicit props for the few per-type settings: `transform_from`,
  `transform_to`, `transform_type` (Vector Transform), `ao_samples`,
  `ao_inside`, `ao_only_local`, `ao_distance`. Ambient Occlusion is a
  parameter node (PS-003) because its Normal input and Color input are
  user-editable in v2; the others are plain IR nodes.
- VECTOR_TRANSFORM behaves like an adjustment (`is_clip` forced).

## Acceptance

- World normal on a sphere baked at 64x64 gives the expected hemispheres;
  `normalize_normal` shifts the range.
