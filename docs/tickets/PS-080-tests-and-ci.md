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
  shader group's colour and alpha on a unit plane whose UVs span 0..1,
  `pixel_at(rgba, u, v)` reads the texel under a UV, `close` and `fmt`
  compare and print colours.
- `tests/test_compile.py` covers the compiler core, including
  synchronous compiles, `suspend_compile` batching and blocking.
- `tests/test_blend.py` bakes the layer blend group of every blend mode
  over transparent, half-transparent and opaque backdrops, clipped and
  not, against closed forms (PS-001).
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
- `tests/test_api_surface.py` lists every Blender class, RNA property,
  RNA function, operator and `gpu` entry point the addon relies on
  (including the `bl_ui.properties_paint_common` panels the sidebar
  reuses) and checks that each still resolves, with `since`/`until`
  gates for known renames. It also scans the addon sources for
  `bpy.types.X`, `bpy.ops.x.y`, `bpy.app.handlers.x` and `bl_ui.x`
  references and resolves each of them, so a new dependency is checked
  without editing the list.
- `tests/test_ui_draw.py` runs windowed (Xvfb on CI). It wraps the
  `draw`, `draw_header`, `draw_item` and `poll` methods of every class the
  addon registers with same-signature recorders, builds a painted cube,
  opens the 3D view, node editor and image editor sidebars, moves the
  addon's sidebar panels onto the active tab, opens popover panels
  through `wm.call_panel` and fails on any exception. Blender swallows
  draw exceptions, so this is the only test that catches a broken panel.
- `tests/run.sh [--ui] [test files]` drives all of the above.
  `BLENDER` selects the executable; `XVFB=1` forces the UI test through
  `xvfb-run` with Mesa software rendering and the OpenGL backend.
  `--python-exit-code 1` makes an uncaught script exception fail the run.

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
  UI draw test under Xvfb, then `extension validate`, `build`,
  `install-file` and an enable check of the built package. Experimental
  builds may fail without failing the workflow. A weekly failure opens or
  updates an issue labelled `ci-compat`.
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
