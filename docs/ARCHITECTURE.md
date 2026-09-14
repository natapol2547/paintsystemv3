# Architecture

Paint System v3 treats the custom node tree as the document and the shader
node group as a build artifact. Data flows one way:

```
PaintSystemNodeTree  --compile-->  IR  --NodeTreeBuilder-->  ShaderNodeTree (tree.compiled)
```

## Ownership rules

- A `PaintSystemNodeTree` owns exactly one dynamic shader datablock,
  `tree.compiled`. It is tagged with the tree's uuid (`ps_owner`) so a copied
  tree gets its own artifact instead of sharing one.
- Nodes own no shader datablocks. Image layers own images (user data), and
  that is all.
- Shared logic lives in static library groups (`compiler/library.py`), built
  in Python on first use and versioned by `LIBRARY_VERSION`. Per-layer state
  is passed through sockets on the instancing group node.
- A nested `PaintSystemGroupLayerNode` instances the wrapped tree's own
  artifact. This is the only artifact-to-artifact reference and it mirrors a
  real datablock relationship.

## Compile pipeline (`compiler/core.py`)

1. `normalize_tree` repairs invariants without firing update callbacks:
   unique node uuids, io nodes present, channel uuids.
2. `build_ir` walks upstream from the active Group Output in topological
   order and calls `node.emit(ctx)` on each node. Nodes append to the IR and
   register which IR sockets provide their outputs; downstream nodes link to
   those via `ctx.upstream(socket)` / `ctx.connect_input(...)`.
3. `IR.fingerprint()` hashes the result. If it differs from
   `tree.compiled_hash`, `IR.apply` patches the artifact through the
   diff-based `NodeTreeBuilder` (identifiers are `"<node uuid>:<role>"`).

Because the compiler never writes back into the Paint System tree there are
no recursion guards. Undo, redo and file load simply mark everything dirty;
unchanged trees are skipped by the fingerprint check.

## Triggers

- `NodeTree.update`, `Node.update` and every property `update=` call
  `mark_dirty(tree)`. A `bpy.app.timers` callback coalesces them into one
  `compile_tree` per tree.
- `undo_post`, `redo_post` and `load_post` mark all trees dirty.
- `depsgraph_update_post` initialises trees created from the node editor
  header, which has no init hook.

## Hybrid caching (`compiler/bake.py`)

Any layer node can be cached. `CompileContext.subtree_hash(node)` hashes the
node's properties, unlinked socket values and, recursively, its upstream.
When `cache_enabled` and `cache_hash == subtree_hash`, the compiler emits a
single Image Texture for the node and does not walk its upstream. When the
hash mismatches the live graph is emitted and `cache_stale` is set.

Baking builds a temporary group with the node's live Color/Alpha outputs,
routes it through an Emission shader in a throwaway material, Cycles-bakes
color and alpha into the cache image, then stores the subtree hash.

## Testing

```
tests/run.sh          # headless smoke test against Blender 5.2 LTS
```
