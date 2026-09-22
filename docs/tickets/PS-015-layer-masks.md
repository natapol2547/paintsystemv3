# PS-015 Layer masks

Epic B. Size M. Milestone M2.

## v2 behaviour

`LayerMask` (`data.py:763-802`): `type` VALUE/IMAGE/ATTRIBUTE/TEXTURE,
`coord_type`, `blend_mode` SUBTRACT/ADD/MULTIPLY, `mask_image`,
`mask_uv_map`. `Layer.masks`, `create_mask`, `remove_mask`
(`data.py:1341-1400`) and the four `new_*_mask` operators
(`layers_operators.py:1121-1172`) exist, `MAT_MT_AddMaskMenu` is registered
(`layers_panels.py:887`), but no graph code reads masks and the menu is
never drawn. Masks were unfinished in v2.

### v2 UI

Paths are relative to the v2 repository root (`~/paintsystem`).
Corrected ranges for the citations above: `LayerMask` is
`paintsystem/data.py:766-803`, `masks`/`active_mask_index` are
`:1344-1354`, and `create_mask`/`remove_mask`/`remove_active_mask` are
`:1402-1411`.

**No live mesh mask UI.** Nothing in `panels/` draws `masks`: there is
no mask `UIList` (the only lists are groups, channels, layers and
actions; `panels/main_panels.py:60`, `panels/channels_panels.py:36`,
`panels/layers_panels.py:58, 899`). Nothing calls or draws
`MAT_MT_AddMaskMenu`, and the `new_*_mask` operators are referenced
only by that menu (`panels/layers_panels.py:893-896`). `remove_mask`
and `remove_active_mask` have no callers at all. The whole mesh mask
path is dead code.

**Dead menu** `MAT_MT_AddMaskMenu` (bl_label "Add Mask",
`panels/layers_panels.py:887-896`, registered at `:936`), in order:

- "Value Mask" (icon VALUE), `paint_system.new_value_mask`.
- "Image Mask" (icon IMAGE), `paint_system.new_image_mask`.
- "Attribute Mask" (icon ATTRIBUTE), `paint_system.new_attribute_mask`.
- "Texture Mask" (icon TEXTURE), `paint_system.new_texture_mask`.

**Dead operators** (`operators/layers_operators.py:1121-1186`,
registered at `:1215-1218`). The bl_labels are "New Value Mask", "New
Image Mask", "New Attribute Mask" and "New Texture Mask", with
descriptions "Create a new {value|image|attribute|texture} mask". All
are REGISTER/UNDO, poll on an active layer, and have no `invoke` or
dialog. `execute` only calls `active_layer.create_mask(TYPE)`: no
image, UV map or node tree is created, no rebuild runs, and the active
mask index is not moved.

**Data details.** `create_mask` adds a `LayerMask` and sets only
`type`. `uid` stays empty, `name` stays "Mask" and `node_tree` stays
None (`paintsystem/data.py:1402-1405`). `remove_mask` and
`remove_active_mask` do not clamp `active_mask_index`
(`:1407-1411`). Defaults and item order: `type` VALUE; `coord_type`
UV from `MASK_COORDINATE_TYPE_ENUM` (AUTO "Auto UV", UV, OBJECT,
POSITION, GENERATED; `:83-89`), which is a subset of the layer
coordinate types; `blend_mode` MULTIPLY from `MASK_BLEND_MODE_ENUM`,
declared in the order SUBTRACT, ADD, MULTIPLY (`:70-74`). None of the
`LayerMask` properties has an update callback (`:766-803`).
`active_mask_index` does have `update=update_node_tree`, so changing it
would rebuild the layer tree even though no graph code reads masks
(`:1350-1354`).

**Grease Pencil.** The only live mask control in v2 is Blender's
native Grease Pencil layer toggle. In the GP branch of
`MAT_PT_Layers`, the "Layer Settings" section draws
`options_row.prop(active_layer, "use_masks", text="")` in the first
settings row, disabled when the GP layer is locked
(`panels/layers_panels.py:566-580`). `GreasePencil_LayerMaskPanel` is
imported for Blender 4.3 and later but never used (`:42-46`).

**v1 masks.** The legacy v1 layer stored `edit_mask`, `mask_image`,
`enable_mask` and `mask_uv_map` (`LegacyPaintSystemLayer`,
`paintsystem/data.py:3054, 3107-3124`). The v1-to-v2 migration copies
only the fields in `pid_mapping`, which has no mask entries, so v1
masks are dropped without a warning
(`operators/versioning_operators.py:18-27, 116-122`).

## v3 design

Masks are nodes feeding the layer's existing `Mask` input, so they get
caching, hashing and undo for free.

- `PaintSystemMaskNode` base (`nodes/masks/`): inputs `Mask` (previous
  mask, unlinked default 1.0), outputs `Mask`; properties `blend_mode`
  MULTIPLY/ADD/SUBTRACT, `enabled`, `invert`. Subclasses: Value, Image
  (uses `CoordMixin`, paintable), Attribute, Texture. Emit: source value
  combined with the incoming mask by a Math node.
- Masks chain like layers: the layer's `Mask` input links to the top mask
  node; each mask's `Mask` input links to the next. `stack()` exposes
  `StackItem.masks` (top-first).
- UI: a "Masks" sub-panel under Layer Settings with a small `template_list`
  (mirror collection like PS-011, `mask_rows` on the tree) and the
  `MAT_MT_AddMaskMenu` entries. Selecting an image mask row sets the paint
  canvas to the mask image (PS-060).
- Node editor category "Masks".

## Acceptance

- Image mask painted white/black halves the visible layer.
- Two masks combine per `blend_mode`.
- Cached layer (`cache_enabled`) invalidates when a mask changes
  (masks are upstream of the layer, so `subtree_hash` covers them).
