# PS-035 Layer Settings sub-panels

Epic D. Size L. Milestone M1 (image, transform), M2 (rest).

## Status

Partly done (M0 slice 4): the Layers panel draws a "Layer Settings"
`layout.panel("layer_settings_panel")`, disabled while the layer is
locked, with the type's `draw_source_settings(context, layout)`: the
image and UV map for image layers, the colour for solid layers, nothing
for folders. Nodes draw the same method between their layer and cache
settings. The name is `draw_source_settings`, pairing with `emit_source`,
because `draw_layer_settings` already draws the lock, opacity and blend
row on nodes. The Image, Transform and Actions sub-panels are open.

## v2 behaviour

`draw_layer_settings` (`panels/layers_panels.py:171-492`),
`layout.enabled = not lock_layer`:

- Per-type box: ADJUSTMENT `template_node_inputs`; NODE_GROUP extra
  inputs; GRADIENT empty select / fix missing; SOLID_COLOR colour;
  RANDOM seed/base/H/S/V; GEOMETRY transform, backface, normalize, AO.
- `image_settings_panel` "Image" (`:304-333`): header has left-aligned
  "Filters" menu (`MAT_MT_ImageFilterMenu`); body: quick edit / reload /
  project apply + `toggle_image_editor` row, `image_node_settings`,
  colour/alpha socket grid, `correct_image_aspect`.
- `gradient_node_settings_panel` + nested Map Range (`:334-352`).
- `texture_node_settings_panel` (`:353-366`),
  `attribute_node_settings_panel` (`:367-377`).
- `layer_transform_settings_panel` "Transform" (`:379-437`): `coord_type`
  enum in the header + `transfer_image_layer_uv`; body per coord type
  (UV `prop_search`, DECAL empty + depth clip, PROJECT view reset / set
  projection / scale / space / falloff, PARALLAX space, uv map, depth),
  nested `mapping_panel`; collapsed + `use_panel_quick_access` -> compact
  row (`:438-460`).
- `layer_actions_settings_panel` "Actions" (`:462-492`): tips box,
  `PAINTSYSTEM_UL_Actions` rows=5, add/delete, bind/frame/marker/type.

## v3 design

- Each layer node class implements `draw_layer_settings(layout, context)`
  for its per-type box and declares which shared sub-panels it uses:
  `CoordMixin.draw_transform_settings` (PS-008), image settings (PS-020),
  actions (PS-063), masks (PS-015, new).
- `panels/layers_panels.py::draw_layer_settings` orchestrates: type box,
  then sub-panels in the v2 order, keeping the v2 `layout.panel` idnames.
- Parameter nodes (PS-003) are drawn through `ctx.param_node`.

## Acceptance

- Screenshot parity for an image layer (Image + Transform open) and a
  gradient layer (Gradient + Map Range open).
- Quick access row appears when the Transform panel is collapsed and the
  preference is on.
