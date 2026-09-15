# PS-033 Brush and Colour sub-panels

Epic D. Size M. Milestone M1.

## Status

Partly done (M0 slice 6): `panels/brush_panels.py` draws the Brush and
Color sections of the 3D view main panel, below the channel list, while
texture painting. They keep the `layout.panel` idnames `MAT_PT_Brush` and
`MAT_PT_BrushColor`.

- Brush: a brush picker, then Blender's `brush_settings` in a box and the
  Advanced Settings sub-section (occlude, backface culling, normal falloff
  and its angle). The picker is the brush asset shelf popover
  (`BrushAssetShelf.draw_popup_selector`) from 4.3 and
  `template_ID_preview` before. v2 showed no picker from 4.3 on.
- Color: closed, the header shows the colour, the secondary colour and
  the flip button, as in v2. Open, it draws Blender's
  `draw_color_settings` with the colour and gradient switch: picker,
  swatches, unified colour toggle, and colour jitter where the version has
  it. That replaces v2's hand-built picker. A Color Palette sub-section
  follows.
- The paint settings come from `paint_settings_from_active_tool` where it
  exists (5.3) and `paint_settings` before; the panels detect features
  instead of comparing version numbers.
- Not ported for the demo: the tooltips popover (its shortcuts have no
  operators yet), Add Preset Brushes, the colour picker settings popover
  (scale, HSV sliders, hex, which need addon preferences and scene
  properties), colour history (PS-062) and grease pencil.
- Tests: a script cannot open a `layout.panel` section, so
  `test_ui_draw.py` registers a test-only panel that draws the open
  section bodies on every Blender in the matrix.

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
