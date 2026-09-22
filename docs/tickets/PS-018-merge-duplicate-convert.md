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

### v2 UI

Entry points:

- `MAT_MT_LayerMenu`, the DOWNARROW_HLT button in the layer list
  sidebar (`panels/common.py:495-496`; full menu in PS-034). "Convert
  to Image Layer" (custom `image` icon) comes first, for layer types
  other than IMAGE and ADJUSTMENT. "Merge Up" (TRIA_UP_BAR) and "Merge
  Down" (TRIA_DOWN_BAR) come last, after a separator
  (`panels/layers_panels.py:724-785`).
- Transfer Image Layer UV: an icon-only UV_DATA button in the aligned
  header row of the closed "Transform" sub-panel of Layer Settings,
  after the `coord_type` dropdown, for IMAGE layers with an image
  (`panels/layers_panels.py:379-385`). Layer Settings is disabled
  while the layer is locked, so the button is too (`:174`).
- None of these can be reached while the channel uses its baked image:
  the Layers panel then returns before the layer list and sidebar
  (`panels/layers_panels.py:633-641`).

All four operators subclass `BakeOperator` and bake through
`Channel.bake` (PS-007). Unlike the channel bakes they pass
`use_group_tree=False`, so the source is a temporary group node running
the channel tree. They bake only the active material, on the selected
and active PS objects that hold it, even though invoke refreshes the
multi-material list (`operators/bake_operators.py:18-31, 591, 662,
792, 913`; `paintsystem/data.py:1675-1677, 2211-2218`). Folder layers
are never disabled by Merge or Convert, so the bake reads the whole
channel output through any enclosing folder (`:654, 775, 896`).

Merge Down / Merge Up (`operators/bake_operators.py:692-931`):

- bl_label "Merge Down" (description "Merge the down layers") and
  "Merge Up" ("Merge the layer into the one above"), REGISTER/UNDO.
  Poll: a neighbour exists in flattened order, neither layer is a
  FOLDER, both share a parent, both are enabled, and the lower of the
  pair does not modify colour data (ATTRIBUTE, GRADIENT_MAP, or a
  blend other than MIX) (`:707-723, 829-845`;
  `paintsystem/data.py:1604-1606`; PS-034).
- Invoke (`:725-741, 847-862`) does not call `BakeOperator.invoke`. It
  runs `get_coord_type` (UV preset, Edit to Object mode, PS-007) and
  refreshes the material list. Then, for the neighbour that survives
  (below for Merge Down, above for Merge Up):
  - IMAGE or TEXTURE with `coord_type` AUTO: `PS_UVMap`. Other coord
    types keep the preset (the first UV map, or the group's UV map
    while the coord-type preference is UNDETECTED), not the
    neighbour's own `uv_map_name`.
  - Any other type: `PS_UVMap` when `use_paint_system_uv`, else the
    preset.
  - IMAGE: resolution CUSTOM at the neighbour image's size. Otherwise
    the last-used resolution.
  - Merge Down also resets the cursor to DEFAULT (`:740`).
  - `image_name` keeps its default "New Image"
    (`operators/common.py:204-209`), so the image is created under
    that name. Execute renames it (see below).
  - `invoke_props_dialog(self)` at the default width, titled with the
    bl_label.
- Dialog body (`:743-754, 864-875`):
  1. A box with an aligned column: "This operation will convert the
     current layer" (INFO), "into an image layer." (BLANK1).
  2. `other_objects_ui` on the layout, for the active material (box
     contents in PS-007).
  3. `image_create_ui(show_name=False)`. `show_float` defaults to True
     (`operators/common.py:246`), so "Use Float" is drawn.
  4. A box with the label "UV Map" (UV) and the UV `prop_search`.
  There is no materials list and no Advanced Settings.
- Execute (`:756-811, 877-931`):
  - Create the image. Disable every enabled non-folder layer in
    flattened order except the pair, and enable the neighbour if needed
    (the poll already requires it).
  - Set both blend modes to MIX. Bake with `force_alpha=True` and the
    default GPU and margin (on, 8, Adjacent Faces). Restore both blend
    modes and re-enable the other layers. Opacity, clipping and masks
    are not touched, so the survivor's own opacity and masks are in the
    bake and still apply live afterwards (inferred from code).
  - `apply_merged_image_to_layer` on the surviving list entry
    (`:681-689`): name + " Merged", type IMAGE, coord UV, the dialog
    UV map, the image, `linked_layer_uid` "", `linked_material` None,
    `correct_image_aspect` False (default True,
    `paintsystem/data.py:1018-1023`). A linked entry therefore stops
    resolving to its source. The blend mode is kept: MIX for Merge
    Down by the poll, the upper layer's own mode for Merge Up.
  - Each node-tree update of an IMAGE layer renames its image to the
    layer name (`paintsystem/data.py:887-890`; `name`, `type` and
    `image` all trigger it, `:825-830, 1013-1017, 1227-1239`). The
    merged image therefore ends up as "<name> Merged", not "New
    Image". For an IMAGE survivor the name change renames the old
    image first, so the new one likely gets a numeric suffix (inferred
    from code). The survivor's old image is not removed.
  - `delete_layer` on the other entry, which also frees its layer data
    (node tree and empty object, not the image) or hands it to a
    linked copy (`paintsystem/data.py:2083-2099,
    1558-1572`). The survivor becomes active
    (`paintsystem/data.py:2071-2081`).
  - INFO "Merged down in X seconds" / "Merged up in X seconds", X
    rounded to two decimals.

Convert to Image Layer (`operators/bake_operators.py:609-678`):

- bl_label "Transfer Image Layer UV" and bl_description "Transfer the
  UV of the image layer" are copied from the transfer operator
  (`:611-612`). The dialog title and the tooltip therefore read
  "Transfer Image Layer UV". REGISTER/UNDO.
- Poll: the active layer's type is not IMAGE (`:615-618`). FOLDER and
  ADJUSTMENT pass. The menu additionally hides the entry for
  ADJUSTMENT, so it shows for FOLDER layers.
- Invoke (`:620-624`) runs `get_coord_type` and the `PS_UVMap` preset,
  then `BakeOperator.invoke` (`:81-89`), which repeats both, refreshes
  the material list, sets `image_name` to "<group>_<channel>" and opens
  the dialog. The image is created under that name, but setting
  `type` to IMAGE in execute runs the layer's node-tree update, which
  renames it to the layer name (`paintsystem/data.py:887-890,
  1227-1239`).
- Dialog body (`:626-635`): label "Baking material: <material>"
  (MATERIAL); `other_objects_ui`; `image_create_ui(show_name=False,
  show_float=True)`; the "UV Map" box; Advanced Settings (closed).
  Execute passes no `use_gpu`, `margin` or `margin_type`, so Advanced
  Settings has no effect.
- Execute (`:637-678`):
  - Create the image. `get_children(active.id)` collects every
    descendant. Every enabled non-folder layer outside the layer and
    its subtree is disabled.
  - The layer is set to MIX with clipping off, baked with
    `force_alpha=True`, then clip and blend are restored.
  - The same entry is retyped in place: coord UV, the dialog UV map,
    the image, type IMAGE. The operator does not change its name,
    blend mode, clipping, masks or opacity (a Pre Mix socket,
    `paintsystem/data.py:992-995`). One exception: a PASSTHROUGH
    folder comes back as MIX, because `get_layer_blend_type` reports
    PASSTHROUGH as MIX (`paintsystem/graph/common.py:24-29`). The
    other layers are re-enabled.
  - `remove_children(active.id)` removes every descendant from the
    collection without calling `delete_layer_data`, so their node trees
    and empty objects stay in `bpy.data`
    (`paintsystem/nested_list_manager.py:117-123`;
    `paintsystem/data.py:1558-1572`).
    Converting a FOLDER flattens it into one image layer and deletes
    all of its children.
  - It writes `ps_ctx.active_layer`, the resolved layer, so for a
    linked entry the source layer is retyped
    (`paintsystem/context.py:107`; inferred from code).
  - INFO "Converted to image layer in X seconds".

Transfer Image Layer UV (`operators/bake_operators.py:542-606`):

- bl_label "Transfer Image Layer UV", REGISTER/UNDO. Poll: an active
  channel and an active IMAGE layer with an image (`:548-551`). Its own
  invoke is commented out (`:553-554`), so `BakeOperator.invoke` opens
  the dialog with the UV preset.
- Dialog body (`:556-564`): label "Baking material: <material>"
  (MATERIAL); `other_objects_ui`; the "UV Map" box; Advanced Settings.
  There are no image settings. Advanced Settings is ignored as in
  Convert (`:591`).
- Execute (`:566-606`): copy the layer image as
  "<image>_Transferred" (same size and format). Disable every enabled
  layer except the active one; unlike Convert and Merge this includes
  folders (`:579-584`). A disabled folder outputs its input and drops
  its children (`paintsystem/graph/common.py:137-139`,
  `paintsystem/graph/basic_layers.py:130-137, 433-435`), so a layer
  inside a folder bakes without its own contribution (inferred from
  code). MIX, clipping off, bake with `force_alpha=True`, restore.
  Then coord UV, the dialog UV map and the copy as the layer image.
  Assigning the image runs the layer's node-tree update, which renames
  the copy to the layer name (`paintsystem/data.py:887-890`), so the
  "_Transferred" name does not survive; the old image, which likely
  holds that name, pushes a numeric suffix onto the copy (inferred
  from code). The old image stays in `bpy.data`. INFO "Transferred
  image layer UV in X seconds".

Duplicate: v2 has no duplicate operator. The closest path is Copy
Layer then Paste Layer(s) in the same menu (PS-017):

- Copy Layer stores only the active entry, so a folder is copied
  without its children (`operators/layers_operators.py:832-840`).
- Paste inserts at the cursor, with the same parent and at the active
  layer's order, so above it, or at the top of the active folder. The
  pasted layer becomes active (`operators/layers_operators.py:885-912`;
  `paintsystem/nested_list_manager.py:69-82`).
- A plain paste gives the copy a new uid and copies the node tree, the
  empty object and the image (saved first, then `Image.copy`), then the
  remaining properties except name, type and the hierarchy fields
  (`paintsystem/data.py:1492-1504, 1520-1522`). `create_layer` has
  already built a node tree, and for an IMAGE layer a blank image named
  after the layer; the copies replace both and leave them unused
  (`paintsystem/data.py:2049-2056, 2067, 856-858`).

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
