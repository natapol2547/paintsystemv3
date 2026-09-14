# PS-006 Vector channels: normal and tangent space transforms

Epic A. Size M. Milestone M2.

## v2 behaviour

`Channel.update_node_tree` (`paintsystem/data.py:1735-1802, 1866-1886,
1962-1999`) wraps a VECTOR channel's stack with:

- `vector_type` NORMAL/VECTOR, `input_vector_space`, `vector_space`,
  `output_vector_space`, `bake_vector_space` (OBJECT/WORLD/TANGENT).
- `use_space_transform_input` / `use_space_transform_output`,
  `normalize_input`, `tangent_uv_map`, `disable_output_transform` (set by
  isolate channel).
- Nodes: `ShaderNodeNormalMap`, `ShaderNodeVectorTransform`,
  `.PS Tangent Normal` + `ShaderNodeTangent` for tangent output.
- `default_value` NORMAL/WORLD_POSITION/OBJECT_POSITION injects a
  length-compare fallback (`vector_mix`) so an empty stack outputs the
  geometry normal instead of black.

UI: `draw_channels_settings_panel` (`panels/channels_panels.py:156-211`).

## v3 design

- Add the properties above to `PaintSystemChannel`. Each `update=` marks
  the tree dirty.
- New `compiler/vector.py` with `emit_vector_input(ctx, channel, ref)` and
  `emit_vector_output(ctx, channel, ref)` called by the Group Input and
  Group Output emitters for VECTOR channels. Roles
  `"<channel uuid>:vin_*"` / `":vout_*"`.
- `disable_output_transform` is not stored on the channel. Isolate channel
  (PS-061) passes it through a compile option on the tree
  (`tree.preview_channel`), so the artifact reflects preview state without
  mutating channel data.
- Geometry layers writing normals use `normalize_normal` (PS-027) to match
  `normalize_input`.

## Acceptance

- Test: normal channel with a solid (0.5,0.5,1) layer and tangent output
  produces the same vector as v2 on a UV sphere (compare a baked pixel).
- Test: switching spaces patches nodes in place without artifact churn.
