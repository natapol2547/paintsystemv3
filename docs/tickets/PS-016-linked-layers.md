# PS-016 Linked layers

Epic B. Size M. Milestone M2.

## v2 behaviour

`paste_layer(linked=True)` creates a BLANK layer with `linked_layer_uid`
and `linked_material` (`layers_operators.py:867-913`). `get_layer_data()`
(`data.py:1524`) resolves the source through a cached uid map; the linked
layer shares the source's node tree datablock, so edits anywhere show
everywhere. `unlink_layer` (`:915`) copies the data out. The list shows a
`LINKED` icon; `MAT_MT_LayerMenu` offers "Unlink Layer".

### v2 UI

All paths are relative to `~/paintsystem`. `data.py` means
`paintsystem/data.py`.

Corrections to the summary above:

- A folder source is pasted as a FOLDER entry, not BLANK
  (`operators/layers_operators.py:900`).
- The `LINKED` icon and "Unlink Layer" also appear on the source row,
  not only on the links (`panels/layers_panels.py:99-100, 741-747`).
- Unlink copies the image with `image.copy()`, so the layer's `image`
  field no longer points at the shared image (`data.py:1496-1500`). For
  an unlinked link the copied node tree still reads the source's image;
  see Unlink below.

Data model:

- A link is an ordinary `Layer` entry with `linked_layer_uid`
  (StringProperty "Linked Layer ID") and `linked_material`
  (PointerProperty to Material). Both use `update=update_node_tree`
  (`data.py:1383-1393`). `is_linked` is true when both are set
  (`data.py:1378-1381`).
- The entry keeps its own `uid`, `id`, `order`, `parent_id` and `name`.
  Everything else is read from the source. `find_node`, the mix nodes
  and `opacity` all go through `get_layer_data()`
  (`data.py:943-995`).
- `get_layer_data()` (`data.py:1524-1536`) returns `self` for a normal
  layer. For a link it looks the uid up in a per-material cache
  (`_get_material_layer_uid_map`, `data.py:1616-1638`). A missing
  material logs an error and returns None.
  - On a cache miss it rebuilds the cache, but it then returns the
    lookup from the old map (`:1535`). The first lookup after a miss
    therefore still returns None.
  - The cache is only invalidated for the active material, when a
    channel rebuilds (`data.py:1825`).
- `PSContext` exposes both sides: `unlinked_layer` is the list entry and
  `active_layer` is `unlinked_layer.get_layer_data()`
  (`paintsystem/context.py:107-108`).
- `is_layer_linked(layer)` (`data.py:3025-3030`) scans every layer in
  every material. It counts the source uid of each entry and is true
  when the count is above one. So it is true for the source and for
  every link. It is also true for layers in a copied material, which
  carry the same uids (inferred from code).

Creating a link. The only UI path is the layer menu:

- `MAT_MT_LayerMenu` (`panels/layers_panels.py:724-785`, bl_label "Layer
  Menu") opens from the DOWNARROW_HLT menu button in the layer list side
  column (`panels/common.py:495-496`). In order:
  - "Convert to Image Layer" (`image` icon), when the resolved type is
    not IMAGE or ADJUSTMENT.
  - "Unlink Layer" (UNLINKED), when `is_layer_linked(unlinked_layer)`.
  - A separator, if either of those was drawn.
  - "Copy Layer" (COPYDOWN), "Copy All Layers" (COPYDOWN), "Paste
    Layer(s)" (PASTEDOWN, `linked=False`), "Paste Linked Layer(s)"
    (LINKED, `linked=True`).
  - A separator, then "Merge Up" (TRIA_UP_BAR) and "Merge Down"
    (TRIA_DOWN_BAR).
- `paint_system.copy_layer` (`operators/layers_operators.py:820-840`,
  bl_label "Copy Layer") clears the clipboard and stores the active
  entry. `paint_system.copy_all_layers` (`:843-864`) stores every entry
  of `flattened_unlinked_layers`. `add_layer_to_clipboard`
  (`data.py:2912-2920`) stores `(uid, material)`. For a link it stores
  the link's source, so a link of a link points straight at the
  original and chains never form. For a normal layer it stores its own
  uid and the active material.
- `paint_system.paste_layer` (`operators/layers_operators.py:867-912`,
  bl_label "Paste Layer", REGISTER and UNDO). `linked` is a
  BoolProperty with SKIP_SAVE. Poll: the clipboard is not empty. There
  is no dialog. For each clipboard entry, in order:
  - Resolve the source with `get_layer_by_uid`. Skip it silently if it
    is missing.
  - With `linked`, call `create_layer(layer.layer_name, "BLANK" or
    "FOLDER", insert_at="CURSOR" for the first entry and "AFTER" for
    the rest, linked_layer_uid=..., linked_material=...)`
    (`data.py:2022-2069`). The new entry gets a fresh uid of its own.
  - Remap parents through `new_layer_id_map`, which is keyed by the
    source's `id`. Root entries go under the active folder, or under
    the active layer's parent (`operators/layers_operators.py:890-894`).
    That test reads `ps_ctx.active_layer`, the resolved source. With a
    link active, it uses the source's `id` or `parent_id`, which come
    from the source channel's id space (inferred from code).
  - Nothing stops pasting into the same material or the same channel,
    so v2 already links within one tree.
  - A folder copied alone is pasted without children. Children are
    separate entries (`data.py:1956-1962`), and `copy_layer` stores only
    the folder.
  - A copied layer whose source sits in a folder that is not in the
    clipboard fails with a KeyError at `:906` (inferred from code). This
    covers any single layer copied from inside a folder with "Copy
    Layer". The entry created at `:900-903` is already in the list when
    the error is raised.

Linked row in the layer list (`MAT_PT_UL_LayerList.draw_item`,
`panels/layers_panels.py:58-108`):

- `linked_item = item.get_layer_data()`. If it is None the method
  returns, so a broken link is an empty row with no warning
  (`:61-63`).
- Indentation uses the entry's own level: `folder_indent` at the
  deepest level, BLANK1 for the others. The row is disabled when the
  entry's parent is disabled (`:73-76`). For a linked folder this reads
  the entry's own unused `enabled`, not the source's.
- The icon part is disabled when the source has opacity 0 or is hidden.
  It shows the clipping icon (`scale_x` 0.7) when the source clips, and
  `draw_layer_icon(linked_item)`.
- The name is `prop(linked_item, "name", emboss=False)` (`:91`), so
  renaming a linked row renames the source everywhere.
- Right-aligned icons, in order:
  - Lock (VIEW_LOCKED, or LOCKED on older builds).
  - KEYTYPE_KEYFRAME_VEC when the source has actions.
  - LINKED when `is_layer_linked`.
  - The warnings button (`error` icon).
  - The opacity text, when `show_opacity_in_layer_list` is on.
  - The source's `enabled` toggle (HIDE_OFF or HIDE_ON).
- `filter_items` (`:110-138`) collapses children by the entry's own
  `is_expanded`. The folder icon toggles the source's `is_expanded`, so
  collapsing a linked folder does not hide its children (inferred from
  code).
- `is_layer_linked` runs a full scan of all materials for every drawn
  row.

Settings area and painting:

- The layer settings panel draws `ps_ctx.active_layer`, which is the
  source. Editing a link edits the source directly. No v2 panel draws a
  "linked" header, a source name or a jump-to-source button. Outside
  the list row and the layer menu, no panel code tests for links. The
  shader editor panel rows resolve entries with `get_layer_data()` and
  show the source's name and icon with no link marker
  (`panels/extras_panels.py:462-477`; see PS-037).
- `update_active_image` paints on the source's image and activates the
  source's UV map (`data.py:252-277`).

Unlink (`paint_system.unlink_layer`,
`operators/layers_operators.py:915-932`, bl_label "Unlink Layer", poll
`active_layer`, no dialog). It calls `unlink_layer_data`
(`data.py:1509-1518`), then rebuilds the active channel.

- On the source, when it has links: `transfer_linked_data`
  (`data.py:1538-1556`) makes the first link found the new owner. It
  copies every property except name, uid, id, order, parent_id and
  layer_name through `link_layer_data` (`:1506-1507`), which includes
  the node tree, the image and the empty linked fields. The other links
  are re-pointed to the new owner. The source then gets
  `duplicate_layer_data(self)`: a new uid, a copied node tree, the image
  saved and then copied, and a copied empty.
- `transfer_linked_data` (used by unlink and delete) assumes a link
  exists. When the uid count is above one only because a material copy
  duplicated the uid, it finds no link and `list(...)[0]` raises
  IndexError (`data.py:1549`; inferred from code). In that state the
  multi-user guard usually hides the Layers panel
  (`panels/layers_panels.py:505-506`).
- On a link: the linked fields are cleared, then `copy_layer_data`
  runs, which is the same duplication plus a property copy
  (`:1520-1522`). The property copy ignores `type`, so the entry stays
  BLANK (or FOLDER) with a copied node tree. `update_node_tree` never
  rebuilds that tree, because BLANK returns early (`data.py:848-849`).
  The copied tree's image node still points at the source's image
  (`paintsystem/graph/basic_layers.py:424-429`), while the entry's
  `image` and the paint canvas use the copy (`data.py:1500, 271-272`).
  So the compiled result keeps showing the source's image, and strokes
  go to an image the tree does not read. This is inferred from code,
  not run.

Delete (`paint_system.delete_item`,
`operators/layers_operators.py:622-655`, bl_label "Remove Item", a
`MultiMaterialOperator`):

- The props dialog reads "Delete '<entry name>' ?" (ERROR) and "Click
  OK to delete, or cancel to keep the layer".
- `Channel.delete_layer` (`data.py:2083-2099`) removes the entry and its
  children. It calls `delete_layer_data` on each (`data.py:1558-1572`).
  - For a source with links, ownership is transferred as in unlink. The
    links keep working, so deleting a layer never leaves a missing
    source.
  - Otherwise the entry's empty object and node tree datablocks are
    removed. A link has neither.
- Missing sources come from `delete_channel` (`data.py:2754-2762`) and
  `delete_group` (`operators/group_operators.py:447-448`). Neither
  transfers ownership.
- With a missing source, `delete_layer_data` calls
  `is_layer_linked(None)` and `update_node_tree` reads
  `get_layer_data().coord_type` (`data.py:836`). Both would raise
  AttributeError (inferred from code).

What a link shares when compiled (`Channel.update_node_tree`,
`data.py:1897-1964`):

- Each entry becomes one group node. Its identifier is the entry's own
  uid (`:1906`). The node tree, the Clip input, the clip mode, the
  PASSTHROUGH check and the FOLDER over sockets all come from the
  source.
- Visibility is baked into the layer node tree
  (`paintsystem/graph/common.py:137-142`). So blend mode, opacity,
  visibility, clip and coordinates are all shared. Only the position in
  the stack (order and parent) is per entry.
- `Layer.masks` exists (`data.py:1344-1353`), and an "Add Mask" menu is
  registered (`panels/layers_panels.py:887-896`). No panel opens that
  menu and no compile code reads masks, so v2 has no mask behaviour to
  compare.
- The clip helper node is named `clip_nt_{layer.id}` after the source's
  id (`:1918`). That id comes from the source channel's id space. Two
  clip runs that start on the same id in one channel (a source and its
  link, or a link and a local layer that happen to share the id) ask
  for the same identifier twice. `add_node` raises ValueError on a
  duplicate while the builder is not yet compiled
  (`paintsystem/graph/nodetree_builder.py:443-445`). A rebuild of an
  existing channel tree counts as compiled (same file, `:276-277`), and
  there the second command replaces the first (same file, `:473`), so
  both runs share one helper node and are wired wrongly (inferred from
  code).
- A link whose source is missing is skipped by the compile loop
  (`:1899-1901`).
- `update_blend_mode` rebuilds every channel that holds the source or a
  link to it (`data.py:1356-1360`, `find_channels_containing_layer`
  `:297-303`). `update_is_clip` only rebuilds the active channel
  (`data.py:1266-1268`). Other materials keep the old clip state until
  their next rebuild.

Other code paths that touch links:

- Merge clears the linked fields on the merged layer
  (`operators/bake_operators.py:681-689`). `merge_down` and `merge_up`
  find the active layer with `flattened_layers.index(resolved)`, which
  picks the first occurrence when a source appears twice in the channel
  (`:698-705, 820-827`).
- `inspect_layer_node_tree` enters the first group node in the channel
  tree whose tree is the source's tree
  (`operators/shader_editor.py:72-82`) and runs `node.view_all`
  (`:85`). It does not select or frame a node.
  When a source and its link share a channel, it may enter through the
  other node, but both nodes use the same tree, so the view is the same.
  Only the node recorded in the editor path differs.
- `duplicate_paint_system_data` skips links and gives non-linked layers
  new uids (`operators/utils_operators.py:330-334`). A link inside the
  duplicated material keeps pointing at its old material.
- Migration turns v2.0 global layers into links. The first user becomes
  the owner, and later users get `linked_layer_uid` and
  `linked_material` (`paintsystem/versioning.py:25-60`).

## v3 design

- `PaintSystemLinkedLayerNode(PaintSystemLayerNode)`: `source_tree`
  (PointerProperty to a `PaintSystemNodeTree`, may be the same tree) and
  `source_uuid`. `resolve()` returns the source node or `None`.
- `emit_source` emits the source node's source subgraph under the linked
  node's own uuid prefix (`ctx.emit_as(self, source_node)` swaps the
  node-id namespace while the source's `emit_source` runs). Blend mode,
  opacity, clip and masks belong to the linked node; only the content is
  shared. Sources that are folders link the whole content chain.
- `hash_parts` includes `source_tree.compiled_hash`-independent data: the
  source node's `subtree_hash` computed in the source tree's context.
- Editing: the Layer Settings panel for a linked node draws the source
  node's settings (`draw_layer_settings` delegated) with a header row
  "Linked to <tree> / <layer>" and the "Unlink" operator, which replaces
  the node by a deep copy of the source chain.
- Missing source (deleted) compiles to transparent and shows the warning
  icon with "Linked layer source missing".
- Cross-material linking works because trees are datablocks: the linked
  node stores the tree pointer, not a material.

## Acceptance

- Paint on the source image; the linked layer in another material
  updates after compile.
- Unlink produces an independent copy with new uuids and a copied image
  reference (image is shared, as in v2).
- Deleting the source shows the warning and does not crash the compile.
