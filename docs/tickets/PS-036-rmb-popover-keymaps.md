# PS-036 Shift+RMB popover, keymaps, brush tooltips

Epic D. Size S. Milestone M1.

## v2 behaviour

- `MAT_PT_TexPaintRMBMenu` (`panels/extras_panels.py:309-406`, ui_units_x
  10, poll PAINT_TEXTURE): colour box with `template_color_picker` scaled
  by `color_picker_scale_rmb`, swatch row (colour / secondary / flip),
  optional HSV sliders (`show_hsv_sliders_rmb`), optional Radius/Strength
  `prop_unified` (`show_brush_settings_rmb`).
- `keymaps.py`: Shift+RMB -> `wm.call_panel` on the popover in the Image
  Paint keymap; `paint_system.color_sample` = I;
  `paint_system.toggle_brush_erase_alpha` = E.
- `MAT_PT_BrushTooltips` (`extras_panels.py:25-59`): keymap shortcut rows
  rendered as event icons, "Preferences" and "Suggest more!" URL.
- Operators: `color_sample` (`utils_operators.py:150`, `paint.sample_color
  (merged=True)` on 4.4+), `toggle_brush_erase_alpha` (`:127`),
  `open_paint_system_preferences` (`:189`).

## v3 design

- `panels/popovers.py` with the RMB popover and tooltips popover ported
  as is; `keymaps.py` in v3 already has a registration helper, add the
  three items.
- The tooltips panel reads the actual keymap items so the displayed keys
  follow user remaps (v2 hard-coded them).

## Acceptance

- Shift+RMB in texture paint opens the popover with the v2 layout.
- I samples colour, E toggles erase alpha and the brush blend mode reads
  back correctly.
