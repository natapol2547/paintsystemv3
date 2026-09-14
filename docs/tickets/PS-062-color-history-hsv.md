# PS-062 Colour history, HSV and hex colour, unified colour sync

Epic G. Size S. Milestone M1.

## v2 behaviour

- `ps_scene_data.hue/saturation/value/hex_color` (`data.py:2867-2900`)
  with update callbacks writing the brush colour; `update_hsv_color`
  refreshes them from the brush.
- msgbus on `UnifiedPaintSettings.color`, `Brush.color`, `Object.mode`
  (`handlers.py:240`).
- `color_history_handler` (`handlers.py:145`, depsgraph IMAGE updates):
  prepends the brush colour to the "Paint System History" palette when
  the active image layer is dirty, dedupes within 0.001, caps at 20.
  `ensure_color_history_palette` on load.

## v3 design

Port onto `scene.paint_system` (`PaintSystemSceneSettings`) unchanged.
Guard the depsgraph handler so it returns immediately when no Paint
System tree is active, since it runs on every depsgraph update.

## Acceptance

- Painting a stroke adds the colour to the history palette once.
- Editing Hue moves the brush colour; picking a colour updates the
  sliders.
