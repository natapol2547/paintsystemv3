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
3. `IR.fingerprint()` hashes the result. If it differs from the
   fingerprint stored on the artifact (`ps_fingerprint`), `IR.apply`
   patches the artifact through the diff-based `NodeTreeBuilder`
   (identifiers are `"<node uuid>:<role>"`). The fingerprint lives on the
   artifact so it always describes the nodes next to it.

The compiler never writes back into the document beyond the normalize
repairs, so a nested compile request only needs a re-entrancy flag.

## Triggers

Compiles run synchronously, inside the edit that caused them, so the
artifact is part of the same undo step. This is a hard rule, not a
performance choice. Memfile undo takes every datablock that is identical
in two consecutive steps straight from memory instead of re-reading it.
An artifact patched after its step was pushed (from a timer, for example)
survives the undo with nodes for layers that no longer exist and pointers
to images the undo just freed. `tests/test_smoke_loop.py` covers this.

- `NodeTree.update`, `Node.update` and every property `update=` call
  `mark_dirty(tree)`, which compiles immediately.
- Anything that makes several edits in a row (operators, `insert_layer_node`,
  `create_channel`, channel socket sync) wraps them in `suspend_compile`;
  the outermost exit compiles once.
- `load_pre`, `undo_pre` and `redo_pre` block compiles, because Blender
  calls `NodeTree.update` on half-restored data. `load_post` (which runs
  before the file's initial undo step is recorded), `undo_post` and
  `redo_post` unblock and compile every tree; with consistent steps that is
  a fingerprint check per tree.
- While `bpy.data` is restricted (addon registration) or writing is
  forbidden (drawing), a `bpy.app.timers` callback compiles instead. That is
  the only path that runs outside an undo step.
- `depsgraph_update_post` initialises trees created from the node editor
  header, which has no init hook.

Synchronous compiles cost a full IR build per edit: about 2 ms for 5
layers, 14 ms for 20 and 85 ms for 50 when dragging an opacity slider.
Large stacks will need a faster value-only patch path.

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
tests/run.sh                        # headless tests (tests/test_*.py)
tests/run.sh --ui                   # plus the windowed UI draw test
BLENDER=/path/to/blender tests/run.sh   # another Blender build
```

`tests/harness.py` registers the addon from the checkout and provides
`check`/`section`/`finish`. `test_compile.py` covers the compiler,
`test_api_surface.py` asserts that every Blender class, property and
operator the addon depends on still exists, and `test_ui_draw.py` draws
every panel in a real window (Xvfb on CI) and fails on draw exceptions.
`.github/workflows/test.yml` runs all of this against the latest patch of
every supported Blender series, lints with ruff (`uvx ruff check .`
locally) and validates the built package with the strict
`blender-extension-builder` validator; `release.yml` drafts a release only
after all of that passes. See PS-080.

## Port backlog

The feature port from v2 is tracked in `docs/BACKLOG.md` with one ticket
per task under `docs/tickets/`. Tickets that extend the rules above
(artifact-owned parameter nodes, IR drivers, folder nodes) update this
document when they land.
