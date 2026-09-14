# PS-033 Brush and Colour sub-panels

Epic D. Size M. Milestone M1.

## v2 behaviour

- `toggle_paint_mode_ui` (`panels/common.py:269-314`): 1.7x "Toggle Paint
  Mode" (depress in PAINT_TEXTURE, icon `paintbrush`), isolate channel
  button (depress on `preview_channel`), `wm.save_mainfile` (icon `save`);
  normal-painting tip box with `arrow_up` icon and X (`hide_norm_paint_tips`)
  when `show_tooltips`; "Bake and Export" menu row.
- `layout.panel("MAT_PT_Brush")` "Brush" (icon `brush`): header popover
  `MAT_PT_BrushTooltips` (INFO) when `show_tooltips`; body draws the
  brush settings (`draw_brush_settings`, `extras_panels.py`).
- `layout.panel("MAT_PT_BrushColor")` "Color" (icon `color`): open ->
  header popover `MAT_PT_BrushColorSettings` "Settings"; closed -> header
  shows colour / secondary colour split + `brush_colors_flip`.
  `draw_brush_color_settings` (`extras_panels.py:189-274`):
  `prop_unified_color_picker` scaled by `color_picker_scale`, HSV sliders
  on `ps_scene_data.hue/saturation/value` when
  `show_more_color_picker_settings` (Hue hidden for SQUARE_SV picker),
  `hex_color` row when `show_hex_color`, `color_jitter_panel` in a box
  (guarded import), collapsible "Color History"
  (`template_palette(color_history_palette)`, fallback text) and "Color
  Palette" (`template_ID` + `template_palette`).
- `MAT_PT_BrushColorSettings` (`:144-158`): `color_picker_type`,
  `color_picker_scale`, `show_hex_color`, `show_more_color_picker_settings`.

## v3 design

- `panels/brush_panels.py` ports the three blocks verbatim against v3
  data: `scene.paint_system` gains `hue`, `saturation`, `value`,
  `hex_color`, `color_history_palette` (PS-062); `preview_channel` lives
  on the tree (PS-061).
- Keep the `layout.panel` idnames `MAT_PT_Brush` and `MAT_PT_BrushColor`.
- Brush settings body: use `bl_ui.properties_paint_common` helpers
  (`brush_settings`, `brush_settings_advanced`) the same way v2 does; check
  the 5.2 signatures because they changed in 4.3.

## Acceptance

- Screenshot parity in TEXTURE_PAINT mode with the panel open and closed.
- Toggle Paint Mode enters/leaves texture paint and switches viewport
  shading (PS-061).
