# PS-080 Per-feature tests and CI

Epic I. Size M. Milestone M1, extended by every later ticket.

## Current state

The harness, the version matrix and the base tests are in place.
Each later ticket adds its own `tests/test_<feature>.py`.

- `tests/harness.py` registers the addon from the checkout (the checkout
  folder name is the package name), exposes `VERSION`, `since()`,
  `before()`, `check`, `section`, `guarded`, `import_from` and `finish`
  (exits with a status in both background and windowed sessions).
  Pixel helpers: `bake_group(node_group, inputs=...)` Cycles-bakes a
  shader group's colour and alpha on a unit plane whose UVs span 0..1
  and gives the selection back afterwards, `pixel_at(rgba, u, v)` reads
  the texel under a UV, `close` and `fmt` compare and print colours, and
  `over(backdrop, layer, opacity, clip, blend)` with `mix_blend` and
  `multiply_blend` composites expected colours by the PS-001 coverage
  rule.
- `tests/test_compile.py` covers the compiler core, including
  synchronous compiles, `suspend_compile` batching and blocking.
- `tests/test_blend.py` bakes the layer blend group of every blend mode
  over transparent, half-transparent and opaque backdrops, clipped and
  not, against closed forms (PS-001). It also checks that library groups
  have no fake user: a blend group a layer stops using survives undo, is
  not saved with the file, and is rebuilt when a layer uses it again.
- `tests/test_smoke_loop.py` drives the operators end to end: setup,
  add layers, painted pixels through the compiled group, save and reload
  with packing, undo and redo of setup, add and remove (including the
  double-push pattern where memfile undo reuses in-memory datablocks), and
  undo of a node editor link whose build ran after its undo step.
- `tests/test_stack.py` covers the stack walk with nested folders, stack
  edits, folder compositing by pixel, alpha following hand-made colour
  links, editing state kept out of the fingerprint, `PSContext` and the
  add and remove operators with folders (PS-010, PS-019, PS-030).
  Script-called operators pass `('EXEC_DEFAULT', True)` to get the undo
  push the UI would make, and background sessions need one explicit
  `ed.undo_push()` before undo works.
- `tests/test_layers.py` covers the layer list rows and filter, the
  active layer index, the move option table and every move's result with
  one compile each, the move operators and undo, and the layer type
  registry through the add operator (PS-011, PS-012, PS-029).
- `tests/test_clip.py` finds clip bases and bakes clipped stacks against
  the PS-001 coverage rule applied run by run: base alpha and opacity,
  runs of several layers, disabled layers, the base's blend mode, the
  ends of a folder or channel, folders as bases and clipped, moves,
  recompiles and caches (PS-013).
- `tests/test_painting.py` checks the canvas, image paint mode, active UV
  map and brush alpha as the active layer, its locks, the active object
  and the material slot change, and across paint mode, undo and reload,
  without recompiling (PS-060, PS-061). Its `run` helper evaluates the
  depsgraph after each operator, as the window loop does, so the
  depsgraph handlers see every step.
- `tests/test_images.py` paints a managed image, an image on disk, a
  packed image with a file path, an image whose directory cannot be
  created and a generated cache image, saves, and checks each image was
  packed or written as PS-056 says (and that unused or unchanged images
  were not), then reopens the file and reads the pixels back.
- `tests/test_demo_flow.py` is the M0 demo acceptance test. On the
  factory cube it runs Setup Paint System, adds image and solid layers and
  a folder, moves a layer past the folder, sets blend mode, opacity and
  clipping, toggles paint mode and writes pixels into the canvas each
  selected layer gives, then saves, reopens, undoes and redoes a clip
  change and a removal, and saves and reopens again. After each step the
  compiled group must match the tree and two baked texels must match the
  stack composited by hand.
- `tests/test_api_surface.py` lists every Blender class, RNA property,
  RNA function, operator and `gpu` entry point the addon relies on
  (including the `bl_ui.properties_paint_common` panels the sidebar
  reuses) and checks that each still resolves, with `since`/`until`
  gates for known renames. It also scans the addon sources for
  `bpy.types.X`, `bpy.ops.x.y`, `bpy.app.handlers.x` and `bl_ui.x`
  references and resolves each of them, so a new dependency is checked
  without editing the list.
- `tests/test_icons.py` parses the addon sources with `ast` and checks
  every icon they name against the running Blender: `icon_kwargs` lists
  and `ps_icon` tuples need one Blender icon or `icons/` file,
  `blender_icon` lists for node and node tree `bl_icon` need one name in
  that class's RNA enum, and `WorkSpaceTool.bl_icon` needs a `.dat` file
  in the datafiles icons folder. It fails on a literal `icon=` other than
  `'NONE'` or a literal node `bl_icon`, on `bl_icon` or `ps_icon` set
  anywhere but directly in the class body, on an icon key unpacked with
  `**` into a layout call, on `icon_value` given a name or `get_icon` of
  a missing file, and on any icon expression it cannot resolve to
  literals (a conditional, a name bound exactly once by a plain
  assignment, a dict literal lookup that is not changed in place), with
  file and line. A last section scans planted faults, so a gap in those
  rules fails on every Blender. Names that are missing but
  have a fallback are printed, so a rename shows up before the fallback
  runs out. On Bforartists 5.2.0, which reports itself as 5.3.0 Alpha,
  `VIEW_LOCKED` falls back to `LOCKED`.
- `tests/test_ui_draw.py` runs windowed (Xvfb on CI). It wraps the
  `draw`, `draw_header`, `draw_item`, `filter_items` and `poll` methods of
  every class the addon registers with same-signature recorders, builds a
  painted cube with clipped, nested, locked and disabled layers, opens the 3D view,
  node editor and image editor sidebars, moves the addon's sidebar panels
  onto the active tab, opens popover panels through `wm.call_panel`, menus
  through `wm.call_menu` and the move layer popup, and fails on any
  exception. Blender swallows
  draw exceptions, so this is the only test that catches a broken panel.
  It also checks the painting triggers only a window loop runs: a node
  made active in the node editor and a material slot switch reported
  through the message bus both move the canvas (PS-060). Those checks run
  before any popup opens, because an open menu holds off the node editor
  redraw, and wait for redraws instead of a fixed delay. The Brush and
  Color section bodies start closed and a script cannot open them, so a
  test-only panel draws them in texture paint mode (PS-033).
- Epic J adds GPU and selection tests. `test_texel_map.py` (PS-092),
  `test_pixel_undo.py` (PS-090), `test_selection_model.py`,
  `test_selection_outline.py` and `test_selection_raster.py` (PS-091
  slices 1 and 2). PS-091 milestone 1 adds `test_selection_session.py`
  (the session's target, state, failure memo and retry, `select_all`,
  undo flags and the depsgraph notify), `test_selection_stencil.py` (the
  PNG round trip, apply and restore, block mode, save and load, crash
  recovery, autopack, scene copies and removal, unregister),
  `test_selection_overlay.py` (the overlay shaders by pixel, the colour
  conversion, the clip offset, batches and the draw safety net), and
  three windowed files: `test_selection_session_ui.py` (message bus
  triggers, undo in texture paint and the Selection section),
  `test_selection_stencil_ui.py` (native strokes through the stencil,
  undo and policy) and `test_selection_overlay_ui.py` (ants in the 3D
  view and the image editor, the redraw timer, batch rebuilds and the
  resize safety net). Checks that need a GPU run headless from 5.2 and
  are skipped headless before that; the rest of each file still runs.
  Background tests give GPU objects back (`session.release()`,
  `raster.release()`, `surface.release()`, `texel_map.release()` or the
  module's `unregister`) before they exit, because a texture freed after
  the GPU context has gone segfaults 5.2.
- PS-091 milestone 2 (PS-092, PS-093) adds `test_surface.py` (headless,
  no GPU: surface keys, suspect marks, the resolved sentinel, the entry
  and key limits, view layers and the handlers), `test_selection_tools.py`
  (headless: outline geometry, the operators' `execute`, undo flags,
  default brush ids) and `test_selection_view_raster.py` (headless from
  5.2 and windowed under `--ui`: the view self-test, `view_eye`, Through,
  occlusion, region clipping, moved objects, depth bias at scale, region
  targets, digests and the geometry reasons). Two windowed files:
  `test_selection_view_windowed.py` (a `VIEW` op recorded from a real 3D
  view in single view, quad view and region overlap selects what
  `location_3d_to_region_2d` puts inside it, a native stroke is clipped
  and the ants draw) and `test_selection_tools_ui.py`, which drives the
  tools through Blender's own event handling with simulated input: the
  toolbar group and keymaps, modifier modes, click and cancel, the
  preview's dashes and width, the tool header, an empty drag, undo and
  Adjust Last Operation, and the brush coming back after unregister in
  every window and workspace. It draws the toolbar and the group's popup
  into a recording layout, to check that the group shows Lasso Selection
  and lists Lasso, Rectangle and Ellipse, before any tool of the group
  is used: Blender then shows the last one used for the session. It
  builds the toolbar popup's keymap with
  `bl_keymap_utils.keymap_from_toolbar.generate` and checks that the
  three tools get consecutive number keys, which fails when the tool
  keymap's click item takes Ctrl+D for the first tool. Its
  check that a box dragged over the cube builds a usable mask held the
  merge order of the tools after the view rasteriser, and no software
  renderer gate may skip it.
  `tests/harness.py` gains `simulate` and `drag` around
  `Window.event_simulate`. A simulated drag reaches no keymap until the
  window has handled a key event, so the test sends Esc first. Tests
  that repeat the last operation call
  `bpy.ops.ed.undo_redo('EXEC_DEFAULT', True)`, and windowed undo on 4.2
  passes only `window` and `area` to `temp_override`: a region override
  segfaults 4.2.23 in `poll_select_mask`.
- PS-093's Ctrl+D item adds `test_keymaps.py` and `test_keymaps_ui.py`.
  The headless file reads the item from the add-on keyconfig field by
  field (exactly one, Ctrl only, `PRESS`, no repeat, `DESELECT`) and
  checks that unregister removes every add-on item and a new register
  adds exactly one again. The windowed file is in `event_simulate`. It
  first fails when the running build's default keyconfig binds Ctrl+D,
  as a press or with `any` or a modifier set to any, in a keymap
  consulted in the 3D view in texture paint or the image editor in
  Paint mode, including the keymaps of every tool the two toolbars
  list. It checks with left and with right click select, by setting the
  preset preference, which rebuilds the keyconfig, and puts the setting
  back. Like `test_icons.py`, it fails on a future Blender that takes
  the combination. Bforartists' own keyconfig has no such preference
  and is read as shipped, together with the Blender keyconfig it falls
  back to. The file then wraps `select_all`'s `execute` to record each
  call and presses Ctrl+D in the 3D view with the brush and with Lasso
  Selection active, with nothing selected, and over the image editor in
  Paint mode. It presses Alt+D and checks that nothing is cleared, and
  presses Ctrl+D over the sidebar's Opacity field, which Blender's User
  Interface keymap turns into a driver, and checks that the selection
  is kept. Python cannot read a button's rectangle, so the test moves
  the pointer down the sidebar until `context.property` names the
  field. Before that press it runs the search a button's tooltip runs
  (`find_item_from_operator` from the sidebar, with the None button's
  properties taken from a disabled item in a temporary keymap) with the
  brush and with each selection tool active, and checks that it finds
  the Image Paint item. A key held through a drag is not recorded by
  simulated input, so annotation with D held is not covered. The file
  takes about 10 s.
- `tests/run.sh [--ui] [test files]` drives all of the above. Three
  arrays at the top list the special files: `window_only` files need a
  window and are skipped by the headless loop, `ui_tests` are run again
  windowed under `--ui`, which includes the GPU tests because 4.2 to
  5.1 have no background GPU context, and windowed files in
  `event_simulate` also get `--enable-event-simulate` (before `--python`,
  in both the display and the `xvfb-run` form), which makes Blender
  ignore real input while they run. `BLENDER` selects the executable;
  `XVFB=1` forces the windowed tests through `xvfb-run` with Mesa
  software rendering and the OpenGL backend; `GPU_BACKEND` passes
  `--gpu-backend` to the headless tests. Blender falls back to OpenGL
  silently when the backend asked for cannot start, so with
  `PS_EXPECT_GPU_BACKEND` set the GPU tests fail on any other backend.
  `--python-exit-code 1` makes an uncaught script exception fail the
  run.

## CI

- `.github/resolve-blender.py` reads `blender_version_min` from the
  manifest, finds the latest patch of every series from there to the newest
  stable on download.blender.org, and adds the daily build of the next
  unreleased series (marked `experimental`).
- `.github/workflows/test.yml` runs on push, pull request, weekly and on
  demand. `lint` runs ruff (Pyflakes rules from `pyproject.toml`).
  `package` builds the extension with `natapol2547/blender-extension-builder`
  and runs its validator in strict mode, which covers the extension
  platform rules (no threading, no promo links, no updater, no dev files,
  no stray properties on `bpy.types` IDs, manifest hygiene). One `test`
  job per Blender version: download (cached per release), headless tests,
  from 5.2 the GPU tests again headless on Vulkan through Mesa's lavapipe
  (`test_selection_raster.py`, `test_texel_map.py`,
  `test_selection_session.py`, `test_selection_stencil.py`,
  `test_selection_overlay.py` and `test_selection_view_raster.py`; the
  OpenGL steps miss Vulkan-only faults), windowed tests under Xvfb (the
  tools and keymap tests with event simulation), then `extension
  validate`, `build`, `install-file` and an enable check of the built
  package that also asserts its add-on preferences attach. The native
  stroke, overlay and preview pixel checks have not been run on llvmpipe
  under Xvfb yet; event simulation and the view self-test passed under
  Xephyr with llvmpipe. No software GL gate exists. If pixel checks
  flake in CI, a gate variable set in the Xvfb step skips those checks
  only, and the tools test's mask check is never gated. Each windowed
  file has 300 s; the tools test takes about 30 s locally. Experimental
  builds may fail without failing the
  workflow. A weekly failure opens or updates an issue labelled
  `ci-compat`.
- `.github/workflows/release.yml` (manual) reuses the test workflow, then
  runs the same action with `create-release` on, which builds, validates
  strictly and drafts a GitHub release tagged from the manifest version.
  Pre-releases use a semver suffix in the manifest (`3.0.0-beta.1`).

## Design for feature tests

- Split by feature: `test_stack.py`, `test_layers_<type>.py`,
  `test_ui_ops.py`, `test_migration.py`, sharing the harness pixel
  helpers (`bake_group`, `pixel_at`) for blend tests.
- GPU filter tests (PS-050) are skipped when
  `gpu.platform.backend_type_get() == 'NONE'`.
- v2 parity tests (PS-001, PS-013, PS-070) install the v2 addon from
  `~/paintsystem` into the same Blender in a separate process and dump
  reference pixels to `tests/fixtures/`; the fixtures are committed so CI
  does not need v2.
- New panels are picked up by `test_ui_draw.py` automatically; panels
  outside the three editors need an explicit open step there.

## Acceptance

- `tests/run.sh` runs all files and exits non-zero on any failure.
- The test matrix is green on push for every supported Blender series.
- A release cannot be built while the matrix fails.
