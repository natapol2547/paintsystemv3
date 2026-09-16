# PS-039 Preferences panel parity

Epic D. Size S. Milestone M2.

## Status

PS-091 added a "Selection" box to `PaintSystemPreferences.draw` with
five properties the selection overlay reads: `show_selection_3d`,
`selection_wash_color`, `selection_wash_opacity`,
`selection_ant_color_a` and `selection_ant_color_b` (colours are
`COLOR_GAMMA`). They are not v2 preferences; the port keeps the box.

`common.ADDON_ID` is now `__package__` instead of the literal
`"paint_system"`. Installed as an extension, the add-on's module is
`bl_ext.<repository>.paint_system`, so the literal never matched:
`PaintSystemPreferences` never attached and
`context.preferences.addons["paint_system"]` did not exist. Code that
reads the preferences looks them up with `ADDON_ID`. The CI enable step
asserts that the preferences attach to the installed package.

## v2 behaviour

`panels/preferences_panels.py:9-113` draws the preferences listed in
`preferences.py:7-24`: `show_tooltips`, `show_hex_color`,
`show_more_color_picker_settings`, `use_compact_design`,
`color_picker_scale`, `color_picker_scale_rmb`, `hide_norm_paint_tips`,
`hide_color_attr_tips`, `use_legacy_ui`, `show_hsv_sliders_rmb`,
`show_active_palette_rmb`, `show_brush_settings_rmb`,
`preferred_coord_type`, `show_opacity_in_layer_list`,
`use_panel_quick_access`, `developer_mode`. `use_compact_design` turns
`scale_content` into a no-op (`panels/common.py:14-20`).

## v3 design

`preferences.py` in v3 already declares these properties, with the four
`*_rmb` ones renamed `*_popover` (PS-036 replaced the right-click
popover with a button and a floating HUD). Remaining work:

- Port the draw layout from v2, dropping `use_legacy_ui`.
- `panels/common.py::scale_content` helper honouring `use_compact_design`.
- `hide_painting_tips` operator (`utils_operators.py:261`) for the tip
  boxes.
- `developer_mode` gates the compiled-info blocks and a "Recompile All"
  button.

## Acceptance

- Every preference changes the UI as in v2 (manual check list in the
  ticket: compact design, opacity column, quick access row, tooltips).
