# PS-061 Toggle paint mode and isolate channel

Epic G. Size M. Milestone M1.

## Status

Partly done (M0 slice 6): `ops/paint_ops.py` ports `toggle_paint_mode`
for meshes. It makes the object active and selected, leaves any other mode
for Object mode, and otherwise enters Texture Paint. Entering sets 3D view
shading to Rendered, or Material Preview under Cycles, then calls
`update_active_image` (PS-060). There is no grease pencil variant. The main
panel draws it as in v2: a large toggle, depressed in Texture Paint, and a
save button beside it. Channel isolate is deferred until after the demo.

## v2 behaviour

- `toggle_paint_mode` (`operators/utils_operators.py:27`): OBJECT <->
  TEXTURE_PAINT (grease pencil variant), viewport shading RENDERED
  (MATERIAL under Cycles), `update_active_image`.
- `isolate_active_channel` (`:106`): toggles `ps_mat_data.preview_channel`;
  on enable records the node/socket feeding the Material Output and the
  scene view transform, wires the group's channel output straight to the
  Material Output, sets the view transform to Standard and
  `channel.disable_output_transform`; on disable restores all (`data.py:2522`).
  Forces RENDERED shading.

## v3 design

- `paint_system.toggle_paint_mode` ported as is.
- Isolate is a compile option rather than a material rewrite where
  possible: `tree.preview_channel: StringProperty` (SKIP_SAVE). When set,
  `interface_outputs` adds a `Preview` shader output and the Group Output
  emitter connects an Emission of the previewed channel to it; the
  operator links that output to the Material Output surface socket and
  records the previous link by socket name on the material
  (`ps_preview_restore`). Disable removes the link, restores the
  original and clears the property. View transform handling as v2.
- Because `preview_channel` is SKIP_SAVE, a file saved while isolated
  reloads un-isolated with the restore data still on the material;
  `on_load_post` runs the restore if `ps_preview_restore` is present.

## Acceptance

- Isolate the Roughness channel: viewport shows the grey mask; disable
  restores the Principled link and the view transform.
- Saving while isolated and reloading shows the normal material.
