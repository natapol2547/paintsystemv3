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
