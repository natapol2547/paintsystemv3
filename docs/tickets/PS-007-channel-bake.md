# PS-007 Channel-level bake ("Use Baked")

Epic A. Size M. Milestone M3.

## v2 behaviour

- `bake_channel` / `bake_all_channels` (`operators/bake_operators.py:182,
  293`) bake the channel output into `channel.bake_image` on
  `channel.bake_uv_map`, colour space from `channel.color_space`.
- `use_bake_image` short-circuits the stack to a Texture Image
  (`data.py:1888-1896`); the Layers header shows "Use Baked" (`layers_panels.py:513-525`).
- `bake_channel as_layer=True` inserts the result as a new image layer.
- `delete_bake_image`, `select_all_baked_objects`.
- Menus `MAT_MT_PaintSystemMergeAndExport` (`layers_panels.py:145-168`).

## v3 design

Channel bake is the node cache applied at the Group Output:

- `PaintSystemChannel` gains `bake_image`, `bake_uv_map`, `use_bake_image`,
  `bake_hash`, `bake_stale` (SKIP_SAVE), `bake_vector_space`.
- `build_ir` accepts `bake_target=('channel', name)`; the Group Output
  emitter, when `use_bake_image` and `bake_hash` matches the channel's
  upstream subtree hash, emits an Image Texture instead of walking the
  stack.
- `compiler/bake.py::bake_channel(context, tree, channel, objects, ...)`
  reuses `bake_node_cache` with the channel's output refs. Multi-object:
  bake each selected object that uses a material linked to the tree into
  the same image (same as v2 `select_all_baked_objects` semantics).
- "Bake as Layer" = bake into a fresh managed image, insert an image layer
  at the top, leave `use_bake_image` off.
- Vector channels bake in `bake_vector_space` (PS-006).

## Acceptance

- Test: bake a 2-layer colour channel at 64x64, enable Use Baked, artifact
  has no blend groups for that channel, pixel matches the live render.
- Editing a layer marks `bake_stale` and the header shows the warning icon
  (same as node cache).
- Menu entries present and functional: Bake Active Channel, Bake Active
  Channel as Layer, Bake All Channels, Export Active Channel, Delete
  Active Channel, Export All Channels.
