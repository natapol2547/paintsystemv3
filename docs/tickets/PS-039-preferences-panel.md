# PS-039 Preferences panel parity

Epic D. Size S. Milestone M2.

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

`preferences.py` in v3 already declares these properties (plus update
checking). Remaining work:

- Port the draw layout from v2, dropping `use_legacy_ui`.
- `panels/common.py::scale_content` helper honouring `use_compact_design`.
- `hide_painting_tips` operator (`utils_operators.py:261`) for the tip
  boxes.
- `developer_mode` gates the compiled-info blocks and a "Recompile All"
  button.

## Acceptance

- Every preference changes the UI as in v2 (manual check list in the
  ticket: compact design, opacity column, quick access row, tooltips).
