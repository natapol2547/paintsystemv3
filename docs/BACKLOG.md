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
- UI strings are English literals that can be translated later without
  restructuring (see Deferred: Multilingual UI).
- Extension platform rules, as given by the Blender extensions review
  team. No `threading`: use `bpy.app.timers`, `subprocess` or
  `multiprocessing` for background work. Register keymap entries in the
  addon keyconfig only and never remove default or user entries. No
  adverts, social links, funding or update checkers in the UI. No calls
  into third-party software. Build the package with
  `blender --command extension build` so development files are excluded.
  No leftover code: unused imports, dead modules and stale preferences
  are removed. CI enforces these with ruff and the strict
  `blender-extension-builder` validator on every push (PS-080).

## Milestones

| Milestone | Goal | Tickets |
|---|---|---|
| M1 Paint again | Image, solid and folder layers with clipping and the full sidebar UI, templates, painting workflow | 001 002 003 008 009 010 011 012 013 014 019 020 021 029 030 031 032 033 034 035 036 040 041 042 056 060 061 062 080 |
| M2 All layer types | Remaining layer types, masks, linked layers, clipboard, actions, vector channels | 004 005 006 015 016 017 018 022 023 024 025 026 027 028 037 038 039 063 064 065 066 |
| M3 Tools | GPU image filters, quick edit, export, channel bake, performance | 007 043 050 051 052 053 054 055 081 |
| M3b Selection tools | Pixel undo, selection, transform and fill tools in both editors | 090 091 092 093 094 095 |
| M4 Migration | Load v2 files into v3 | 070 071 072 082 |

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
| [PS-036](tickets/PS-036-rmb-popover-keymaps.md) | Shift+RMB popover, keymaps, brush tooltips | S | 033 |
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
| [PS-050](tickets/PS-050-gpu-filter-framework.md) | GPU image filter framework | L | 090 |
| [PS-051](tickets/PS-051-gpu-blur-sharpen.md) | Gaussian blur and sharpen on the GPU | M | 050 |
| [PS-052](tickets/PS-052-invert-fill-clear-resize.md) | Invert, fill, clear, resize | S | 050 |
| [PS-053](tickets/PS-053-gpu-brush-painter.md) | Brush painter on the GPU | L | 050 009 |
| [PS-054](tickets/PS-054-quick-edit.md) | Quick edit (external editor) and toggle image editor | M | 020 |
| [PS-055](tickets/PS-055-export.md) | Export image and export all | S | 007 |
| [PS-056](tickets/PS-056-image-save-policy.md) | Image save and pack policy | S | – |

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
| [PS-081](tickets/PS-081-performance-budget.md) | Performance budget and profiling | M | 010 |
| [PS-082](tickets/PS-082-grease-pencil.md) | Grease Pencil support (deferred) | L | 034 |

## Epic J. Selection, transform and fill tools

Inspired by Pixel Art Studio (`~/Downloads/pixel_art_studio_blender_v1.2.1-0`,
GPL-3.0-or-later, same licence as Paint System). Its numpy raster core
and integer-isometry seam engine are not ported; its tool design, undo
model, fill and portal table are. Tickets cite its files by
`file:line`.

| ID | Title | Size | Depends on |
|---|---|---|---|
| [PS-090](tickets/PS-090-pixel-undo-stack.md) | Pixel undo stack for scripted image edits | M | – |
| [PS-091](tickets/PS-091-selection-model-and-overlays.md) | Selection mask model and overlays | M | 090 |
| [PS-092](tickets/PS-092-screen-to-uv-projection-pass.md) | Screen-to-UV projection pass | M | 050 |
| [PS-093](tickets/PS-093-selection-tools.md) | Selection tools: box, ellipse, lasso, wand, by face | M | 091 092 095 |
| [PS-094](tickets/PS-094-transform-tool.md) | Transform and move tool, pixel clipboard | L | 050 091 092 |
| [PS-095](tickets/PS-095-fill-tool.md) | Fill tool with seam-aware flood | M | 050 091 |

## Deferred

- Multilingual UI. The v3 UI ships in English only for now, but every
  ticket keeps strings translatable so a later `bpy.app.translations`
  dictionary can cover them: pass literal `text=` arguments to layout
  calls rather than building labels by concatenation or f-strings, keep
  operator/property `name` and `description` as plain literals, and
  route dynamic labels through a single helper when they are introduced.

## Not ported

- Legacy UI mode (`use_legacy_ui`). The v3 sidebar implements only the modern layout.
- `SHADER` layer type. It is not in `LAYER_TYPE_ENUM` and has no graph builder in v2.
- `BLANK` layer type. v2 used it as the carrier for linked layers; v3 has a dedicated node (PS-016).
- `PS Camera Plane Old` geometry node group from `library2.blend`.
