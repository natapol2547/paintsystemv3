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
- The addon sets no fake users. Every datablock it makes is kept by a real
  user (the material, the tree's `compiled` pointer, an artifact's group
  node), so what nothing uses is not saved and does not clutter the file.
  Library groups are generated again on next use.
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
   artifact so it always describes the nodes next to it. A reused node
   keeps the value of any input the IR does not set, so an emitter sets
   an input on every compile or never.

The builder writes a value only when RNA does not already hold it (floats
compared as float32, datablocks by identity), because every write on the
artifact tags the tree and the materials using it. The fingerprint is
stamped whether or not anything was written, so a comparison that wrongly
reports "equal" leaves the artifact stale for good; `tests/test_parity.py`
is the net for that. What a build did reach — nodes created, values
written, links created and removed, whether it arranged — is counted in
`NodeTreeBuilder.stats` and left on `compiler.core.last_build_stats`.

Links are diffed the same way, by the pair of socket addresses each link
connects rather than by `ps_identifier`: a hand-duplicated artifact node
carries the tag, and matching on it would mistake the copy's link for the
real one. One pass over `node_tree.links` decides every removal, so the
diff never reads `NodeSocket.links` (see the link index under Layer
stack).

Node locations follow the same rule, in the builder's layout and in
`arrange_stack`, even though they are outside the IR and the fingerprint:
laying a stack out rewrites the position of every layer, and nearly all of
them keep the position they had. `NodeTreeBuilder._set_loc` and
`_shift_loc` are the only things that move a node during a build, which is
what lets layout cache each node's bounding box for the length of one.

The compiler never writes back into the document beyond the normalize
repairs, so a nested compile request only needs a re-entrancy flag.

Setting `PS_PROFILE=1` in the environment makes `compiler/profile.py` log
how long each phase took (`normalize`, `build_ir`, `fingerprint`, `apply`,
and the builder's `upsert`, `links` and `arrange` inside it). Unset, a
phase is one call into a shared no-op, so the switch costs nothing.

## Layer stack (`nodetree/stack_ops.py`)

The stack is the graph; there is no parent or order property. The top
layer feeds a channel socket on the active Group Output, each layer takes
the stack below it on `Color`/`Alpha`, and a folder
(`PaintSystemFolderLayerNode`) takes the top of its content on
`Content Color`/`Content Alpha`. The bottom layer of a folder has nothing
linked below it, which reads as a transparent backdrop. A folder composites
its content like any layer composites its source, so its opacity, blend
mode and `enabled` apply to the whole content without touching the child
layers.

`tree.stack(channel)` walks this top first and returns `StackItem(node,
level, parent, index_in_parent)`, each folder followed by its content.
Structural edits (`insert_on_top`, `insert_above`, `insert_into`, `remove`)
are link operations that keep two invariants: a layer's `Color` output
feeds at most one slot, and a slot's alpha input is linked from the alpha
partner of whatever feeds its colour input.

The walks read a socket's links through `socket_links` rather than through
`NodeSocket.links`: that property is implemented in Python and scans the
whole tree, so a walk down a stack is quadratic in its link count. The
`link_index` context manager maps a tree's links by socket in one pass and
`socket_links` reads that map, falling back to the property for any tree
without one. An index may only cover a read-only stretch — `build_ir`
installs one for the length of a build, and a block that goes on to edit
links calls `invalidate` before its first write — because a stale map is
a wrong graph, not a slow one. Indexes are keyed by tree pointer, so a
compile of a child tree during a parent's build cannot clobber the
parent's.

Hand edits in the node editor can break the second invariant. The compiler
reads alpha through the colour link regardless (`CompileContext.source`),
and `repair_alpha_links` tidies the links at the start of the next stack
edit. It does not run during a compile: compiles can run after the edit's
undo step was pushed, where changing the document would reintroduce the
stale-undo problem described under Triggers.

Moves are computed from the same walk. `movement_options(items, node,
direction)` lists what up or down can mean next to folders (skip a
sibling, enter a folder, leave one) and `move` detaches the node and
reinserts it; a folder carries its content because the content hangs off
the folder. `tree.move_layer_node` runs a move as one compile and expands
the folders around the result, as `insert_layer_node` does after an add.

Clipping is resolved at compile time from the same links. A clipped
layer's base is the first unclipped layer below it (`clip_base`). The
base outputs its source unblended, the clipped layers composite onto it
with the blend group's `Clip` input on, and the top of the run blends the
result over the stack with the base's settings. Moves and edits never
touch `is_clip`; the next compile finds the new base. A layer whose
outputs are part of a run ignores its cache.

`context.parse_context(context)` resolves the object, material, tree,
channel, active layer and its stack item in one place (PS-030).

The layer list (`panels/layers_panels.py`) is a UIList over `tree.nodes`
itself. `filter_items` hides nodes outside the stack and rows under a
collapsed folder and orders the rest by the walk; `active_layer_index` is
a get/set property over `nodes.active`. Nothing about the list is stored,
so undo, load and hand edits cannot leave it out of date. Layer types are
listed by `nodes/layers/registry.py`, which feeds the Add Layer menu, the
add operator's enum and the node editor categories.

## Painting (`context.py`, `handlers/paint_handlers.py`)

Texture painting follows the active layer. `update_active_image(context)`
points the image paint canvas at the layer's `paint_image`, makes the UV
map the layer is placed with the mesh's active UV map, and turns brush
alpha off when the layer locks it. It edits tool settings and mesh data,
never the tree, so it never compiles. Everything that changes the active
layer calls it, and three triggers cover selection changes that do not go
through the addon: `depsgraph_update_post` for another active object, a
message bus subscription on `Object.active_material_index` for another
material slot, and a node editor draw callback plus a timer for a node
clicked in the node editor, which Blender reports in no other way.

A selection (PS-091) is document data too: ops on the tree, compiled
into a GPU mask the way the tree is compiled into a material. Only one
is live, the active tree's, and it applies to the active layer.
`selection/session.py` keeps what is derived from it in step. Writers
call `session.notify()`, which schedules one timer tick; the tick
resolves a `State` for the active layer, builds the mask if the state
changed (remembering failures per digest and retrying GPU errors), and
hands the state to its two consumers in order. `selection/stencil.py`
points Blender's Stencil Mask at a PNG of the mask so native strokes
are clipped, blocks painting when a selection exists but cannot be
used, and backs up the user's stencil settings where undo treats them
like the settings themselves. `selection/overlay.py` draws the marching
ants and an optional tint from draw handlers that only read the cached
mask, and calls `notify()` when what they would draw is out of step.
Nothing but the tick builds a mask. The selection edits no material and
never compiles. A mask that selects nothing counts as no selection: the
stencil is restored and nothing is drawn.

Selections drawn in the 3D view (PS-093) are `VIEW` ops: an outline in
region pixels, the view it was drawn in (stored relative to the object,
so the selection stays on its texels when the object moves), the object
and its UV map. `selection/view_raster.py` rasterises them into the
same UV-space mask through the texel map (PS-092), which holds the world
position of every texel: a signed distance to the outline at region
size, a depth pass of the painted object, and one pass over the texels.
The tools in `tools/` only append ops and notify, like every other
selection writer.

Per-object GPU caches (texel maps, depth batches, overlay batches) and
`VIEW` digests are keyed by the content of the evaluated surface,
through `gpu_passes/surface.py`, so events that report a geometry
update without changing the mesh (a stroke on 5.3, a stroke undo,
entering texture paint) rebuild nothing. The contract has three sides:
handlers mark surfaces suspect and never evaluate, timers and operators
resolve a key (reading the mesh arrays only when suspect and hashing
only on a real change), and draw callbacks only peek at the last key,
requesting a resolve when it is not fresh, so a draw shows the previous
result for one frame at most.

Painted pixels live in memory until the file is saved. `save_pre`
(`handlers/node_tree_handlers.py`) passes every image a Paint System node
points at, and every image the addon created, to `common.save_image`: a
packed image or one without a file is packed again, an image with a file
is written to it, and a failed write drops the path and packs instead.
Images nothing in a Paint System tree uses are left to Blender.

## Triggers

Compiles run synchronously, inside the edit that caused them, so the
artifact is part of the same undo step. This is a hard rule, not a
performance choice. Memfile undo takes every datablock that is identical
in two consecutive steps straight from memory instead of re-reading it.
An artifact patched after its step was pushed (from a timer, for example)
survives the undo with nodes for layers that no longer exist and pointers
to images the undo just freed. `tests/test_smoke_loop.py` covers this.

- Every property `update=` calls `mark_dirty(tree)`, which compiles
  immediately.
- Anything that makes several edits in a row (operators, `insert_layer_node`,
  `create_channel`, channel socket sync) wraps them in `suspend_compile`;
  the outermost exit compiles once.
- `NodeTree.update` (links and nodes edited in the node editor or by a
  script) calls `tree_updated(tree)`. It builds the IR to tell a no-op from
  a change. A change stamps the artifact's fingerprint with a fresh
  `pending <token>` and leaves the build to a timer, because Blender builds
  no sockets for a group node created during a node tree update (see
  below). The stamp is written inside the edit, so that step's artifact
  differs from every other step and memfile undo re-reads it instead of
  keeping the copy the timer built afterwards.
- `load_pre`, `undo_pre` and `redo_pre` block compiles, because Blender
  calls `NodeTree.update` on half-restored data. `load_post` (which runs
  before the file's initial undo step is recorded), `undo_post` and
  `redo_post` unblock and compile every tree; with consistent steps that is
  a fingerprint check per tree.
- While `bpy.data` is restricted (addon registration) or writing is
  forbidden (drawing), a `bpy.app.timers` callback compiles instead.
- `depsgraph_update_post` initialises trees created from the node editor
  header, which has no init hook.

Scripts that edit links directly and need the artifact at once call
`flush_now()` or make the edits inside `suspend_compile`.

The selection session is the exception to synchronous work: nothing it
derives is document data, so it syncs on a timer. Its triggers:

- `selection.session.notify()` from the selection operators,
  `update_active_image` (every active layer, tree, object or material
  change), the message bus subscriptions on `Object.mode` and
  `Window.scene` in `handlers/paint_handlers.py`, an Object geometry
  update in `depsgraph_update_post` (a renamed or removed UV map, an
  edited surface), any depsgraph update while a `VIEW` selection cannot
  be used for a geometry reason, `frame_change_post` (an animated
  deformation reports no depsgraph update), a surface key resolve that
  found a change, and the overlay's draw callbacks when the mask or
  image size is out of step.
- `notify(force=True)` from `undo_post` and `redo_post`, `load_post` and
  `load_post_fail` (after `session.forget_failures()`), and the stencil's
  own message bus subscriptions on `ImagePaint.use_stencil_layer`,
  `invert_stencil`, `stencil_image` and `Mesh.uv_layer_stencil_index`,
  so a user edit to settings the selection holds is set back.
- `depsgraph_update_post` marks the surface of an object with a
  geometry update suspect, and `undo_post`, `redo_post` and
  `frame_change_post` mark every surface suspect; nothing is dropped.
  `load_post` and `load_post_fail` forget every surface key, texel map
  and overlay batch.
- `load_post` calls `stencil.on_file_loaded()` before the forced notify:
  it forgets the old backups, resets a stencil a file saved without
  `save_pre` still holds, and subscribes again.
- `save_pre` calls `stencil.restore_all()` before it saves images, so the
  file keeps the user's stencil settings and no reference to the mask
  image; `save_post` runs `session.sync(force=True)` synchronously so
  the stencil is back before the next stroke.

Scripts that write the ops, or remove a UV map with
`mesh.uv_layers.remove()` (which reports no depsgraph update), call
`notify()` themselves.

What Blender does around `NodeTree.update`, checked on 4.2 and 5.2:

- It calls `Node.update` for every node of the tree, then `NodeTree.update`
  once. The addon only implements the tree callback, so an edit costs one
  IR build rather than one per node.
- `BKE_ntree_update` returns early when re-entered. Inside the callback,
  assigning `node_tree` to a new `ShaderNodeGroup` leaves it without
  sockets, and `interface_update` does not help. A group created from
  scratch is fine: its Group Input and Output nodes are added after its
  interface exists.
- Links created by the callback are dropped afterwards.
- A link the edit just created still reads `is_valid == False`, because
  validation runs after the callback. The stack walk and the compiler
  therefore ignore `is_valid` and skip only muted links.

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

## Filter layers (`filters/`)

A filter layer (PS-057) filters the layers below it and stays editable. It
is an ordinary layer node whose `emit_source` reads a **derived image** it
owns and whose `emit_blend` *replaces* the stack below with those pixels
instead of compositing over them, so the layer's Opacity — labelled Amount
— fades between the filtered and the unfiltered picture with no rebuild.
A filter layer is never cached; the derived image is the cache.

The pipeline is one generator, `filters/layer_build.py::steps`:

1. `layer_plan.resolve_input` decides how the stack below can be turned
   into pixels, and refuses before any video memory is spent.
2. `filters/composite.py` draws that stack into one texture on the GPU.
3. The layer's kind (`filters/layer_specs.py`) runs over it, followed by
   an sRGB encode.
4. The result is read back a band of rows at a time.
5. The last unit writes it into the image, packs it and stamps it.

A kind runs a *list* of passes rather than one, because how many a filter
needs can depend on its own parameters: a separable blur (PS-051) is two
per axis-pair, and a sigma wider than one kernel is reached by running the
pair again. Each pass is a unit of the generator, so a wide blur stays
cancellable. An empty list asks for the stack below unchanged.

A pass whose spec sets `reads_second` gets the composite bound alongside
the chain's own texture — the unsharp mask needs the picture the blur was
made from, and by then the chain has overwritten it. `layer_build` holds
the composite out of the pool for the whole chain when, and only when,
some pass asks.

Only the last unit writes anything, which is what makes a build
cancellable: the viewport shows the previous result until the commit, and
abandoning the generator gives its textures back.

**Colour space.** Everything up to the encode is scene linear and straight
alpha, which is what the render engines feed a shader. The derived image is
byte sRGB like a painted layer image, so a dedicated pass encodes on the way
out and the compiled Image Texture node decodes back to what was filtered.

**Stamps live on the image, not the node** (`filters/derived.py`), for the
reason the compiler already gives for the artifact: whichever copy of each
undo restores, the stamp sits next to the pixels it describes. The commit
packs before it stamps, because an unpacked generated image loses its
pixels to `image.copy()` and to undo-then-redo while its ID properties
survive both.

**Freshness** has two halves, because neither covers the other:

- *Structural* (`filters/freshness.py`). The build stamps a fingerprint of
  what it was asked for — the subtree hash below, the filter and its
  parameters, the resolution, the UV map, the addon version — as named
  parts, and every compile recomputes it and compares. The parts stay
  separate so a disagreement can name which one moved.
- *Pixels*. `compiler/ir.py` reduces a datablock to its name, so painting
  into an image below changes no hash at all.
  `freshness.note_image_changed` closes that from the depsgraph and from
  `undo/pixels.py`, and marks every built filter layer reading that image.

A filter layer's parameters, resolution, UV map and bookkeeping are all in
`ps_unhashed_props`, so dragging a slider invalidates no cache above it.
What consumers above must see — "these pixels are a different picture now" —
comes back through `hash_parts`, which returns the build stamp.

**Rebuilding** runs the same generator two ways.
`PAINTSYSTEM_OT_rebuild_filter_layer` drives it from a modal with a
progress bar and Escape to cancel. `filters/layer_job.py` drives it from a
`bpy.app.timers` tick, debounced, so a stroke below a filter layer
refreshes it shortly after the stroke ends. The automatic path is gated to
the GPU composite and to a GPU context already known good; a layer needing
a Cycles bake turns its own Auto Refresh off and says so rather than
freezing the window from a timer.

**Lifecycle.** The derived image is the layer's content, not a re-derivable
artifact: duplicating a layer copies it, the remove-layer operator removes
it, and `derived.cleanup_orphan_derived` sweeps what neither saw — on file
read only, where there is no undo stack to break.

## Testing

```
tests/run.sh                        # headless tests (tests/test_*.py)
tests/run.sh --ui                   # plus the windowed tests (ui_tests in run.sh)
BLENDER=/path/to/blender tests/run.sh   # another Blender build
```

`tests/harness.py` registers the addon from the checkout and provides
`check`/`section`/`finish` plus Cycles bake helpers for pixel checks.
`test_compile.py` covers the compiler, `test_blend.py` the blend math,
`test_stack.py` the stack walk, folders, stack edits and `PSContext`,
`test_layers.py` the layer list, moves and the layer type registry,
`test_clip.py` clipping by pixel,
`test_painting.py` the canvas, UV map and brush following the selection,
`test_images.py` where painted images go on save,
`test_demo_flow.py` the whole playable demo by pixel,
`test_smoke_loop.py` the operators end to end with save, reload and undo,
`test_api_surface.py` asserts that every Blender class, property and
operator the addon depends on still exists, and `test_ui_draw.py` draws
every panel in a real window (Xvfb on CI) and fails on draw exceptions;
it also checks the selection triggers that only a window loop runs.
`test_parity.py` walks a 40-layer tree of images, solids, folders and clip
runs through a long edit sequence and checks after every step that the
incrementally patched artifact holds what a compile from scratch would
have produced, that its fingerprint is the current one, and that an
unchanged recompile changes nothing (PS-081), and `test_perf.py` measures
that ticket's budget on a 100-layer tree instanced in a material, with a
`PS_PERF_SCALE` multiplier for slower runners and machine-free checks
that the work it removed stays removed.
The Epic J tests cover the texel map (`test_texel_map.py`), pixel undo
(`test_pixel_undo.py`) and the selection: `test_selection_model.py`,
`test_selection_outline.py` and `test_selection_raster.py` for the ops
and the mask, `test_selection_session.py`, `test_selection_stencil.py`
and `test_selection_overlay.py` for the session and its consumers,
`test_surface.py`, `test_selection_view_raster.py` and
`test_selection_tools.py` for surface keys, `VIEW` ops and the tool
operators, and the windowed `test_selection_session_ui.py`,
`test_selection_stencil_ui.py`, `test_selection_overlay_ui.py`,
`test_selection_view_windowed.py` and `test_selection_tools_ui.py` for
message bus triggers, native strokes through the stencil, the overlay
in real editors, selections recorded from a real 3D view and the tools
driven by simulated input (`--enable-event-simulate`). GPU checks run
headless from Blender 5.2 and windowed under `--ui` on every version,
since 4.2 to 5.1 have no background GPU context.
`test_keymaps.py` reads the addon's items from the addon keyconfig, and
the windowed, event-simulated `test_keymaps_ui.py` presses Ctrl+D in
texture paint and fails when the running build's default keyconfig binds
Ctrl+D in a keymap the item is consulted before or after (PS-093).
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
