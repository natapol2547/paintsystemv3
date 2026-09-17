# PS-084 Renaming a datablock leaves the stored fingerprint stale (deferred)

Epic I. Size S. Milestone M3 or later. Found while profiling PS-081 on
2026-09-18; older than that work.

## What happens

Renaming an image a layer uses, or renaming the Paint System tree itself,
changes what a compile would fingerprint but triggers no compile. The
fingerprint stored on the artifact keeps describing the old name until
the next edit, so until then:

- the artifact reports as out of date although it is correct - the shader
  holds datablock pointers, not names - and `core.tree_updated` therefore
  stamps the tree pending and schedules a compile on the next node editor
  touch;
- the next compile runs a full `IR.apply` that writes nothing (measured:
  `BuildStats(nodes_created=0, values_written=0, links_created=0,
  links_removed=0)`), paying a builder pass for a rename the shader never
  cared about;
- a baked layer loses its cache. `CompileContext.subtree_hash` serialises
  the same way, so the stored `cache_hash` stops matching and the layer
  compiles live until it is baked again.

Nothing renders wrong, which is why this is a ticket and not a fix in
PS-081.

## Why

`IR._serialize` identifies a datablock as `["id", <type>, name_full]` and
`IR.meta['tree']` is the tree's name (`compiler/ir.py`,
`compiler/core.py`), so a name is part of the hash. A rename touches no
node and no link, so `NodeTree.update` does not run and nothing calls
`mark_dirty`.

## Reproduce

Confirmed on Blender 5.2.1 LTS.

1. Set up a tree with an image layer, compile.
2. `image.name = "something else"` (or `tree.name = ...`).
3. No compile runs, and `artifact_fingerprint(tree)` no longer equals
   `build_ir(tree).fingerprint()`.
4. The next compile writes zero values but does the whole builder pass.

## Fix sketch

- The narrow fix is to keep names out of the payload where the check
  they serve is identity: what the fingerprint needs of a datablock is
  "the same one as last time", not its name. A pointer cannot be hashed
  across sessions, so the payload would carry a per-build index of the
  IR's ID values and the artifact would hold the resolved pointers to
  compare against - the same walk PS-083 needs, which is why the two are
  cheaper done together than apart.
- The blunt fix is a `bpy.msgbus` subscription on the `name` of the types
  the payload can embed (`bpy.types.Image`, `bpy.types.NodeTree`) that
  marks every Paint System tree dirty. Check first that msgbus resolves
  those keys; it re-registers on file load like the subscriptions in
  `handlers/paint_handlers.py`, and it turns a rename into a full
  recompile, which is what the stale fingerprint costs anyway.
- The first option changes the payload's shape, so every `cache_hash`
  saved by `compiler/bake.py` stops matching once and the layers holding
  them rebake. `hash_payload` itself must stay as it is either way.

## Acceptance

- Renaming an image a layer uses, or the tree, leaves
  `artifact_fingerprint(tree) == build_ir(tree).fingerprint()`.
- A baked layer stays cached across a rename of its image.
- Not scheduled.
