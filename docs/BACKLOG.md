# Paint System v3 port backlog

Tickets live in `docs/tickets/PS-NNN-*.md`. Each ticket records the v2
behaviour it replaces (with `file:line` references into `~/paintsystem`),
the v3 design, acceptance criteria and dependencies.

Guiding constraints for every ticket:

- The 3D-view sidebar must look and behave like v2. Only the backend changes.
- The Paint System node tree is the document. The shader artifact is derived
  by `compile_tree` and is never edited by hand except through the
  artifact-owned parameter nodes described in PS-003.
- No per-layer or per-channel shader datablocks. Shared logic is a static
  library group (`compiler/library.py`), either generated in Python or
  appended from `library2.blend` (PS-002).
- Every port is an opportunity to simplify. Tickets note what v2 did badly
  and what the v3 version should do instead.
- Blender 4.2 LTS and newer are supported (`blender_version_min` in the
  manifest). Version-dependent API goes through `common.is_newer_than`
  with a fallback for the older path. CI runs the tests against the
  latest patch of every supported series plus the next alpha (PS-080), so
  a ticket is not done until the matrix is green.
- Icon names go through a fallback lookup instead of straight to Blender.
  Blender renames icons between versions and forks such as Bforartists
  ship their own set (it has no `VIEW_LOCKED`), and an unknown name raises
  in draw, or in `register_class` for a node's `bl_icon`. Layout calls
  pass `**common.icon_kwargs(new, old, ...)`, node and node tree classes
  set `bl_icon = common.blender_icon(new, old, ...)`, and only
  `icon='NONE'` and `WorkSpaceTool.bl_icon` file names stay literal.
  `tests/test_icons.py` fails on any other literal (PS-080).
- UI strings are English literals that can be translated later without
  restructuring (see Deferred: Multilingual UI).
- Extension platform rules, as given by the Blender extensions review
  team and by the Extensions terms of service. No `threading`: use
  `bpy.app.timers`, `subprocess` or `multiprocessing` for background
  work. Register keymap entries in the addon keyconfig only and never
  remove default or user entries. Shadowing counts as removing: the addon
  keyconfig is matched before the default one, so binding a combination
  Blender already uses in the same keymap takes it away from the user.
  Check `scripts/presets/keyconfig/keymap_data/blender_default.py` before
  adding an item; background Blender reports an empty default keyconfig
  and cannot answer this. No adverts, social links, funding or update
  checkers in the UI (terms 6.1-6.3). No calls into third-party software
  and no files written outside storage the extension owns (terms 3.9,
  5.2). No network access without the manifest permission and a
  `bpy.app.online_access` check (terms 4.1-4.5). Build the package with
  `blender --command extension build` so development files are excluded.
  No leftover code: unused imports, dead modules and stale preferences
  are removed. CI enforces what it can with ruff and the strict
  `blender-extension-builder` validator on every push (PS-080); the
  keymap and third-party rules are read, not tested, except that
  `tests/test_keymaps_ui.py` checks the add-on's Ctrl+D against the
  running build's default keyconfig in a window (PS-093).

## Milestones

| Milestone | Goal | Tickets |
|---|---|---|
| M0 Playable demo | A thin slice of M1 that can be opened and painted with. See "Playable demo" below | 001 010 011 012 013 019 029 030 033 034 035 056 060 061 080 (parts, see below) |
| M1 Paint again | Image, solid and folder layers with clipping and the full sidebar UI, templates, painting workflow | 001 002 003 008 009 010 011 012 013 014 019 020 021 029 030 031 032 033 034 035 036 040 041 042 056 060 061 062 080 |
| M2 All layer types | Remaining layer types, masks, linked layers, clipboard, actions, vector channels | 004 005 006 015 016 017 018 022 023 024 025 026 027 028 037 038 039 063 064 065 066 |
| M3 Tools | GPU image filters, quick edit, export, channel bake, performance | 007 043 050 051 052 053 054 055 057 081 |
| M3b Selection tools | Spikes, pixel undo, selection, transform and fill tools, 3D view first | 090 091 092 093 094 095 096 |
| M4 Migration | Load v2 files into v3 | 070 071 072 082 |

## Playable demo

The first goal is a demo that can be opened and painted with, built as
a vertical slice through M1 rather than by finishing whole epics.
Remaining parts of each ticket stay open under M1.

Demo bar: on a cube, Setup Paint System; add image layers, solid layers
and a folder; reorder them, set blend mode, opacity and clipping; enter
paint mode from the panel; pick brush and colour in the sidebar; paint
and see the layers composite correctly; save, reopen and undo without
breaking anything. Green on every Blender in the CI matrix (4.2+).

Status: all seven slices are done and `tests/test_demo_flow.py` checks
the demo bar on every Blender in the matrix. What stays open is listed
in each ticket's Status section and under Deferred below.

Slices, in order. Each ends playable, is committed, and keeps CI green.

| Slice | Scope | Tickets (demo part only) |
|---|---|---|
| 1 Smoke the existing loop (done) | Paint updates the viewport through the compiled group; pixels survive save and reload; undo after add/remove layer leaves a valid artifact. Fix what breaks. Found and fixed: compiles on a timer left stale artifacts after undo (now synchronous), node `name` shadowing broke layer lookup before 5.2, msgbus rename subscription lost on file load. `tests/test_smoke_loop.py` | – |
| 2 Correct compositing (done) | Porter-Duff blend groups with the Clip input (W3C source-over unclipped, source-atop clipped); pixel sampling helper in `tests/harness.py`; `tests/test_blend.py` checks every mode over transparent, half-transparent and opaque backdrops, clipped and not. Found: the ticket's clip formula counted the backdrop alpha twice, and v2's Post Mix overshoots for clipped layers over partly transparent pixels. v2 comparison fixtures come later | 001 |
| 3 Stack model (done) | `stack()` walk, `nodetree/stack_ops.py`, folder node; `PSContext`; `lock_layer` and `lock_alpha` (no warnings API yet). `tests/test_stack.py`. Found: inside `NodeTree.update` Blender drops links the callback creates, has not validated the edit's new links yet, and builds no sockets for new group nodes. Node editor edits now stamp the artifact pending and build on the next tick (undo-safe, covered in the smoke test); alpha follows colour in the compiler and stack edits repair the links | 010, 030, 019 |
| 4 Layers panel (done) | Row view model with indentation and folder collapse; move up/down with folders; registry and Add Layer menu for Folder, Image, Solid; layer settings for blend, opacity and image. `tests/test_layers.py`. Found: the list can sit on `tree.nodes` with a filter and a get/set index, so no mirror collection is needed; v2 offered a `SKIP` that did nothing at the bottom of a folder, and without it a lone `MOVE_ADJACENT` would jump out of every folder without asking (now `MOVE_OUT_BOTTOM` is offered too); icon names differ across 4.2 and 5.x and an unknown one raises in draw | 011, 012, 029, 034, 035 |
| 5 Clipping (done) | `is_clip`, compile-time clip runs, list toggle and icon. Acceptance uses a self-contained pixel test instead of the v2 comparison. `tests/test_clip.py`. Found: the artifact builder keeps the value of an input the IR stops setting, so a layer unclipped after a compile stayed clipped until its `Clip` input was set on every compile; the base's blend mode now applies to the whole run, which v2 never did; a cached base cannot stand in for a run and compiles live | 013 |
| 6 Painting workflow (done) | Active layer sets canvas and UV map (also from the node editor); toggle paint mode (no channel isolate); brush and colour panels on both the 4.2 brush and 4.3+ asset brush paths; image save and pack policy. 060 and 061 with `tests/test_painting.py`; 033 on Blender's own paint panel helpers; 056 with `tests/test_images.py`. Found: painted images that were not created by the addon (loaded from disk, or generated by hand) were neither saved nor packed and lost their strokes on reopen; clicking a node in the node editor reports nothing (no update, handler or message bus notification), so a draw callback notices and a timer syncs; a material slot change is no depsgraph update, only a message bus notification, which v2 missed; operators called from a script run no depsgraph handlers until the depsgraph is evaluated; an open menu holds off node editor redraws; the colour jitter helper is `color_jitter_panel` in 4.5 and `draw_color_jitter_panel` from 5.0, and 4.2 has neither, so the Color section calls `draw_color_settings`, which handles all three | 060, 061, 033, 056 |
| 7 Demo acceptance test (done) | `tests/test_demo_flow.py` scripts the demo bar: setup, layers, folder, move, blend mode, opacity and clipping, paint mode, pixels written into the canvas and checked at texels of the compiled output against the PS-001 coverage rule, save, reopen, undo, redo and a second save. Runs in the CI matrix. The coverage helpers moved from `test_clip.py` into `tests/harness.py`. Found: `bake_group` left its bake plane as the active object, so an operator run after a pixel check acted on the plane; it now gives the selection back | 080 |

Deferred until after the demo: library imports (002), parameter nodes
(003), coordinate mixin and non-UV coordinates (008), UDIM (009),
pass-through folders (014), header presets and groups popover (031),
the full channels panel (032), colour popover and HUD (036), multiple
groups and templates (040-042), colour history (062).

## Epic A. Compositing core and compiler extensions

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-001](tickets/PS-001-blend-math.md) | Layer blend math: W3C compositing in generated library groups | M | – |
| [PS-002](tickets/PS-002-library-blend-import.md) | Append groups from `library2.blend` through `compiler/library.py` | S | – |
| [PS-003](tickets/PS-003-artifact-owned-parameter-nodes.md) | Artifact-owned parameter nodes (curves, ramps, texture parameters) | M | – |
| [PS-004](tickets/PS-004-ir-drivers.md) | IR support for drivers | S | – |
| [PS-005](tickets/PS-005-channel-options.md) | Channel options: alpha socket, colour space, factor range, defaults | M | – |
| [PS-006](tickets/PS-006-vector-channels.md) | Vector channels: normal and tangent space transforms | M | 002 005 |
| [PS-007](tickets/PS-007-channel-bake.md) | Channel-level bake ("Use Baked") | M | 005 |
| [PS-008](tickets/PS-008-coordinate-mixin.md) | Coordinate and transform mixin for texture-driven layers | L | 002 |
| [PS-009](tickets/PS-009-ps-uvmap-and-udim.md) | `PS_UVMap` auto UV and UDIM tile detection | S | – |

## Epic B. Layer stack model

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-010](tickets/PS-010-folder-node-and-stack-walk.md) | Folder layer node and hierarchical stack walk | L | – |
| [PS-011](tickets/PS-011-layer-rows-view-model.md) | Layer rows view model for the UIList | M | 010 |
| [PS-012](tickets/PS-012-move-with-folder-options.md) | Move up/down with folder movement options | M | 010 011 |
| [PS-013](tickets/PS-013-clipping-layers.md) | Clipping layers | M | 001 010 |
| [PS-014](tickets/PS-014-passthrough-folders.md) | Pass-through folders | S | 010 |
| [PS-015](tickets/PS-015-layer-masks.md) | Layer masks | M | 008 |
| [PS-016](tickets/PS-016-linked-layers.md) | Linked layers | M | 010 |
| [PS-017](tickets/PS-017-clipboard.md) | Clipboard: copy, copy all, paste, paste linked, unlink | M | 010 016 |
| [PS-018](tickets/PS-018-merge-duplicate-convert.md) | Merge up/down, duplicate, convert to image layer, transfer UV | M | 010 020 |
| [PS-019](tickets/PS-019-base-layer-flags.md) | Base layer flags and warnings API | S | – |

## Epic C. Layer types

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-020](tickets/PS-020-image-layer.md) | Image layer full port | L | 008 009 |
| [PS-021](tickets/PS-021-solid-color-layer.md) | Solid colour layer parity | S | – |
| [PS-022](tickets/PS-022-attribute-layer.md) | Attribute layer | S | – |
| [PS-023](tickets/PS-023-adjustment-layer.md) | Adjustment layer | M | 003 013 |
| [PS-024](tickets/PS-024-gradient-layer.md) | Gradient layer and empty gizmo | L | 003 004 064 |
| [PS-025](tickets/PS-025-random-color-layer.md) | Random colour layer | S | – |
| [PS-026](tickets/PS-026-texture-layer-redesign.md) | Texture layer redesign | M | 003 008 |
| [PS-027](tickets/PS-027-geometry-layer.md) | Geometry layer | S | 003 |
| [PS-028](tickets/PS-028-custom-node-group-layer-redesign.md) | Custom node group layer redesign | L | – |
| [PS-029](tickets/PS-029-layer-type-registry.md) | Layer type registry: icons, Add Layer menu, operators | M | – |

## Epic D. 3D-view UI parity

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-030](tickets/PS-030-ps-context.md) | `PSContext` resolver | S | – |
| [PS-031](tickets/PS-031-main-panel.md) | Main panel, header presets, groups popover, material settings | M | 030 040 |
| [PS-032](tickets/PS-032-channels-panel.md) | Channels sub-panel, settings and add-channel menu | M | 005 030 |
| [PS-033](tickets/PS-033-brush-and-color-panels.md) | Brush and Colour sub-panels | M | 030 061 062 |
| [PS-034](tickets/PS-034-layers-panel.md) | Layers panel: list, sidebar, menus, warnings | L | 011 012 029 030 |
| [PS-035](tickets/PS-035-layer-settings-subpanels.md) | Layer Settings sub-panels | L | 034 |
| [PS-036](tickets/PS-036-color-popover-and-hud.md) | Colour popover, floating colour HUD, keymaps, brush tooltips | L | 033 |
| [PS-037](tickets/PS-037-node-editor-and-material-panels.md) | Node editor panel, material properties injection, inspect layer | S | 030 |
| [PS-038](tickets/PS-038-quick-tools-panels.md) | Quick Tools panels | S | 030 |
| [PS-039](tickets/PS-039-preferences-panel.md) | Preferences panel parity | S | – |

## Epic E. Groups and templates

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-040](tickets/PS-040-multiple-groups-per-material.md) | Multiple Paint System groups per material | M | – |
| [PS-041](tickets/PS-041-group-templates.md) | Group templates and channel templates | L | 040 005 |
| [PS-042](tickets/PS-042-delete-and-move-group.md) | Delete group (dissolve template nodes) and move group | S | 041 |
| [PS-043](tickets/PS-043-make-tree-single-user.md) | Make tree single user | S | 040 |

## Epic F. Image tools, quick edit, export

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-050](tickets/PS-050-gpu-filter-framework.md) | GPU image filter framework (done, no UDIM) | L | 090 |
| [PS-051](tickets/PS-051-gpu-blur-sharpen.md) | Gaussian blur and sharpen on the GPU (blur done as a filter layer kind) | M | 050 |
| [PS-052](tickets/PS-052-invert-fill-clear-resize.md) | Invert, fill, clear (done), resize | S | 050 |
| [PS-053](tickets/PS-053-gpu-brush-painter.md) | Brush painter on the GPU | L | 050 009 |
| [PS-054](tickets/PS-054-quick-edit.md) | Toggle image editor | S | 020 |
| [PS-055](tickets/PS-055-export.md) | Export image and export all | S | 007 |
| [PS-056](tickets/PS-056-image-save-policy.md) | Image save and pack policy | S | – |
| [PS-057](tickets/PS-057-filter-layer.md) | Filter layer: a filter that stays editable | L | 050 051 083 084 |

## Epic G. Painting workflow and handlers

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-060](tickets/PS-060-active-layer-sync.md) | Active layer to canvas, UV map and brush sync | M | 019 |
| [PS-061](tickets/PS-061-toggle-paint-mode-isolate-channel.md) | Toggle paint mode and isolate channel | M | 040 |
| [PS-062](tickets/PS-062-color-history-hsv.md) | Colour history, HSV and hex colour, unified colour sync | S | – |
| [PS-063](tickets/PS-063-layer-actions.md) | Layer actions (frame and marker based visibility) | M | – |
| [PS-064](tickets/PS-064-empty-object-gizmos.md) | Empty object gizmos and the Paint System collection | M | – |
| [PS-065](tickets/PS-065-projection-view-ops.md) | Projection view operators | S | 008 |
| [PS-066](tickets/PS-066-multi-material-operator-base.md) | Multi-object and multi-material operator base | S | 030 |
| [PS-067](tickets/PS-067-eraser-brush.md) | Eraser brush in the sidebar (deferred) | S | 033 |

## Epic H. Versioning

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-070](tickets/PS-070-migrate-v2-data.md) | Migrate v2 material data to v3 trees | L | all of Epic B and C |
| [PS-071](tickets/PS-071-tree-version-migrations.md) | Tree version migration framework | S | – |
| [PS-072](tickets/PS-072-legacy-v1-data.md) | Legacy v1 data | S | 070 |

## Epic I. Infrastructure

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-080](tickets/PS-080-tests-and-ci.md) | Per-feature tests and CI | M | – |
| [PS-081](tickets/PS-081-performance-budget.md) | Performance budget and profiling (done) | M | 010 |
| [PS-082](tickets/PS-082-grease-pencil.md) | Grease Pencil support (deferred) | L | 034 |
| [PS-083](tickets/PS-083-fingerprint-recreated-datablock.md) | Stale artifact when a datablock is recreated under a deleted one's name (deferred) | S | – |
| [PS-084](tickets/PS-084-fingerprint-stale-after-rename.md) | Stale stored fingerprint after a datablock rename (deferred) | S | – |

## Epic J. Selection, transform and fill tools

Selections and transforms are built on the compiler, the same way layers
are:

- A selection is a list of operations stored in the document. A GPU
  rasteriser derives a soft mask from it, as the compiler derives the
  material from the tree, so selection undo and saving come from Blender.
- A transform is a floating layer drawn by the compiled material. Dragging
  handles changes a view-layer attribute the material reads, so the
  preview runs at shader speed; Enter writes pixels once.
- Work done in the 3D view reaches the image through a cached texel
  position map (world position per texel). Floods run in screen space.
  No tool needs UV seam or mesh adjacency code.
- Handles are custom Python gizmos; overlays are draw handlers.
- The few operations that write pixels (commit, fill, filters, cut)
  register with Blender's own image undo; there is no separate stack.

Decisions of 2026-09-16:

- Enter merges a transform into its layer (no new layer is left behind).
- Selections are soft, with feather and an anti-alias toggle.
- The 3D view in texture paint mode gets the tools first; the image editor
  follows with the same operators.
- After the PS-096 spikes: the transform handles are a custom
  `bpy.types.Gizmo` (the built-in cage ignores Ctrl and Shift and misses
  corners in the image editor); a commit is one image undo step and the
  floating state is session state, not document data; surface moves use a
  UV lookup decal at 2x density; write-once images are packed.

Pixel Art Studio (`~/Downloads/pixel_art_studio_blender_v1.2.1-0`,
GPL-3.0-or-later) sets the bar for how the tools should feel. None of its
data structures or algorithms are ported.

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-096](tickets/PS-096-selection-transform-spikes.md) | Spikes for selection and transform | S | – |
| [PS-090](tickets/PS-090-pixel-undo-stack.md) | Pixel undo for scripted image edits | S | 096 |
| [PS-092](tickets/PS-092-texel-position-map.md) | Texel position map | M | 096 |
| [PS-091](tickets/PS-091-selection-model-and-overlays.md) | Selection model and overlays. Slices 1 to 4 and milestone 2 surface keys done: model, mask, stroke clipping, overlays, view selections | L | 050 092 096 |
| [PS-093](tickets/PS-093-selection-tools.md) | Selection tools: Rectangle, Ellipse and Lasso Selection in the 3D view. Built; the rest moved to PS-097 | M | 091 092 |
| [PS-094](tickets/PS-094-transform-tool.md) | Transform tool and pixel clipboard (deferred) | L | 008 081 090 091 092 |
| [PS-095](tickets/PS-095-fill-tool.md) | Fill tool | M | 050 090 091 092 |
| [PS-097](tickets/PS-097-more-selection-tools.md) | More selection tools: polygon lasso, magic wand, faces, image editor (deferred) | L | 090 091 092 093 |

## Deferred

- Multilingual UI. The v3 UI ships in English only for now, but every
  ticket keeps strings translatable so a later `bpy.app.translations`
  dictionary can cover them: pass literal `text=` arguments to layout
  calls rather than building labels by concatenation or f-strings, keep
  operator/property `name` and `description` as plain literals, and
  route dynamic labels through a single helper when they are introduced.
- More selection tools (PS-097): the polygon lasso, the magic wand,
  select by face, UV island or material, and the image editor selection
  tools with clipping there. Nice to have and not scheduled; split out of
  PS-093 on 2026-09-17, once Rectangle, Ellipse and Lasso Selection were
  built in the 3D view.
- Transform tool and pixel clipboard (PS-094). Deferred on 2026-09-18 in
  favour of the selection actions and the GPU filters; its design stands
  and nothing else depends on it.
- Eraser brush in the sidebar (PS-067), for artists who do not know to
  set a brush to Erase Alpha. Nice to have and not scheduled. The user
  creates the brush and puts it in the library `.blend`; ask them to do
  that before the ticket is started.
- Two fingerprint bugs found while profiling PS-081 on 2026-09-18, both
  older than that work and both from the same assumption: the IR
  identifies a datablock by `name_full`, and nothing revalidates the
  artifact's ID pointers once a fingerprint matches. PS-083: deleting an
  image and creating another under the freed name leaves the artifact's
  Texture Image empty, because the fingerprint matches the one stored
  before the deletion and no compile runs. PS-084: renaming a datablock a
  layer points at changes the fingerprint without triggering a compile
  (the tree's own name is already covered by a msgbus subscription), so
  the stored one is stale until the next edit and a baked layer loses its
  cache. Both want the same fix and are cheaper done together; not
  scheduled, since neither misrenders a normal edit.

## Not ported

- Legacy UI mode (`use_legacy_ui`). The v3 sidebar implements only the modern layout.
- `SHADER` layer type. It is not in `LAYER_TYPE_ENUM` and has no graph builder in v2.
- `BLANK` layer type. v2 used it as the carrier for linked layers; v3 has a dedicated node (PS-016).
- `PS Camera Plane Old` geometry node group from `library2.blend`.
