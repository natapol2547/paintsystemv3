# PS-057 Filter layer

Epic F. Size L. Milestone M3.

## v2 behaviour

v2 had no filter layer. Two features covered parts of one:

- Adjustment layers (`data.py:127`, ported as PS-023) filter the stack
  below inside the shader. Only per-texel maths can be written that way:
  nothing that reads a neighbouring texel — a blur, the brush painter —
  fits.
- The image filters (`operators/image_operators.py:178, 208`,
  `operators/image_filters/`) ran over one layer's own pixels and wrote
  the result into a copy of the image, which was assigned back to the
  layer. The parameters and the source pixels were not kept, so the
  effect could not be re-edited, and it never saw the layers below.

So the thing this ticket adds — an effect that reads the whole composite
below it and stays editable — did not exist in v2 and has no behaviour to
match.

## Decisions

Four open questions were settled before the work started, and the design
below assumes all four:

- **Blur first.** PS-051's gaussian blur is the mechanism's first
  payload, because its output can be checked against a numpy reference
  pixel for pixel. PS-053's painterly brush painter lands afterwards as
  a second `filter_type` with no mechanism change. PS-051 is therefore a
  dependency of this ticket rather than a sibling.
- **Amount only, no blend mode.** The filtered pixels replace the stack
  below, faded by the layer's existing Opacity. `blend_mode` is hidden
  because it has no meaning under replacement. Photoshop and Krita both
  offer per-filter blending, so expect a follow-up ticket; the Filter Mix
  group is shaped so that adding modes swaps its middle and touches
  nothing else.
- **Auto refresh on by default.** A stroke below a filter layer
  refreshes it when the stroke ends. The refresh is hard-gated to the
  GPU composite path so it can never start a Cycles bake, and
  `auto_refresh` is per layer for the cases where waiting is preferable.
- **The derived image is packed into the `.blend`.** A reopened file
  shows the filter without rebuilding, and undo or redo past a build
  gives real pixels rather than black. The cost is one full-resolution
  image per filter layer in the file; a per-layer Pack or Rebuild switch
  is a later addition if anyone reaches a file-size wall.

## v3 design

### Shape

A filter layer is an ordinary `PaintSystemLayerNode` whose `emit_source`
returns an Image Texture over a derived image it owns, and whose
`emit_blend` *replaces* the stack below with those pixels instead of
compositing over them. It never sets `cache_enabled`, and
`CompileContext.is_cached` (`compiler/core.py:205-216`) is never true for
it.

The derived pixels are produced by compositing the stack below into one
RGBA buffer and running a GPU filter pass over it (`filters/core.py`,
PS-050). The composite is done on the GPU where it can be, and by the
existing Cycles bake where it cannot.

### Why not the node cache

The obvious implementation is `cache_enabled` plus a filtered
`cache_image`, so the compiler substitutes the layer through
`ctx.is_cached` like any other cached layer. It is wrong on three counts,
each fatal alone, and all three were confirmed against running code
during the design spikes:

- The cache swallows the blend. `build_bake_tree` bakes
  `ctx.output_ref(bake_target, ...)` and `is_cached` exempts the bake
  target, so the target emits its full `emit_blend`
  (`compiler/core.py:352-357`, `compiler/bake.py:34-42`): opacity, blend
  mode, `enabled`, Mask and Clip end up *inside* the baked pixels. Every
  one of those is in `node_state`, so nudging an opacity slider would
  invalidate the cache and queue a multi-second rebuild. What a filter
  wants cached is the filtered pixels; what it wants live is the blend.
- `is_cached` refuses a node where `feeds_clip_run(node)` is true
  (`compiler/core.py:212-215`), which a filter layer becomes the moment
  anyone clips a layer to it. Removing that refusal makes the whole stack
  below the clip base compile to transparent black, silently:
  `ctx.upstream` returns None and `input_source` falls back to the socket
  default (`compiler/core.py:193-198`). For an ordinary layer the refusal
  costs performance; for a filter layer it would cost the feature.
- A stale ordinary cache falls back to a pixel-equivalent live graph
  (`nodes/layers/base_layer_node.py:178-180`). A filter layer has no live
  graph, because a shader cannot sample neighbouring texels of a
  procedurally composited stack, so "stale" would mean the effect
  vanishes from the viewport.

Under this design the node cache is untouched and composes *on top of* a
filter layer as an independent knob: ticking Use Cache on the filter
layer collapses it and everything below into one image in the usual way.

### Filter Mix (`compiler/library.py`)

`emit_blend` is overridden to emit `{uuid}:fmix`, a group from a new
`filter_mix_group()`. Inputs Prev Color `cb`, Prev Alpha `ab`, Color
`cs`, Alpha `as`, Amount, Mask, Clip; outputs Color and Alpha:

```
f     = clamp(Amount * Mask)
kept  = mix(1, ab, Clip)
a     = ab * (1 - f) + as * f * kept
share = as * f * kept / a
Color = mix(cb, cs, share)
Alpha = a
```

At `f == 1` unclipped the output is exactly the filtered pixels; at
`f == 0` exactly the stack below. It is the premultiplied lerp written as
a Mix, which is possible because the two weights sum to `a`, and it
reuses `_build_layer_blend`'s trick that Math DIVIDE returns 0 for a zero
divisor, so a fully transparent result takes the backdrop colour rather
than NaN. Nine nodes; a sibling of `_build_layer_blend`
(`compiler/library.py:67-169`) with the source alpha moved out of the
coverage term.

`kept` weights only the source while the backdrop keeps `1 - f`, exactly
as `_build_layer_blend` does. Folding `kept` into `f` instead would let a
clipped filter add coverage outside the layer it is clipped to: backdrop
alpha 0.5 under an opaque filter at full Amount would come out at 0.75
rather than 0.5. Clipped, the output alpha is `as * ab` at full Amount
and never exceeds `ab`.

Inheriting `emit_blend` instead is wrong in a way the artist sees. Source
over the same stack it was computed from compounds alpha: backdrop alpha
0.5 with filtered alpha 0.5 at full Opacity gives alpha 0.75 and
`source_share` 0.667, so at full strength the unfiltered original shows
through and every soft edge gains a halo. A channel's bottom is the
transparent Group Input (`nodetree/tree.py:195-199`), so "the stack below
is opaque" is not a case to rely on.

Amount is the inherited `opacity` (`0.0` when `enabled` is off), so the
existing slider is the dry/wet control and costs a recompile, never a
rebuild. `blend_mode` is meaningless under replacement; the class sets
`ps_shows_blend_mode = False` and `draw_layer_settings` and
`panels/layers_panels.py` skip it.

Unbuilt — no derived image, or it has no data — forces Amount to `0.0`.
That is an exact pass-through in every arrangement, including as a clip
base, so adding a filter layer changes nothing on screen until it is
built, and deleting its image gives the original back rather than a black
band.

### Data model

`PaintSystemFilterLayerNode` in `nodes/layers/filter_layer_node.py`,
registered in `nodes/layers/registry.py` as the fourth type
(`ps_type = 'FILTER'`, `ps_menu_section = 'EFFECT'`,
`ps_add_options = ('filter_type', 'resolution')`).

Authored:

- `filter_type: EnumProperty`, items from `filters/layer_specs.py`. v1
  ships one entry; PS-051 and PS-053 add theirs with no mechanism change.
- One flat `FloatProperty`/`IntProperty`/`EnumProperty` per parameter of
  every registered kind (`blur_sigma`, `blur_strength`, later
  `stamp_density` and the rest), drawn conditionally on `filter_type`.
  Flat, not a `PointerProperty` to a `PropertyGroup`: `IR._serialize`
  falls through to `repr()` for a PropertyGroup
  (`compiler/ir.py:190-206`), and `repr()` of one is a data path, not its
  contents — a parameter group would be invisible to every hash in the
  addon and would additionally churn `node_state` on a rename.
- `resolution` (`RESOLUTION_ITEMS` from `ops/node_tree_ops.py:12`),
  `uv_map`, `auto_refresh: BoolProperty(default=True)`.

Derived bookkeeping on the node:

- `derived_image: PointerProperty(bpy.types.Image)`.
- `derived_capture: PointerProperty(bpy.types.Image)` — the fallback
  path's baked input, session only (below).
- `derived_stale_structure: BoolProperty(options={'SKIP_SAVE'})` —
  written from `emit_source`, only when the value changes, exactly as
  `cache_stale` is (`nodes/layers/base_layer_node.py:167-180`). Not
  saved, because the first compile after a file read recomputes it and
  `on_load_post` calls `mark_dirty()`.
- `derived_stale_pixels: BoolProperty` — written from outside the
  compile, by the watch below. This one *is* saved, deliberately
  diverging from `cache_stale`: nothing recomputes it for free after a
  reload, and a file saved out of date must reopen out of date rather
  than show a fresh badge over old pixels.
- `derived_error: StringProperty` — the last refusal, shown until the
  next attempt.

`paint_image` stays None. That is load-bearing and needs no new code:
`context.update_active_image` already skips a layer with no image, and
`filters/actions.py:99` already refuses an action on one, so the brush
and the pixel actions can never target a filter layer.

### Hashing

`compiler/core.py:_hashed_props` (`:243-261`) gains one rule: skip any
name in `getattr(cls, 'ps_unhashed_props', ())`, a class attribute
defaulting to `()` on `PaintSystemBaseNode`. The filter node lists its
`derived_*` bookkeeping, its parameters, `resolution`, `uv_map` and
`auto_refresh` there. One declaration rather than a second name prefix,
and free at runtime because `_hashed_props` is memoised per class.

Two consequences, both wanted. Writing `derived_stale_structure` from
inside a compile cannot churn `node_state` and recompile forever. And
dragging a blur slider does not invalidate a node cache or a PS-007
channel bake above the filter before any pixel has changed.

What consumers above *must* see — "these pixels are a different picture
now" — comes back through the existing hook:

```python
def hash_parts(self, ctx):
    # The derived image hashes by name like any datablock
    # (compiler/ir.py:190), so the stamp of its pixels is what a cache
    # above has to see.
    image = self.derived_image
    return [image.get(BUILD_KEY, "") if image is not None else ""]
```

`subtree_hash` folds `hash_parts` in at `compiler/core.py:236-238`.

### The stamps live on the image, not the node

Five ID properties, written together at commit, constants in
`filters/derived.py`:

- `ps_filter_owner` — `"<tree uuid>:<node uuid>"`.
- `ps_filter_uv_map` — the UV map the pixels were laid out in. The
  fingerprint hashes the resolved map but cannot give it back, and
  `emit_source` needs the name to build the Image Texture's UV Map node,
  so it is stamped separately.
- `ps_filter_fingerprint` — the structural token:
  `hash_payload([FILTER_VERSION, filter_type, params, [w, h],
  resolved_uv_map, ctx.subtree_hash(below) or "empty"])`. Cheap to
  recompute inside a compile, where `ctx._subtree_hashes` is already
  warm.
- `ps_filter_sources` — the digest of every source image at build time,
  by name. Read only by the digest pass below.
- `ps_filter_build` — a hash of the other two. This is what `hash_parts`
  returns, so a rebuild that changed only source *pixels* still
  invalidates a cache above it.

They are on the image and not on the node for the reason
`compiler/core.py:29-31` already gives for the artifact: whichever copy
of the node and whichever copy of the image undo restores, the stamp
describes the pixels sitting next to it. It is also what makes
duplication and `image.copy()` behave.

Note that an unpacked generated image loses its pixels through
`image.copy()` and comes back black after undo-then-redo (PS-096:173-176),
while its ID properties survive both. A stamp with no pixels is the worst
failure this feature can have, so **the commit step packs before it
stamps**: `ResultImage.commit` (`filters/core.py:302-317`), then
`undo.pixels.pack_write_once`, then the four stamps. A failed pack leaves
the layer out of date rather than wrongly fresh.

### Compile

Only `emit_source` and `emit_blend` are overridden. `emit`
(`nodes/layers/base_layer_node.py:167-194`) runs unmodified, so clip
runs, folders, the stack walk and `topological_order` behave exactly as
they do for an image layer, the Group Input node stays in the artifact,
and `build_bake_tree` keeps working over a tree containing filter layers.

- Built: `emit_image_texture(ctx, self, 'result', self.derived_image,
  stamped_uv_map)` — artifact nodes `{uuid}:result`, `{uuid}:result:uv` —
  feeding `{uuid}:fmix`. The UV map used is the one stamped on the image,
  not the authored `uv_map`, so changing the setting relocates nothing
  until the rebuild runs.
- Unbuilt: Amount forced to 0, one `fmix` group with an unlinked Color
  input. Cheap and provably invisible.
- Stale: identical artifact to built. The old pixels keep rendering.
  Flicking the viewport back to the unfiltered stack on every brush dab
  underneath is worse than old pixels next to a loud badge.

`emit_source` also recomputes the structural fingerprint and writes
`derived_stale_structure` when it differs, on the "write only when the
value changes" rule.

Cost: one `subtree_hash` walk per filter layer per compile, memoised —
about what `is_cached` already costs a cached layer. `tests/test_perf.py`
gets a budget line for a 20-layer stack with one filter layer.

Not done, deliberately: no pruning of the live sub-stack when Amount is
exactly 1. It would recreate the `feeds_clip_run` upstream-not-visited
failure for a second reason, and the user can already get that saving by
ticking Use Cache on the filter layer.

### The filter's input

`filters/layer_plan.py::resolve_input(context, tree, node)` walks the
subtree below and returns a plan that names its path.

**Path A, GPU composite (`filters/composite.py`), the default.** The walk
mirrors `emit`/`emit_blend`/clip/folder over the same links the compiler
reads (`stack_ops.feeding_link`, `clip_base`, `feeds_clip_run`, folder
`Content Color`), compositing bottom-up through a ping-pong pair of
`RGBA16F` targets. `RGBA16F` for the reason `filters/core.texture_format`
gives: 11 mantissa bits hold every `k/255` exactly, at half the memory of
`RGBA32F`. Image sources upload through `gpu.types.Buffer` and decode
sRGB to linear in the shader, matching what an Image Texture node feeds
the shader today; the result is encoded back into the derived image's
storage on the way out. `filters/blend_glsl.py` ports
`_build_layer_blend` and the 19 `ShaderNodeMix` modes.

Peak video memory, which the design budgets rather than hopes about: the
ping-pong pair plus one source texture at a time, each released after its
layer composites — about 400 MB at 4096², plus the filter's own targets.
An allocation failure is caught and reported by name.

Conditions, each checked by name, each falling back rather than failing:
every node below is Image, Solid, Folder or a valid filter layer; every
`uv_map` resolves to the same UV layer through
`gpu_passes/texel_map.py:112::resolve_uv_map`, which already maps `""` to
the active render UV map; no `source == 'TILED'`; the blend mode is in
`ALLOWED_BLEND_MODES`; no linked Mask input (PS-015). An object is needed
only when at least one layer below sets an explicit `uv_map`: when they
all leave it empty they resolve to the same map whatever it is, and the
composite is object-independent.

`ALLOWED_BLEND_MODES` is an allow-list a mode joins only once its
per-texel parity test against a real Cycles bake of the same subtree
passes (`tests/test_filter_layer_parity.py`). A mode outside it is not a
refusal; it falls back.

**Path B, Cycles fallback (`compiler/bake.py`).** For group layers, mixed
UV maps, UDIM, un-parity-tested blend modes, linked masks, and the layer
types that genuinely evaluate surface data (PS-022, PS-023, PS-024,
PS-025, PS-026, PS-027, PS-028) — all of which land in M2, before this
ticket. `bake_subtree(context, tree, node, obj, image, *, margin,
uv_map)` is `bake_node_cache`'s body (`compiler/bake.py:107-163`)
extracted verbatim, with the cache bookkeeping left behind in
`bake_node_cache`, which becomes a short caller with an unchanged
signature and return value.

No compiler change is needed for this: `build_ir` already takes any node
as `bake_target` and links its output refs into `bake:out`
(`compiler/core.py:324, 337, 352-357`), so passing *the node that feeds
the filter's Color input* bakes exactly the composite below, including
that node's own blend — and for a clip base, the base's own content,
which is what a clipped filter layer wants. Those are the lines PS-007
plans to extend with `bake_target=('channel', name)`; this ticket leaves
them alone.

The bake is 2 s at 2048² and 10 to 14 s at 4096², measured on a default
cube with a two-layer stack, which is a best case. So the fallback keeps
its result: `derived_capture` is a session-only image tagged
`ps_session`, and a parameter change re-runs only the filter passes
against it. The module docstring carries the rule that follows:
**timers re-filter, operators bake.** No timer path may start a Cycles
bake, ever.

Two bugs in that code are fixed while it is being extracted, both
recorded already: `bake_node_cache` overwrites the selection and the
active object with no save or restore (`compiler/bake.py:145-147`, while
`tests/harness.py:153-154` does it right and `docs/BACKLOG.md:92` records
the same bug being fixed once in `bake_group`), and `_temporary_material`
leaves a permanent `None` material slot on an object that had none
(`:84-85`).

### Freshness

Two halves, because `subtree_hash` has one known blind spot and refusing
to paint under a filter layer is not a product.

**Structural.** The fingerprint above, recomputed in `emit_source` and
compared with the stamp. `subtree_hash` (`compiler/core.py:218-240`)
already covers a layer added, removed, reordered or muted, a
`fill_color`, another layer's blend mode or opacity, a clip flag, an
image datablock swapped or renamed, and a nested group tree's recompile.
Free, on every compile. Note it hashes the upstream of the Color input,
not the filter node itself: opacity, Amount, `enabled`, Mask and Clip are
outside it by construction.

**Pixels.** `IR._serialize_other` reduces a datablock to `["id", type,
name_full]` (`compiler/ir.py:190-191`), so painting into an image below
changes no hash. `filters/freshness.py` closes it without hashing pixels
on the hot path:

- Blender tags the painted image in the depsgraph at the end of a stroke
  on every version in range (`paint_image_proj.cc`, `paint_image_2d.cc`,
  `paint_image_ops_paint.cc`), and an `Image` row does appear in
  `depsgraph.updates` for such a tag. `on_depsgraph_update_post` gains a
  branch collecting `update.id.original` values that are
  `bpy.types.Image` and calling `freshness.note_image_changed(...)`. That
  handler is the most carefully tuned function in the repo (PS-092,
  PS-093); the branch only reads the same `depsgraph.updates` walk and
  drops nothing.
- A session map of filter node to the source image `session_uid`s
  recorded at build time turns that into `derived_stale_pixels = True` on
  the nodes downstream. After a file read the map is empty, so the first
  image update rebuilds it from the stacks once.
- `filters/core.ResultImage.commit` and `undo.pixels.write_pixels` call
  `note_image_changed` too, so the addon's own writes — Clear, Fill,
  Invert today, PS-095's fill later, and one filter layer feeding another
  — are exact rather than depending on the depsgraph.
- A debounced timer (`bpy.app.timers`, the pattern at
  `compiler/core.py:487-495` and `selection/session.py:255-281`)
  recomputes the digest of suspect images and **clears** the flag when
  they came back identical, which is the common case after an undo or
  after a stroke on an unrelated layer. The digest is never allowed to
  claim freshness, only to withdraw a false alarm. It has a hard budget —
  more than three suspect images, or any above 4K, and the pass skips and
  the layer stays out of date. Failing safe is the rule.

The stale row names the reason by comparing fingerprint parts: "Out of
date — the layers below changed", "— the filter settings changed", "—
the resolution changed", "— the pixels below changed". An `ERROR` badge
appears on the layer row in `PAINTSYSTEM_UL_layers.draw_item`, mirroring
`draw_cache_settings` (`nodes/layers/base_layer_node.py:148-159`).
`paint_system.clear_filter_result` drops the derived image and returns
the layer to pass-through, so a wedged layer is recoverable without
deleting it.

### Rebuild

`filters/layer_build.py::steps(context, tree, node)` is a generator of
bounded units, driven two ways.

**Explicitly**, by `PAINTSYSTEM_OT_rebuild_filter_layer`, a modal
operator on a `wm.event_timer_add(0.01)` with a per-event budget, a
resolution/margin/UV-map dialog copied from `PAINTSYSTEM_OT_bake_cache`
(`ops/bake_ops.py:20-60`), `wm.progress_update` plus
`workspace.status_text_set`, and ESC or right mouse to cancel.
`PAINTSYSTEM_OT_rebuild_filter_layers` runs the same generator over every
out-of-date filter layer in the tree, bottom-up, under one modal — which
is also how a filter layer below an out-of-date filter layer is handled:
it is rebuilt first, not refused.

The steps: resolve (nothing allocated yet, `Refused` by name), composite
or bake, filter passes, banded readback, commit. **Only the commit step
writes anything**, so a cancel leaves the previous pixels bit-identical.

The honest budget: `gpu_passes.core.read_color` reads a whole framebuffer
in one call — 0.99 s at 4096² on the probe machine — and
`PixelSource.from_image` uploads one image in one call, about 0.17 s at
4096². `read_color` therefore grows a row range so the readback yields
per band, as `run_pass` already bands its draws, and an upload is one
step per source image. The claim this ticket makes is that no single step
runs longer than roughly 0.2 s at 4K, not 40 ms. The modal holds the
window for the length of the build, which is the price of being
cancellable; the auto path below exists so the common case does not reach
it.

`bl_options = {'REGISTER', 'UNDO'}`, as `PAINTSYSTEM_OT_bake_cache` has.
This is not the case PS-090's no-`UNDO` rule covers. That rule exists
because a memfile step stacked on an *image undo step* costs two Ctrl+Z,
and a derived image pushes no image step at all — that is what
`ResultImage` is for. With `UNDO` and a packed image, one Ctrl+Z takes
the whole rebuild back, pixels and stamps together, which is what a
button labelled Update should do.

**Automatically**, by `filters/layer_job.py`, the same generator pulled
from a `bpy.app.timers` tick with a time budget, debounced 0.4 s, gated
by `auto_refresh`, with the `GPU_ERROR` retry and back-off copied from
`selection/session.py:255-281`. Two hard rules:

- The auto path runs **only** when `resolve_input` reports the composite
  path. A layer on the Cycles fallback shows "Update needed (baked
  input)" and waits for the button, so a background refresh can never
  lock Blender for ten seconds.
- The viewport keeps showing the previous pixels for the whole job, and
  the commit is one atomic write, so no intermediate state is ever
  visible.

The job holds `(tree.name, node.uuid)` and re-fetches each tick (PS-090's
datablock rule); `cancel_all()` runs from `undo_post`, `redo_post` and
`load_post`. It writes pixels, packs, stamps and calls `mark_dirty(tree)`
— which is the repo's existing timer-safe compile path, including the
pending-fingerprint stamp that keeps memfile undo from trusting a copy a
timer patched (`compiler/core.py:470-495`). A pixel write the timer makes
after an undo step was pushed is lost on a Ctrl+Z together with its
stamp, which leaves the layer out of date and schedules another refresh:
self-healing, and worth keeping that way.

`poll` uses `gpu_passes.core.gpu_known()`; only a path about to draw
calls `gpu_available()`, because `gpu.init()` crashes rather than raises
on 5.2.1 and 5.3 with no usable driver (`gpu_passes/core.py:33-70`).

### Refusals

Raised as `filters.core.Refused` from `resolve_input` and reported as a
`WARNING` (`ops/pixel_ops.py:60-80`), each naming the layer or image at
fault, in the style of `filters/actions.py:82-114`:

- "This Blender has no GPU context to run a filter on"
- "There is nothing below 'X' to filter"
- "Image 'Y' below is a UDIM image, which is not supported yet" — the
  fallback cannot help either; `bake_node_cache` does not do UDIM (PS-009)
- "Image 'Y' below has no pixels; is its file missing?"
- "Image 'Y' below is linked from another file"
- "Filtering the layers below 'X' needs a Cycles bake, which needs an
  active mesh object with a UV map" — the fallback's preconditions
  (`compiler/bake.py:109-112`)
- "Layers below use different UV maps ('UVMap' and 'UVMap.001'), so
  filtering them needs an active mesh object"
- "The GPU could not allocate the textures for this filter; try a lower
  resolution"
- "Filter layers only work on colour channels"

### Lifecycle

**Create.** Nothing is allocated in `create()`. A new filter layer has
`derived_image = None` and compiles as a pass-through, so adding one is
instant and cannot fail. The image is made in the commit step through
`create_managed_image` (`compiler/bake.py:24-31`), byte and sRGB like a
painted layer image — which halves memory against float and keeps
PS-056's open premultiply-twice bug (PS-056:32-37) away from this
feature. The datablock is reused and `image.scale()`d on a resolution
change, exactly as `bake_node_cache` does (`:116-120`), so PS-083 and
PS-084 stay rare rather than becoming routine.

**Duplicate.** `copy()` calls `super().copy(node)` and then
`self.derived_image = self.derived_image.copy()`, re-stamping
`ps_filter_owner` with the new uuid. The pixels come with it because the
image is packed, and the other three stamps come with it because ID
properties survive `image.copy()`, so the duplicate is immediately valid
and looks identical. Sharing the pointer — which is what Shift+D does
today for `cache_image` — would let two layers silently overwrite each
other. That same bug is fixed on `PaintSystemLayerNode.copy` in the same
ticket, by clearing `cache_image`, `cache_hash`, `cache_uv_map` and
`cache_stale`: `bake_node_cache` reuses `node.cache_image` when it is not
None (`:116-117`), so today baking either duplicate overwrites the
other's pixels with no warning. A cache is a re-derivable artifact, so
clearing it is right; a filter result is the layer's content, so copying
it is right.

`derived_capture` is dropped by `copy()`; it is session data.

**Delete.** `PAINTSYSTEM_OT_remove_layer.execute` (`ops/layer_ops.py:81-97`)
already computes the removed set, including a folder's descendants; it
gains a pass removing each filter layer's derived image before
`tree.remove_layer_node`. In the operator and not in `Node.free`: `free`
also runs on undo-driven teardown, while the operator carries `UNDO`, so
the memfile step records both the node and the removed packed image and
Ctrl+Z brings back both with pixels intact. The remove dialog gains a
line: "Its filtered image goes with it."

**Sweep.** `filters/derived.py::cleanup_orphan_derived()`, modelled on
`cleanup_orphan_artifacts` (`compiler/core.py:380-391`): every image
carrying `ps_filter_owner` whose tree uuid or node uuid no longer
resolves to a filter layer pointing back at it, removed at 0 users (or 1
plus a fake user). Called from `on_load_post` next to
`cleanup_orphan_artifacts()`, **and only there**: a load is the one
moment with no undo stack to break, and sweeping at save time would set
up PS-083's recreate-under-a-freed-name case. It covers the paths the
remove operator does not: a node-editor delete, a channel deletion
(`nodetree/tree.py:202-206`, which leaves layer nodes alive and is out of
scope otherwise), and a file from a crash or an older build.

**Save.** `paint_system_images()` (`handlers/node_tree_handlers.py:16-26`)
gains two rules: an image tagged `ps_filter_owner` is included only when a
live filter layer still points at it, and an image tagged `ps_session` is
never included. So an orphan is never packed into the next `.blend`, and
the fallback's capture — a second full-resolution buffer per filter layer
— never enters a file at all. `on_load_post` removes every `ps_session`
image outright, so a capture cannot survive into a session whose
freshness maps have been reset. A live derived image is packed at commit
already, so the save path is a no-op for it; that is what makes a
reopened file show the filter without a rebuild.

**Undo and redo.** PS-090:83-84 says owners of derived images rebuild them
from their inputs after undo and redo. This design does not, and PS-090
has to be amended to allow the alternative: the pixels are packed so they
survive, the stamps travel on the same datablock so they always describe
those pixels, `on_undo_post` already calls `mark_dirty()` so the next
compile re-checks the structural half for free, and `freshness` drops its
session maps so the pixel half re-verifies rather than being trusted
across a restored document. Worst case the user sees "Out of date" and
the layer refreshes. Running a multi-second GPU or Cycles job inside
`undo_post` is precisely the freeze this whole design exists to avoid.

**Fake users.** None, per `docs/ARCHITECTURE.md:20-23`. The node's
pointer is the real user.

**Ownership UI.** No `template_ID` on `derived_image`. `cache_image` has
one (`nodes/layers/base_layer_node.py:159`) and it lets a user point a
derived slot at authored artwork with nothing marking it read-only. The
panel shows the name and size as a label, the state row with its reason,
Auto Refresh, Update, Cancel while a job runs, and Clear Result.

## What this changes elsewhere

- `compiler/core.py:_hashed_props` gains the `ps_unhashed_props` rule. No
  shipped class declares one, so no existing `cache_hash` changes value;
  `tests/test_compile.py` and `tests/test_parity.py` get a case pinning
  it, or the next reader deletes it.
- `compiler/bake.py` is split into `bake_subtree` plus a short
  `bake_node_cache`, and two bugs are fixed in passing (selection and
  active object restored, no stray `None` material slot). Any test that
  relied on the baked object staying active will see the previous active
  object back. `docs/ARCHITECTURE.md:288-298` describes this code in
  prose and needs re-reading.
- `PaintSystemLayerNode.copy` clears the cache pointer. PS-018's
  duplicate-layer work must expect a duplicate to arrive unbaked.
- `nodes/layers/registry.py` gains a fourth type, so the Add Layer menu,
  the `paint_system.add_layer` enum and the node editor's Layers category
  all grow an entry, and it opens a dialog because it has
  `ps_add_options`. `tests/test_layers.py`, `test_ui_draw.py`,
  `test_icons.py`, `test_api_surface.py` and `test_demo_flow.py`
  enumerate layer types somewhere.
- `ops/layer_ops.py::PAINTSYSTEM_OT_remove_layer` now removes Image
  datablocks. `tests/test_smoke_loop.py:171-199`, which undoes and redoes
  exactly that operator and calls `check_no_freed_images`, is the test
  most likely to catch a mistake.
- `handlers/node_tree_handlers.py::paint_system_images()` stops returning
  two categories of image. That is a deliberate departure from PS-056's
  blanket rule and both PS-056 and `docs/ARCHITECTURE.md:191-197` need
  the sentence; `tests/test_images.py` needs the cases.
- `gpu_passes/core.py::read_color` gains an optional row range. Existing
  callers pass none and are unaffected.
- `filters/core.py` gains `PixelSource.from_texture`, so its module
  docstring ("in and out of the image's own pixels") stops being true and
  is updated.
- PS-090's caller rule about owners of derived images is amended to allow
  pack, re-check and flag.
- `ops/bake_ops.py` refuses to bake a cache over an out-of-date filter
  layer below the target, by name. PS-007 and PS-055 owe the same check
  when they land; both get a line.
- PS-051 becomes a dependency of PS-057 rather than a sibling, and
  PS-053's "in place on the image" line is amended: the painterly filter
  becomes a filter-layer kind as well as a destructive action.
- `docs/BACKLOG.md` gains the PS-057 row under Epic F and the M3
  milestone list.

## Prerequisites

- **PS-084** (a rename invalidates a cache silently). The structural
  fingerprint folds `ctx.subtree_hash`, which serialises a datablock by
  name (`compiler/ir.py:190-191`), so renaming any image below a filter
  layer would silently cost a multi-second rebuild. Fixing it first also
  avoids invalidating every stored fingerprint later.
- **PS-083** (delete and recreate under a freed name leaves a stale
  artifact). With a filter layer that case stops being a blank texture
  and becomes pixels computed from an image that no longer exists. The
  design reuses and rescales one datablock to keep it rare, but the
  revalidation gap itself belongs to PS-083.

## Known gaps

- No region-limited invalidation and no low-resolution proxy. A refresh
  recomputes the whole image. Krita's rect-walk model and Photoshop's
  tile pyramid both need machinery this codebase has nothing of, and the
  extension platform's no-threading rule blocks the concurrency that
  makes them cheap.
- The Cycles fallback cannot be sliced or cancelled: `bpy.ops.object.bake`
  is one blocking call. The only non-blocking path Python can reach is
  `bpy.ops.object.bake('INVOKE_DEFAULT')`, which starts a `WM_jobs` and
  reports through the `object_bake_*` handlers; it needs a real window,
  so the test matrix cannot cover it, and it would keep a throwaway
  material and overridden render settings alive across an arbitrary gap.
  Noted as the only way that freeze ever goes away.
- Float (32-bit) derived images are out of scope until PS-056's packed
  float premultiply bug is fixed. A heavy blur of a smooth gradient will
  band at 8 bits, and an HDR stack clips through a filter layer.
- UDIM is refused, as everywhere else in `filters/` (PS-009).
- Channel deletion still leaves layer nodes alive
  (`nodetree/tree.py:202-206`); the sweep collects their images, the
  nodes stay. Pre-existing, out of scope.
- `note_image_changed` marks a whole layer suspect, not a region, so a
  one-texel dab costs a full refresh.

## Acceptance

- An unbuilt filter layer over a two-layer stack bakes pixel-identical to
  the same stack without it, at 64², for every blend mode on the layer
  below.
- Filter Mix at Amount 1 renders the derived image exactly and at Amount
  0 the stack below exactly; with backdrop alpha 0.5 and filtered alpha
  0.25 at Amount 0.5 it matches the premultiplied-lerp reference within
  the bake's tolerance.
- `tests/test_filter_layer_parity.py`: for every mode in
  `ALLOWED_BLEND_MODES`, the GPU composite of a subtree matches a Cycles
  bake of the same subtree at 256² within 2/255. A mode that fails is not
  in the list and falls back to the bake instead.
- A clipped layer above a filter layer composites onto the filter's
  pixels, and the stack below the filter is still emitted — the artifact
  has the lower layer's blend group and `Prev Color` linked.
- Changing opacity, Mask, Clip or `enabled` recompiles and does not mark
  the layer out of date; changing a `fill_color` below does.
- Painting into an image below marks the filter out of date within one
  debounce; an undo that restores those pixels clears it through the
  digest pass without a rebuild.
- Duplicating a filter layer gives an independent image with the same
  pixels; rebuilding either leaves the other unchanged.
- Removing a filter layer removes its image and Ctrl+Z restores both with
  pixels; saving a file holding an orphaned tagged image does not pack
  it; loading that file sweeps it.
- ESC during a rebuild leaves the previous pixels bit-identical and the
  state row unchanged.
- Measured and recorded in this ticket after the first slice that can
  run it: a 2048² rebuild through the composite path, a 4096² one, and
  the cost of the pack at both sizes.
