# PS-070 Migrate v2 material data to v3 trees

Epic H. Size L. Milestone M4.

## v2 data

`Material.ps_mat_data.groups[]` -> `channels[]` -> `layers[]` (flat, with
`id`, `parent_id`, `order`, `uid`), plus global layer data resolved
through `get_layer_data()`; per-layer node trees `.PS <name> (<uid>)`,
per-channel trees `.PS <name>`, the group node tree, library groups,
`MarkerAction` collections, masks (unused), clipboard, `ps_scene_data`
colour state. Versioning history in `paintsystem/versioning.py` and
`operators/versioning_operators.py`.

## v3 design

`migration/v2.py::migrate_material(material) -> Report`, run from
`on_load_post` when `ps_mat_data` exists with groups, and exposed as
`paint_system.migrate_v2` for manual retries:

1. For each group create a tree (`tree.template` from `group.template`,
   coord defaults), a slot (PS-040), and channels with all PS-005/006
   options.
2. For each channel walk `flatten_hierarchy` bottom-up and create nodes by
   `LAYER_TYPE_ENUM` -> registry `ps_type` (PS-029), copying properties by
   name, images by pointer, coord props into the mixin, `is_clip`,
   `enabled`, `lock_*`, `is_expanded`, `actions`, `external_image`.
   Folders get their children inserted into the content chain; linked
   layers become linked nodes (PS-016) resolved by uid across materials
   after all trees exist.
3. Parameter-node state (curves, ramps, texture settings, custom group
   inputs) is read from the old per-layer node tree by node name
   (`source`, `map_range`, ...) and applied to the new artifact nodes
   after the first compile (`ctx.param_node`).
4. Rewire the material: snapshot links from the old group node by socket
   name, replace it with the artifact instance, reconnect. Old v2
   template nodes (Mix Shader, Transparent, Shader to RGB) are kept and
   tagged for PS-042.
5. Remove old per-layer and per-channel trees and the v2 group tree when
   they have no users; leave library groups for other files.
6. Clear `ps_mat_data` groups and stamp `material.paint_system.migrated_from = 2`.
   Unsupported cases are collected into the report and shown in a dialog
   like v2's `update_paint_system_data`.

Keep the v2 PropertyGroups registered read-only in v3 (`legacy/v2_props.py`)
so the data is readable; they are never written.

## Acceptance

- Fixture files (create with v2 in the old repo): every layer type,
  nested folders, clipped stack, linked layers across two materials,
  vector channel, baked channel, actions. After load in v3 the sidebar
  shows the same stack and a render of each fixture matches the v2
  render within 1/255, except in texels where a non-MIX layer sits over
  a transparent backdrop (expected difference, see PS-001).
- Migration is idempotent and never runs twice on the same material.
