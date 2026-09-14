# PS-032 Channels sub-panel, settings and add-channel menu

Epic D. Size M. Milestone M1 (list, add/delete/move), M2 (settings).

## v2 behaviour

`panels/channels_panels.py`:

- Closed header shows popover `MAT_PT_ChannelsSelect` (`:93`, ui_units_x
  10) with the active channel name and type icon; open shows
  `draw_channels_panel` (`:114-123`).
- `PAINTSYSTEM_UL_channels.draw_item` (`:39-63`): baked channel shows
  icon + name + "Baked" (TEXTURE_DATA); otherwise `split(factor=0.7)` with
  type icon (icon_only, no emboss), name, and the group node's unlinked
  input `default_value` (colour or float) on the right.
- Side column (`:71-91`): add (`MAT_MT_AddChannelMenu` when templates
  remain, else `add_channel` with CUSTOM), `delete_channel`,
  `move_channel_up/down`. `rows=max(len(channels), 3)`.
- `layout.panel("MAT_PT_ChannelsSettings")` default closed ->
  `draw_channels_settings_panel` (`:156-211`): Use Baked toggle + delete
  bake image; VECTOR baked -> `bake_vector_space`; else property-split
  column `type`, `color_space`, `use_alpha` (+ alpha default value),
  VECTOR block (default value, Vector Transform panel), FLOAT block
  (`use_max_min`, min, max).
- `MAT_MT_AddChannelMenu` (`:231`): channel templates (Color, Metallic,
  Roughness, Normal) not yet present, then "Custom".
- Operators (`operators/channel_operators.py`): `add_channel` (template
  or CUSTOM dialog with live "name will be renamed" warning), `delete_channel`
  with confirmation, `move_channel_up/down`.

## v3 design

- Replace the existing channel UIList in `panels/main_panels.py` with the
  v2 draw, reading the default value from the material's group node input
  socket (found through `MATERIAL_GROUP_KEY`) when unlinked.
- `ops/channel_ops.py` already has add/remove/move; extend `add_channel`
  with `template` (PS-041 channel templates) and the CUSTOM dialog
  properties from PS-005.
- Settings sub-panel draws PS-005/PS-006/PS-007 properties with the v2
  layout.

## Acceptance

- Screenshot parity with v2 for a Color + Roughness setup.
- Adding "Metallic" from the menu creates a FLOAT channel wired to the
  Principled Metallic input when the template group is PBR (PS-041).
