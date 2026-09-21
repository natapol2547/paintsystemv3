# PS-036 Colour popover, floating colour HUD, keymaps, brush tooltips

Epic D. Size L. Milestone M2.

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

## Why the mouse binding is gone

Blender's default Image Paint keymap gives RIGHTMOUSE to
`paint.grab_clone`, and to `brush.stencil_control` with and without every
modifier (`scripts/presets/keyconfig/keymap_data/blender_default.py`
lines 5051 and 5063-5073). An item in the addon keyconfig is matched
before the default one, so binding any RIGHTMOUSE combination in that
keymap takes stencil control away from the user. That breaks the platform
rule against removing or shadowing default entries, and stencil control
is what PS-091 paints selections through. The v3 keymap therefore binds
no mouse button, and the comment in `keymaps/__init__.py` says so.

Two keyboard items come back with this ticket. `I` and `E` are unbound in
the default Image Paint keymap (only Alt+E is taken, on 4.2 and 5.2), and
Image Paint is checked before the 3D View and Screen keymaps that own the
letters generally, so those two shadow nothing while texture painting.
They were registered ahead of their operators and removed again: an item
naming an operator that does not exist is leftover code, and the shortcut
rows in the preferences went with them. This ticket adds the operators,
the two items and the shortcut rows together. Any key added later gets
the same check against `blender_default.py` first; background Blender
reports an empty default keyconfig, so that check reads the file. The
add-on's first Image Paint key, Ctrl+D (PS-093), also has a windowed
guard: `tests/test_keymaps_ui.py` fails when the running
build's default keyconfig binds Ctrl+D where the item runs. I and E
join that guard, and `tests/test_keymaps.py`, with their items.

## v3 design

Two surfaces over one layout spec. They cannot share a draw function:
`UILayout` only exists inside a panel, menu or popup draw callback, and
nothing built on it can be drawn into a `draw_handler`. So the spec is a
small data structure - rows of (kind, property, label) - and each surface
renders it its own way.

### Stage 1: popover from a button (ships alone)

- `panels/popovers.py` holds `PAINTSYSTEM_PT_color_popover` with the v2
  layout: `template_color_picker` scaled by the preference, the swatch
  row, optional HSV sliders, optional Radius/Strength `prop_unified`.
- Opened by `wm.call_panel` from a button in the 3D view tool header and
  from the Paint System panel header. No keymap item is needed for this,
  which is the whole point: a button carries no shadowing risk.
- Native popovers close when the pointer leaves them. `keep_open=True`
  keeps one open across clicks on its own buttons but not across a brush
  stroke, so this stage gives a fast way to reach the colour wheel, not a
  palette that stays up.
- The tooltips popover is ported too, minus the "Suggest more!" URL: the
  platform rules keep links out of the addon's UI. It reads the live
  keymap items through a lookup in `keymaps/__init__.py`, added with the
  popover, so the displayed keys follow user remaps, which v2 hard-coded.

### Stage 2: floating colour HUD

A palette that stays open while painting, one per 3D view area, drawn
with `gpu` and `blf`. This is the only way to get it; Blender has no
persistent floating panel and an addon cannot add a region to a built-in
space type.

- `ops/color_hud.py`: `paint_system.color_hud`, a modal operator started
  from a toggle button in the tool header. Invoking it in an area that
  already has one cancels that instance.
- Each running modal owns its own draw handler, added with
  `bpy.types.SpaceView3D.draw_handler_add(draw, (self,), 'WINDOW',
  'POST_PIXEL')`, and stores `self.area_ptr = context.area.as_pointer()`
  at invoke. The handler fires for every 3D view region, so it returns
  immediately unless `bpy.context.area.as_pointer() == self.area_ptr`.
  That is what makes two 3D views independent: two modals, two handlers,
  two rects, no shared state.
- `modal` returns `PASS_THROUGH` for everything except a mouse event
  inside its own area and inside the HUD rect, or a drag it already owns.
  It never claims an event in another area. The HUD is dragged by its
  title strip. It cancels itself when its area is gone
  (`context.screen.areas` no longer holds the pointer).
- The hue/saturation disc is a `gpu.types.GPUTexture` built with numpy -
  polar coordinates to HSV at the current value, alpha 0 outside the
  radius - and rebuilt only when the value changes, not per redraw.
  Drawn with `gpu.shader.from_builtin('IMAGE')` under
  `gpu.state.blend_set('ALPHA')`. The value slider, swatches and the
  radius and strength bars are flat quads.
- `POST_PIXEL` gives region pixel space with the origin bottom-left, the
  same space as `event.mouse_region_x/y`, so hit tests need no
  conversion. Widget colours come from
  `context.preferences.themes[0].user_interface` and sizes scale by
  `context.preferences.system.ui_scale`, so the HUD follows the theme and
  the DPI.
- Stability, since terms 3.7 covers it: the draw callback catches its own
  exceptions and removes the handler rather than raising on every redraw;
  `cancel` removes the handler; module `unregister` removes any handler
  still alive; there is no timer and nothing is uploaded to the GPU per
  frame. Area pointers dangle after a file read, so `load_post` clears
  the WindowManager set that backs the toggle button's pressed state.
- Not in scope: turning an arbitrary N-panel into a HUD. A `draw()`
  function cannot be introspected into drawable primitives, so every
  widget the HUD shows is written by hand. Keeping it to colour, swatches,
  radius and strength is what keeps stage 2 finite.

### Preferences

The four `*_rmb` preferences are renamed to `*_popover`
(`color_picker_scale_popover`, `show_hsv_sliders_popover`,
`show_active_palette_popover`, `show_brush_settings_popover`) and drive
both surfaces. PS-039 draws them.

## Acceptance

- The tool header button opens the popover with the v2 layout; no keymap
  item exists for it.
- `I` samples colour, `E` toggles erase alpha and the brush blend mode
  reads back correctly.
- No item in the addon keyconfig collides with a default item in the same
  keymap, checked against the shipped `blender_default.py`.
- Stage 2: the HUD stays up across a brush stroke, drags by its title
  strip, and passes every event outside its rect through to the brush.
- Stage 2: with two 3D views open, a HUD in one leaves the other
  untouched; with a HUD in each, dragging one does not move the other.
- Stage 2: disabling the addon with a HUD open leaves no draw handler and
  no artifacts in the viewport.
