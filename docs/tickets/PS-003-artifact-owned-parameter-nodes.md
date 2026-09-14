# PS-003 Artifact-owned parameter nodes

Epic A. Size M. Milestone M1. Core prerequisite for adjustment, gradient,
texture and geometry layers.

## Problem

Some Blender node state cannot be stored as a property on a Paint System
node: `CurveMapping` on RGB Curves, `ColorRamp` on Colour Ramp, the many
enum and socket values on procedural texture nodes. v2 simply exposed the
live shader node in the UI (`template_node_inputs`, `template_curve_mapping`,
`template_color_ramp`, `panels/layers_panels.py:197-203, 334-366`) and
relied on `NodeTreeBuilder.compile` preserving user edits unless a
`.force` key was set (`graph/nodetree_builder.py:454-461, 949+`).

v3 regenerates the artifact from the IR, so a value edited on the artifact
node would be overwritten on the next compile.

## Design

Introduce an IR flag `IRNode.user_owned = True`:

- The builder creates the node on first apply and links it, but never
  writes properties or unlinked socket values into it on later applies.
  The node's identifier stays `"<uuid>:<role>"`, so it survives diffs.
- `CompileContext.emit_param_node(node, role, bl_idname, **initial)` creates
  such a node with initial values only used on creation.
- `CompileContext.param_node(node, role)` returns the live artifact node
  (or `None` before the first compile) so panels can draw it with the
  Blender templates. Panels must tolerate `None` and draw a "Compile to
  edit" placeholder.
- Hashing: `node_state`/`subtree_hash` must include the user-owned node's
  state. Add `serialize_artifact_node(node)` in `compiler/core.py` that
  serialises inputs' `default_value`, enum properties, `color_ramp`
  elements and `mapping.curves` points. The layer's `hash_parts` returns
  this so cache invalidation (PS-007, node cache) notices curve edits.
- Editing a user-owned node does not fire any Paint System update, so the
  cache-stale indicator refreshes on the next compile trigger. Document
  this; it is acceptable because the artifact is live in the viewport
  regardless.
- Copying a Paint System node (`copy()` gives a new uuid) creates a fresh
  parameter node with defaults. To preserve values, `PaintSystemBaseNode.copy`
  records `_copied_from_uuid`; the compiler copies the state from the old
  parameter node when creating the new one, if it still exists.
- Undo: artifact nodes are undone with the file like any datablock, so no
  extra handling.

## Acceptance

- Test: emit an RGB Curves param node, move a curve point on the artifact
  node, recompile, point is preserved; `subtree_hash` changed.
- Test: duplicate the layer, parameter state is copied.
- Test: force recompile (`compile_tree(force=True)`) keeps user-owned
  values.

## Consequence

User-owned nodes are the only exception to the "artifact is derived"
rule. Keep the list of roles that use it short and document each in
`docs/ARCHITECTURE.md`.
