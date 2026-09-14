# PS-043 Make tree single user

Epic E. Size S. Milestone M3.

## v2 behaviour

`duplicate_paint_system_data` (`operators/utils_operators.py:288-353`),
surfaced as "Fix Data Duplication" when a material is multi-user:
snapshots material-side links by socket name, allocates fresh node trees
for the group and channels, duplicates every non-linked layer, rebuilds,
re-points the group nodes and reconnects.

## v3 design

The tree is a datablock, so this is mostly `tree.copy()`:

- `paint_system.make_tree_single_user`: copy the tree (nodes copy with
  new uuids via `PaintSystemBaseNode.copy`), copy managed images when
  `duplicate_images` is checked (default on, v2 shared images), copy
  parameter-node state (PS-003 copy path), point the material's slot and
  group node at the new tree, compile.
- The artifact is not copied; `ensure_artifact` creates a new one because
  the uuid differs.

## Acceptance

- Two objects with a shared material: after making single user on one,
  painting on it does not affect the other; the artifact count grows by
  one.
