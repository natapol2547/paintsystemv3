# PS-018 Merge up/down, duplicate, convert to image layer, transfer UV

Epic B. Size M. Milestone M2.

## v2 behaviour

- `merge_down` / `merge_up` (`operators/bake_operators.py:692, 814`): both
  layers non-folder, same parent, enabled, target not
  `modifies_color_data`; bakes the pair with blend forced to MIX into a
  new image, applies it to the surviving layer, deletes the other.
- `convert_to_image_layer` (`:609`): bakes a non-image layer's output into
  an image layer replacing it. Menu entry "Convert to Image Layer" for
  non IMAGE/ADJUSTMENT layers.
- `transfer_image_layer_uv` (`:542`): re-bakes an image layer to a
  different UV map (button in the Transform panel).
- No duplicate operator existed; users used copy/paste.

## v3 design

All of these are `bake_node_cache` with a different target:

- `compiler/bake.py::bake_refs_to_image(context, tree, refs, objects, image,
  uv_map)` generalises the current function to bake any (color, alpha)
  refs. `bake_node_cache` becomes a thin wrapper.
- Merge down: build an IR with `bake_target=('merge', upper, lower)` whose
  output is `lower` source composited with `upper` (both MIX, opacity 1,
  masks applied). Bake, assign the image to `lower` (converted to an image
  node if it is not one), remove `upper`. Merge up is the same with roles
  swapped. Poll rules as v2.
- Convert to image layer: bake the node's `emit_source` refs, replace the
  node with a `PaintSystemImageLayerNode` carrying the same name, blend,
  opacity, clip, masks and position.
- Transfer UV: bake the image node's own image through the new UV map
  into a new image, then switch `uv_map_name` and `image`.
- Duplicate layer: `duplicate_subtree` (PS-017) + `insert_above(node)`.
  Add "Duplicate Layer" to `MAT_MT_LayerMenu` (new in v3). Expect the
  duplicate to arrive unbaked: `PaintSystemLayerNode.copy` clears the
  cache, because the two layers would otherwise share one image and
  baking either would overwrite the other's pixels. A filter layer copies
  its derived image instead of dropping it, since that image is the
  layer's content rather than a re-derivable artifact (PS-057).

## Acceptance

- Merge down of a blue solid over a red image yields an image layer with
  the blended pixels and one fewer node.
- Convert a texture layer at 256x256 and compare the centre pixel to the
  live render.
- All four operators are single undo steps.
