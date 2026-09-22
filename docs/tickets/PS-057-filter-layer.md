# PS-057 Filter layer

Epic F. Size L. Milestone M3.

## Status

Shipped through the GPU composite path, with the Invert, Blur, Sharpen
and Painterly kinds (PS-051, PS-053), automatic refresh, packed results
and undo. The Cycles fallback (Path B below) is designed but not built: a
stack only a bake could draw is refused by name for now. The sections
below describe the code as it is, and say so where a part is still only
a plan.

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

### v2 UI

All paths are relative to `~/paintsystem`.

- There is no filter layer to draw. `LAYER_TYPE_ENUM` has no filter
  type (`paintsystem/data.py:99-111`). The Add Layer menu
  (`MAT_MT_AddLayerMenu`, `panels/layers_panels.py:843-884`) offers
  Folder, Solid Color, the Image, Gradient, Texture, Adjustment and
  Geometry submenus, Fake Light, Attribute Color, Random Color and
  Custom Layer, and nothing that filters.
- The image filters are reached through `MAT_MT_ImageFilterMenu`:
  "Filters" in the header of the layer settings "Image" section, and
  "Apply Image Filters" in the bake box. PS-051 and PS-053 describe the
  menu, the dialogs and what they write. One qualification to the
  summary above: with Use Baked on, the filters read the channel's
  bake image, not the layer's pixels (`operators/common.py:303-322`).
  Blur and Sharpen then write their result into the active layer's
  image, while the Brush Painter writes it into the bake
  (`operators/image_operators.py:197, 227, 396-399`). Invert Colors
  and Fill Image, the other two items of the same menu, change the
  image in place rather than a copy
  (`operators/image_operators.py:35-42, 143-158`).
- Dead filter data. `paintsystem/data.py` defines `FILTER_TYPE_ENUM`
  (`:197-201`) with "Blur" (BLUR), "Edge Enhance" (EDGE_ENHANCE) and
  "Sharpen" (SHARPEN), and a `Filter` PropertyGroup (`:2989-3005`) with
  `name`, `type` "Filter Type" over that enum, `radius` "Radius"
  (float, default 1.0) and `iterations` "Iterations" (int, default 1).
  `Filter` is not in the module's `classes` tuple (`:3304-3318`), no
  `CollectionProperty` or `PointerProperty` refers to it, and nothing
  else reads the enum, so both are dead. Edge Enhance has no
  implementation anywhere in v2:
  `operators/image_filters/basic_filters.py` holds only the blur, the
  sharpen and a `smooth_image` (`basic_filters.py:86-100`) that
  nothing calls.

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
- **Auto refresh on by default.** A new filter layer builds itself once
  things go quiet, and a stroke below one refreshes it when the stroke
  ends. The refresh is hard-gated to the GPU composite path so it can
  never start a Cycles bake, and `auto_refresh` is per layer for the
  cases where waiting is preferable.
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
`CompileContext.is_cached` (`compiler/core.py`) is never true for
it.

The derived pixels are produced by compositing the stack below into one
RGBA buffer and running a GPU filter pass over it (`filters/core.py`,
PS-050). The composite is done on the GPU where it can be. Where it
cannot, the plan is the existing Cycles bake; until that lands, such a
stack is refused.

### Why not the node cache

The obvious implementation is `cache_enabled` plus a filtered
`cache_image`, so the compiler substitutes the layer through
`ctx.is_cached` like any other cached layer. It is wrong on three counts,
each fatal alone, and all three were confirmed against running code
during the design spikes:

- The cache swallows the blend. `build_bake_tree` bakes
  `ctx.output_ref(bake_target, ...)` and `is_cached` exempts the bake
  target, so the target emits its full `emit_blend`
  (`compiler/core.py::build_ir`, `compiler/bake.py`): opacity, blend
  mode, `enabled`, Mask and Clip end up *inside* the baked pixels. Every
  one of those is in `node_state`, so nudging an opacity slider would
  invalidate the cache and queue a multi-second rebuild. What a filter
  wants cached is the filtered pixels; what it wants live is the blend.
- `is_cached` refuses a node where `feeds_clip_run(node)` is true,
  which a filter layer becomes the moment
  anyone clips a layer to it. Removing that refusal makes the whole stack
  below the clip base compile to transparent black, silently:
  `ctx.upstream` returns None and `input_source` falls back to the socket
  default. For an ordinary layer the refusal
  costs performance; for a filter layer it would cost the feature.
- A stale ordinary cache falls back to a pixel-equivalent live graph
  (`nodes/layers/base_layer_node.py`). A filter layer has no live
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
(`compiler/library.py`) with the source alpha moved out of the
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
transparent Group Input (`nodetree/tree.py`), so "the stack below
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

- `filter_type: EnumProperty`, items from `filters/layer_specs.py`:
  Invert, Blur, Sharpen and Painterly. A new kind is a new entry there,
  with no change to the mechanism.
- One flat `FloatProperty`/`IntProperty`/`EnumProperty` per parameter of
  every registered kind (`invert_alpha`, `blur_sigma`, `sharpen_radius`,
  `sharpen_strength`, the `painter_*` set), drawn conditionally on
  `filter_type` from the list each kind declares. Flat, not a
  `PointerProperty` to a `PropertyGroup`: `IR._serialize` falls through
  to `repr()` for a PropertyGroup, and `repr()` of one is a data path,
  not its contents — a parameter group would be invisible to every hash
  in the addon and would additionally churn `node_state` on a rename.
- `resolution` (`RESOLUTION_ITEMS`), `uv_map`,
  `auto_refresh: BoolProperty(default=True)`. An empty `uv_map` follows
  the layers below: the one map they name, else the active render map.
- `surface_name: StringProperty`, shown as "Object", with a search over
  the meshes the build accepts: the name of the mesh the layer resolves
  against when it needs one (UV maps, below). The node's
  `surface_object` looks it up. Add Layer fills it from the active mesh
  when that mesh shows the tree, and so does the first build that needs
  a mesh. After that the layer resolves the same way whatever is
  selected, so Auto Refresh keeps working while the user works on
  another object. The UV Map field searches this mesh's maps. Unhashed:
  which mesh answers changes no compiled node, and when the mesh matters
  the freshness stamp records what matters about it. PS-053's seams
  read the same object.

  A name rather than an Object pointer, decided with the user on
  2026-09-23 after review. A pointer is a real user of the mesh, so the
  mesh becomes part of the tree: appending a Paint System material from
  another file brought that file's mesh into the scene, and deleting the
  mesh in the viewport kept it in the file for good. The cost of a name
  is that renaming the mesh loses it. The layer then shows "the object
  or its render UV map changed" and resolves against the active mesh,
  and the next build that goes ahead stores the new name.
- `enabled` and `lock_layer` are redeclared for their update callbacks
  only, because either can hold a refresh back (Rebuild, below).

Derived bookkeeping on the node:

- `derived_image: PointerProperty(bpy.types.Image)`.
- `derived_stale_reason: StringProperty` — why the image no longer
  matches its structural inputs, or empty. Written from `emit_source`
  on every compile and by nothing else, and only when the value
  changes, as `cache_stale` is. It has no update callback: tagging the
  tree from inside a compile would schedule another compile. It is
  saved, but nothing trusts the saved value, because `on_load_post`
  calls `mark_dirty()` and the first compile recomputes it.
- `derived_stale_pixels: BoolProperty` — written from outside the
  compile, by `filters/freshness.py`. Saved deliberately: nothing
  recomputes it for free after a reload, and a file saved out of date
  must reopen out of date rather than show a fresh badge over old
  pixels.
- `derived_error: StringProperty` — why the automatic refresh could not
  build the layer, shown in the panel. Only the auto job writes it. With
  Auto Refresh still on, it is a refusal the job keeps checking on every
  change, and it clears once the layer builds or no longer needs to.
  With Auto Refresh off, the job gave up and switched it off: switching
  Auto Refresh by hand either way clears the message, so a message never
  sits next to a choice the user made. A successful Update clears it and
  switches Auto Refresh back on
  (`ops/filter_layer_ops.py::_resume_auto_refresh`).

`stale_reason` on the node combines the two: the structural reason when
there is one, otherwise "the pixels below changed" when the pixel flag is
set, and always empty for an unbuilt layer. `needs_build` is what the
auto job asks: out of date, or not built at all.

`paint_image` stays None. That is load-bearing and needs no new code:
`context.update_active_image` already skips a layer with no image, and
`filters/actions.py::resolve_target` already refuses an action on one, so
the brush and the pixel actions can never target a filter layer.

### Hashing

`compiler/core.py::_hashed_props` has one rule for this: skip any name
in `getattr(cls, 'ps_unhashed_props', ())`, a class attribute defaulting
to `()` on `PaintSystemBaseNode`. The filter node lists its `derived_*`
bookkeeping, its parameters, `filter_type`, `resolution`, `uv_map` and
`auto_refresh` there. One declaration rather than a second name prefix,
and free at runtime because `_hashed_props` is memoised per class.

Two consequences, both wanted. Writing `derived_stale_reason` from
inside a compile cannot churn `node_state` and recompile forever. And
dragging a blur slider does not invalidate a node cache or a PS-007
channel bake above the filter before any pixel has changed.

What consumers above *must* see — "these pixels are a different picture
now" — comes back through the existing hook, which `subtree_hash` folds
in:

```python
def hash_parts(self, ctx):
    # The derived image hashes by name like any other datablock, so the
    # stamp of its pixels is what a cache above this layer has to see.
    return [build_stamp(self.derived_image)]
```

### The stamps live on the image, not the node

Four ID properties, written together at commit, constants in
`filters/derived.py`:

- `ps_filter_owner` — `"<tree uuid>:<node uuid>"`.
- `ps_filter_fingerprint` — the structural token: what the build was
  asked for. A JSON object of named parts (`filters/freshness.py::
  fingerprint_parts`): `version` (`FILTER_VERSION`), `filter`, `params`
  (the kind's own fingerprint of its settings), `size`, `uv_map` as
  authored, `below` (`ctx.subtree_hash` of the node feeding the Color
  input, or `"empty"`), `clip`, present only when the layer is
  clipped, and `surface`, present only when the build needed a mesh: the
  mesh's active render UV map, which no hash of the tree covers. A
  compile has no plan, so it hashes `surface` only when the stamp has
  it. Stored as parts rather than one hash so that a mismatch can
  name the part that moved. Cheap to recompute inside a compile, where
  the subtree hashes are already warm.
- `ps_filter_uv_map` — the UV map the pixels were laid out in, resolved.
  The compiled Image Texture's UV Map node needs it, and it has to be
  the map the pixels were built in however the setting has moved since.
- `ps_filter_build` — a hash of the fingerprint and a digest of the
  result's bytes. This is what `hash_parts` returns, so it has to change
  exactly when the pixels do: painting below moves no property, so two
  builds either side of a stroke share a fingerprint and differ only in
  the digest.

They are on the image and not on the node for the reason the compiler
already gives for the artifact: whichever copy of the node and whichever
copy of the image undo restores, the stamp describes the pixels sitting
next to it. It is also what makes duplication and `image.copy()` behave.

An unpacked generated image loses its pixels through `image.copy()` and
comes back black after undo-then-redo (PS-096), while its ID properties
survive both. A stamp with no pixels is the worst failure this feature
can have, so **the commit packs before it stamps**
(`filters/layer_build.py::commit`): the build's own PNG goes into the
image with `image.pack(data=...)`, the image switches to a file source,
and only then are the four stamps written. `derived.is_built` asks for a
stamp *and* for a packed file or loaded pixels, so an image that lost its
pixels reads as unbuilt rather than as wrongly fresh.

A build can come out with exactly the pixels the image already holds —
a stroke below undone before the refresh ran is the ordinary case. The
commit sees that from the stamps alone: equal build stamps mean equal
pixels, so when the image is still packed, not dirty and stamped with
the same four values, it skips the pack, keeps the decoded buffer, and
does not report the image as changed to the filter layers above. It
still clears the pixel flag, under the same rule as a real commit (see
Freshness), and still compiles, because the compile is what tells the
auto job the layer has settled.

### Compile

Only `emit_source` and `emit_blend` are overridden. `emit`
(`nodes/layers/base_layer_node.py`) runs unmodified, so clip
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

`emit_source` also recomputes the structural fingerprint parts, compares
them with the stamp, and writes `derived_stale_reason` when the answer
differs, on the "write only when the value changes" rule. The same spot
tells the auto job about the result: a layer that needs a build (out of
date, or never built) calls `layer_job.notify()` when it has Auto
Refresh on and is switched on, and a layer up to date calls
`layer_job.settled()`. Almost every way a
filter layer goes out of date ends in a compile, so this is where the
auto job normally learns of work; the exceptions are under Rebuild.

Cost: one `subtree_hash` walk per filter layer per compile, memoised —
about what `is_cached` already costs a cached layer. `tests/test_perf.py`
has a budget case for a stack with one built filter layer.

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

Conditions, each checked by name: every node below is Image, Solid,
Folder or a valid filter layer; the blend mode is in
`ALLOWED_BLEND_MODES`; no linked Mask input (PS-015); no clip reaching
outside its own stack. A layer that fails one of these gets a plan on
the bake path, with the reason. Every `uv_map` must also resolve to the
same UV layer, and no source may be UDIM; those two are refused
outright. The names decide first, because they need no mesh:

- nothing names a map: every layer uses the active render map, whatever
  it is, and the result does too;
- every layer below names the same map: the result is laid out in it,
  and a filter layer with its own UV Map left empty follows them;
- two different names: refused, whatever the mesh.

A mesh is needed only when one map is named and some layers below leave
theirs empty, because those use the mesh's active render map
(`gpu_passes/texel_map.py::resolve_uv_map`). The mesh is the layer's
Object, else the active object through `context.get_ps_object`, so an
empty parented to the mesh counts. Either one must be a mesh whose
materials show the tree, directly or nested through group layers
(`context.uses_tree`); another mesh would answer for UV maps the
material never sees. A stored name that finds no object, or finds one
deleted in the viewport but kept in the file by another user (in no
collection), does not count. Nothing searches the file for a suitable mesh: it would scan every
object on every resolve and would have to guess when several meshes
show the tree. `resolve_input` only reads; the build stores the mesh it
used as the Object once it goes ahead (`layer_plan.keep_surface`), in
its first unit. That is the one write before the commit, so a cancelled
Update keeps it, outside any undo step; it names the mesh the next build
would store anyway. On a linked tree the name lasts for the session,
like the result the Update button builds beside it. The Cycles bake
always needs a mesh, from the same place.

`ALLOWED_BLEND_MODES` is an allow-list a mode joins only once its
per-texel parity test against a real Cycles bake of the library group
passes (`tests/test_filter_blend.py`). A mode outside it is not a
refusal; it goes to the bake path.

**Path B, Cycles fallback (`compiler/bake.py`). Not built yet.** Today a
plan on the bake path is refused by `layer_build.steps`, and the auto job
stops on it with the same reason. The design, for when it lands: group
layers, un-parity-tested blend modes, linked masks, and the layer types
that genuinely evaluate surface data (PS-022 to PS-028) bake their input
with Cycles. `bake_subtree(context, tree, node, obj, image, *, margin,
uv_map)` already exists as `bake_node_cache`'s body, extracted with the
cache bookkeeping left behind in `bake_node_cache`.

No compiler change is needed for this: `build_ir` already takes any node
as `bake_target` and links its output refs into `bake:out`, so passing
*the node that feeds
the filter's Color input* bakes exactly the composite below, including
that node's own blend — and for a clip base, the base's own content,
which is what a clipped filter layer wants. Those are the lines PS-007
plans to extend with `bake_target=('channel', name)`; this ticket leaves
them alone.

The bake is 2 s at 2048² and 10 to 14 s at 4096², measured on a default
cube with a two-layer stack, which is a best case. So the fallback keeps
its result: `derived_capture` is a session-only image tagged
`ps_session`, and a parameter change re-runs only the filter passes
against it. The rule that follows: **timers re-filter, operators bake.**
No timer path may start a Cycles bake, ever.

The two bugs found in that code on the way — the bake overwriting the
selection and the active object, and a stray `None` material slot left
on an object that had none — were fixed when `bake_subtree` was
extracted.

### Freshness

Two halves, because `subtree_hash` has one known blind spot and refusing
to paint under a filter layer is not a product.

**Structural.** The fingerprint above, recomputed in `emit_source` and
compared with the stamp. `subtree_hash` (`compiler/core.py`)
already covers a layer added, removed, reordered or muted, a
`fill_color`, another layer's blend mode or opacity, a clip flag, an
image datablock swapped or renamed, and a nested group tree's recompile.
Free, on every compile. Note it hashes the upstream of the Color input,
not the filter node itself: opacity, Amount, `enabled` and Mask are
outside it by construction.

Clip is the exception, and was mistaken for one of those at first. A
clipped layer's Color input carries its clip base's own content rather
than the stack below, because the base holds its blend for the top of
the run to make — the same fact the input section above relies on. So
the filter's own Clip flag decides what it filters, and `subtree_hash`
cannot see it: the node feeding the socket is the same node either way.
The fingerprint carries a `clip` part for it, recorded as
`clip_base(node) is not None` rather than as `is_clip`, because a
clipped layer with no unclipped layer under it composites as if it were
not clipped and filters the same stack. The part is left out of the
stamp entirely when the layer is unclipped, so a stamp written before it
existed still matches for the layer it was already right about, and only
a clipped layer asks to be rebuilt.

That last rebuild is a real cost and worth being honest about: the
*build* has always honoured the clip, because `plan_below` plans the
base with placement `PASS` and the bake takes the same branch. The
pixels of a clipped layer stamped by the older code were most likely
right. What the stamp cannot say is whether the layer was clipped before
or after those pixels were made, and clipped afterwards is the case the
part exists to catch — a layer that read as fresh while showing a filter
of the wrong picture. One rebuild per clipped filter layer is the price
of not being able to tell the two apart. The alternative, bumping
`FILTER_VERSION`, rebuilds every filter layer instead; ignoring a
missing key reports a legacy clipped layer as fresh the moment it is
unclipped.

**Pixels.** `compiler/ir.py` reduces a datablock to its type and name,
so painting into an image below changes no hash. `filters/freshness.py`
closes that without hashing pixels on the hot path:

- Blender tags the painted image in the depsgraph at the end of a
  stroke, and an `Image` row appears in `depsgraph.updates` for the tag.
  `on_depsgraph_update_post` collects those images and calls
  `freshness.note_image_changed(...)`, then `layer_job.notify()`
  unconditionally, which keeps the refresh debounce from starting partway
  through a stroke. That handler is the most carefully tuned function in
  the repo (PS-092, PS-093); the branch only reads the same
  `depsgraph.updates` walk and drops nothing.
- `note_image_changed` sets `derived_stale_pixels` on every built filter
  layer that reads one of the images. Which images a layer reads is
  worked out when an image is tagged, by `composite.plan_below`, rather
  than recorded at build time: the walk only runs when something changed,
  and a recorded set would be one more thing to invalidate. A change to
  *which* images are below is structural anyway.
- `undo.pixels.write_pixels` and the build's own commit call
  `note_image_changed` too, so the addon's own writes — Clear, Fill,
  Invert, Blur, Sharpen, and one filter layer feeding another — are exact
  rather than depending on the depsgraph.
- A build in flight is counted separately. The layer is usually marked
  already, which is why it is being built, so a second stroke partway
  through would change nothing and the commit would clear the flag over
  pixels the build never saw. `freshness.reading(uuid)` is held for the
  whole build, and every stroke below the layer meanwhile bumps
  `freshness.changes(uuid)`; the commit clears the flag only when that
  count is the one the build started with.

Nothing ever clears the flag without a build. A stroke that is undone
before the refresh runs still costs one, but a build that finds the same
pixels commits nothing (the end of the stamps section above), so the
cost is GPU time and never a repack or a cascade to the filter layers
above.

The panel names the reason: the structural part that moved, checked in
the order of `freshness.REASONS` — the layers below, clipping, the
filter, the resolution, the filter settings, the UV map, the object or
its render UV map, a Paint System update — or "the pixels below
changed". The filter and the resolution
come before the settings because each changes the settings as well. The
layer row shows an `ERROR` badge while out of date and a refresh icon
while the auto job builds it (`draw_row_state`).
`paint_system.clear_filter_result` drops the derived image and returns
the layer to pass-through, so a wedged layer is recoverable without
deleting it. It also switches Auto Refresh off, because the auto job
builds any layer with no pixels and would bring the result straight
back.

### Rebuild

`filters/layer_build.py::steps(context, tree, node)` is a generator of
bounded units, driven two ways.

**Explicitly**, by `PAINTSYSTEM_OT_rebuild_filter_layer` (the Update
button), a modal operator on a `wm.event_timer_add(0.01)` that spends
0.05 s of each event on the build, with `wm.progress_update` plus
`workspace.status_text_set`, and ESC or right mouse to cancel. There is
no dialog: the layer's own `resolution` and `uv_map` sit in the panel
right above the button. `execute` runs the whole build in one go, for a
script or a call with no window.

Update first cancels any automatic refresh, because two builds of one
layer would race for the same image. After a successful build it also
clears the auto job's message and switches Auto Refresh back on if the
job had turned it off (`derived_error` set, see the data model).

The steps: resolve (nothing allocated yet, `Refused` by name), composite,
the kind's passes or its own build, the sRGB encode into a byte target,
banded readback into a PNG stream (`filters/png.py`), commit. **Only the
commit writes anything**, so a cancel leaves the previous pixels
bit-identical, and the textures live in the generator's frame, so
closing it gives them back.

The honest budget: a whole-framebuffer read is 0.99 s at 4096² on the
probe machine, so `gpu_passes.core.read_color_bytes` takes a row range
and the readback yields per band, as `run_pass` already bands its draws.
The claim this ticket makes is that no single step runs longer than
roughly 0.2 s at 4K, not 40 ms (see Measurements). The modal holds the
window for the length of the build, which is the price of being
cancellable; the auto path below exists so the common case does not
reach it.

A filter layer below an out-of-date filter layer is handled by the auto
job's order, bottom of each stack first. There is no rebuild-all
operator.

`bl_options = {'REGISTER', 'UNDO'}`, as `PAINTSYSTEM_OT_bake_cache` has.
This is not the case PS-090's no-`UNDO` rule covers. That rule exists
because a memfile step stacked on an *image undo step* costs two Ctrl+Z,
and a derived image pushes no image step at all — its build packs the
pixels rather than registering them. With `UNDO` and a packed image, one
Ctrl+Z takes the whole rebuild back, pixels and stamps together, which is
what a button labelled Update should do.

**Automatically**, by `filters/layer_job.py`, the same generator pulled
from a `bpy.app.timers` tick: debounced 0.4 s, 0.02 s of build per tick,
gated by `auto_refresh`. A candidate is a filter layer that is switched
on, has Auto Refresh on, is not locked, is out of date or not built yet
(`needs_build`), and is not being built by the Update button
(`freshness.building`), taken bottom of each stack first. So adding a
filter layer is enough to get its pixels. Three hard rules:

- A switched-off layer is never a candidate. It renders as a
  pass-through, so a refresh would spend a whole composite and its video
  memory on pixels nothing can show, and switching a filter off to
  compare with and without is the ordinary thing to do with one. The
  layer stays marked out of date, and switching it back on is what asks
  for the refresh that was held back. The panel says so meanwhile,
  rather than sitting on a stale badge with Auto Refresh on and nothing
  happening.

  Asked for by the `enabled` update callback rather than by the compile
  that `mark_tree_dirty` schedules, even though that compile does reach
  `_note_stale` in the ordinary case. A layer standing behind its own
  valid bake cache is never emitted — `enabled` is hashed, so switching
  it off and on again restores the very hash the cache was baked at —
  and it would sit out of date with nothing coming. `lock_layer` needs
  the same treatment for a different reason: it runs `update_painting`,
  which marks nothing at all, because the lock changes where a stroke
  goes and not what the tree compiles to.

- The auto path runs **only** when `resolve_input` reports the composite
  path. A layer whose stack needs a bake gets a message naming the
  reason and is skipped, so a background refresh can never lock Blender
  for ten seconds. Any other refusal from `resolve_input`, such as
  nothing below the layer, is handled the same way. Auto Refresh stays
  on, and the layer waits: it builds as soon as the stack or the scene
  allows it. A refusal is about something that changes; switching Auto
  Refresh off would leave a layer added to an empty folder waiting for
  an Update nobody knows to press. A refusal about the stack is asked
  again by the next compile. One about the scene (no mesh that shows the
  tree, or a mesh without the UV map a layer names) compiles no tree
  when it is fixed, so `on_depsgraph_update_post` calls
  `layer_job.scene_changed()`, which asks again while any layer is
  waiting. Once a build has stored the layer's Object, the selection no
  longer matters. Without one, `resolve_input` falls back to the view
  layer's active object, because a timer's context may have no screen
  and so no `context.object`. A linked tree is skipped: it is read from
  its library again whenever the file opens, so a result built into it
  would be thrown away. A build that has started
  and then fails or is refused still switches Auto Refresh off, because
  trying again would repeat the same failure.
- The viewport keeps showing the previous pixels for the whole job, and
  the commit is one atomic write, so no intermediate state is ever
  visible.

Two bounds keep it from spinning. A layer built `BUILD_LIMIT` (3) times
with no compile finding it fresh in between cannot satisfy its own
check, so the job gives up on it: Auto Refresh off, and a message in the
panel telling the user to press Update. A build overtaken by a stroke or
a setting that moved is dropped and started again after the debounce,
and after `RESTART_LIMIT` (2) restarts in a row the next one runs to the
end anyway, so someone painting in short bursts still sees the filter
catch up.

The job holds the tree name, node name and uuid, and re-fetches the node
when it needs it (PS-090's datablock rule). `cancel_all()` runs from
`on_restore_pre`, so before every undo, redo and file load, and also
when Update starts, from the Cancel button, and on unregister. The
build generator itself still holds the node and tree it started with,
so each tick first checks the node is still there and drops the job
(`_drop`) if the layer or its tree was removed; its commit would
otherwise write through freed memory and crash Blender. The
commit packs, stamps and calls `mark_dirty(tree)` — the repo's existing
timer-safe compile path. A pixel write the timer makes after an undo
step was pushed is lost on a Ctrl+Z together with its stamp, which
leaves the layer out of date and schedules another refresh:
self-healing, and worth keeping that way.

`poll` and the auto job use `gpu_passes.core.gpu_known()`; only a path
about to draw calls `gpu_available()`, because `gpu.init()` crashes
rather than raises on 5.2.1 and 5.3 with no usable driver.

From 5.3 on (Blender commit baafdc000115), reading a file also unbinds
a windowed session's GPU context, and it stays unbound until the event
loop next handles a window's events or draws. Timers, load handlers and
scripts can run in that gap, and so can any operator they call. An
operator run from the UI cannot, because the event loop binds the
window's context before it handles that window's events. So each tick
of the auto job first asks `gpu_passes.core.context_active()` and, with
no context, waits a debounce without touching the GPU, not even to
close a job. Before this, the first texture of a build raised, the
build failed, and the job switched Auto Refresh off with a message
blaming GPU memory. `filters.core.new_texture` now tells the two cases
apart. The gap is short in a running Blender, where the window loop
draws right after the read. A test script runs before that loop starts,
so `tests/test_filter_auto.py` draws the window itself after a reopen.

### Refusals

Raised as `filters.core.Refused`, mostly from `resolve_input` before
anything is allocated, and each naming the layer or image at fault. The
Update button reports one as a `WARNING`; the auto job puts it in the
panel through `derived_error` instead, because a popup from a background
job would interrupt whatever the user was doing.

- "This Blender has no GPU context to run a filter on" (the Update
  button's poll)
- "No GPU context is active right now; try again in a moment" (a texture
  allocation with no bound context, see above)
- "There is nothing below 'X' to filter"
- "Filter layers only work on colour channels"
- "Image 'Y' below is a UDIM image, which is not supported yet" — the
  fallback could not help either; `bake_node_cache` does not do UDIM
  (PS-009)
- "Image 'Y' below has no pixels; is its file missing?"
- "'X' needs a mesh to tell which UV map the layers below use, but …
  Select a mesh that uses this Paint System tree, or set the layer's
  Object", where "…" is "nothing is selected", "'Camera' is not a mesh
  that uses this Paint System tree", or what is wrong with the stored
  Object ("its Object 'Obj' was deleted or renamed", "… was deleted",
  "… is not a mesh", "… does not use this Paint System tree")
- "'Obj' has no UV map named 'UVMap.001'"
- "Layers below 'X' use different UV maps ('UVMap' and 'UVMap.001'), so
  filtering them needs a Cycles bake", or "… on 'Obj' ('UVMap' and the
  render map 'UVMap.001') …" when the mesh decided it
- "'X' is set to UV map 'UVMap.001', but the layers below use 'UVMap'.
  Clear its UV Map to follow them", or "… use the render map 'UVMap' on
  'Obj'. …" — the layers below agree, and only the filter layer's own UV
  Map differs
- "Filtering the layers below 'X' needs a mesh to bake on, because …,
  but …" — a bake plan with no mesh, with the same endings as above
- "Filtering the layers below 'X' needs a Cycles bake, because …" — any
  plan on the bake path, until Path B is built
- "The GPU does not have enough memory for an image this size"
- "'X' asks for a filter this build does not have (KIND)" — a file from a
  newer build

### Lifecycle

**Create.** Nothing is allocated in `create()`. A new filter layer has
`derived_image = None` and compiles as a pass-through, so adding one is
instant and cannot fail. With Auto Refresh on, the auto job then builds
it once things go quiet. The image is made in the commit step through
`create_managed_image`, at one texel because the packed PNG brings its
own size, and switched to a file source when the PNG is packed. It holds
bytes in sRGB like a painted layer image, which halves memory against
float and keeps PS-056's open premultiply-twice bug away from this
feature. The datablock is reused on a resolution change, as
`bake_node_cache` does, so PS-083 and PS-084 stay rare rather than
becoming routine; the pack itself resizes it.

**Duplicate.** `copy()` calls `super().copy(node)` and then
`self.derived_image = self.derived_image.copy()`. The pixels come with
it because the image is packed, and the stamps come with it because ID
properties survive `image.copy()`, so the duplicate is immediately valid
and looks identical. Its owner stamp still names the original until its
first build, which nothing reads in the meantime. Sharing the pointer —
which is what Shift+D used to do for `cache_image` — would let two
layers silently overwrite each other; `PaintSystemLayerNode.copy` now
clears the cache for the same reason. A cache is a re-derivable
artifact, so clearing it is right; a filter result is the layer's
content, so copying it is right.

**Delete.** `PAINTSYSTEM_OT_remove_layer.execute` computes the removed
set, including a folder's descendants, and removes each filter layer's
derived image before `tree.remove_layer_node`. In the operator and not
in `Node.free`: `free` also runs on undo-driven teardown, while the
operator carries `UNDO`, so the memfile step records both the node and
the removed packed image and Ctrl+Z brings back both with pixels intact.
The remove dialog says "Its filtered image goes with it."

**Sweep.** `filters/derived.py::cleanup_orphan_derived()`, modelled on
`cleanup_orphan_artifacts`: every image carrying `ps_filter_owner` that
no filter layer points at, removed at 0 users (or 1 plus a fake user).
Called from `on_load_post` next to `cleanup_orphan_artifacts()`, **and
only there**: a load is the one moment with no undo stack to break, and
sweeping at save time would set up PS-083's recreate-under-a-freed-name
case. It covers the paths the remove operator does not: a node-editor
delete, a channel deletion (which leaves layer nodes alive and is out of
scope otherwise), and a file from a crash or an older build.

**Save.** `paint_system_images()` includes an image tagged
`ps_filter_owner` only while a filter layer still points at it, so an
orphan is never packed into the next `.blend`. A live derived image is
packed at commit already, so the save path is a no-op for it; that is
what makes a reopened file show the filter without a rebuild. (The
fallback's session-only capture would add a second rule here, never to
save it, when Path B lands.)

**Undo and redo.** PS-090 says owners of derived images rebuild them
from their inputs after undo and redo. This design does not, and PS-090
is amended to allow the alternative: the pixels are packed so they
survive, the stamps travel on the same datablock so they always describe
those pixels, `derived_stale_pixels` travels on the node, and
`on_undo_post` calls `mark_dirty()` so the next compile re-checks the
structural half for free. An undo or redo that moves pixels below
leaves the layer marked out of date, and it refreshes; a refresh that
finds the pixels it already holds goes through the no-op commit
(`tests/test_filter_auto.py`, "undo after an automatic refresh").
Running a multi-second GPU or Cycles job inside `undo_post` is precisely
the freeze this whole design exists to avoid.

One thing does have to happen there. A memfile undo restores the packed
file and the stamps, but Blender carries an image's decoded buffer and
GPU texture across it, so after an undo past a rebuild the viewport went
on showing the newer pixels under the older stamp. The commit records
each result's build stamp by `session_uid`, and `on_undo_post` frees the
buffers of every result whose stamp no longer matches the record, so it
decodes the restored file at its next draw. Only those: freeing every
result on every Ctrl+Z would decode each filter layer in the file again
for an undo that touched none of them.

**Fake users.** None, per `docs/ARCHITECTURE.md:20-23`. The node's
pointer is the real user.

**Ownership UI.** No `template_ID` on `derived_image`. `cache_image` has
one, and it lets a user point a derived slot at authored artwork with
nothing marking it read-only. The panel shows the state (Refreshing, Not
built, Out of date, or the size), Update and Clear Result or Cancel
while a job runs, Auto Refresh, and one line under them: the auto job's
message, why a refresh is waiting, the reason to rebuild, or the image
name.

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
  datablocks. `tests/test_smoke_loop.py`, which undoes and redoes
  exactly that operator and calls `check_no_freed_images`, is the test
  most likely to catch a mistake.
- `handlers/node_tree_handlers.py::paint_system_images()` stops returning
  orphaned filter results. That is a deliberate departure from PS-056's
  blanket rule and both PS-056 and `docs/ARCHITECTURE.md` need the
  sentence; `tests/test_images.py` needs the cases.
- `gpu_passes/core.py::read_color` gains an optional row range, and
  `read_color_bytes` reads a byte target the same way. Existing callers
  pass none and are unaffected.
- `filters/core.py` gains `PixelSource.from_texture`, so its module
  docstring ("in and out of the image's own pixels") stops being true and
  is updated.
- PS-090's caller rule about owners of derived images is amended to allow
  pack, re-check and flag.
- Not done yet: `PAINTSYSTEM_OT_bake_cache` (`ops/layer_ops.py`) should refuse to bake a cache over an
  out-of-date filter layer below the target, by name. PS-007 and PS-055
  owe the same check when they land.
- PS-051 becomes a dependency of PS-057 rather than a sibling, and
  PS-053's "in place on the image" line is amended: the painterly filter
  becomes a filter-layer kind as well as a destructive action.
- `docs/BACKLOG.md` gains the PS-057 row under Epic F and the M3
  milestone list.

## Prerequisites

- **PS-084** (a rename invalidates a cache silently). The structural
  fingerprint folds `ctx.subtree_hash`, which serialises a datablock by
  name (`compiler/ir.py`), so renaming any image below a filter
  layer would silently cost a multi-second rebuild. Fixing it first also
  avoids invalidating every stored fingerprint later.
- **PS-083** (delete and recreate under a freed name leaves a stale
  artifact). With a filter layer that case stops being a blank texture
  and becomes pixels computed from an image that no longer exists. The
  design reuses and rescales one datablock to keep it rare, but the
  revalidation gap itself belongs to PS-083.

## Measurements

A rebuild through the composite path, on the probe machine (Blender
5.2.1, headless GPU context), over a two-layer stack: a solid fill and a
2048² image layer. Best of three after a warm-up run, in milliseconds.

| Resolution | GPU composite, filter and readback | Write to the image | Pack | Total |
| --- | --- | --- | --- | --- |
| 1024² | 22 | 4 | 148 | 195 |
| 2048² | 62 | 15 | 627 | 780 |
| 4096² | 395 | 62 | 2182 | 2928 |

**The pack is the rebuild.** It is three quarters of the cost at 1024²
and 2048², and it is the one part that cannot be sliced: `image.pack()`
is a single blocking call inside the last unit of `steps`, so the
generator's 0.02 s budget has no purchase on it. Everything the design
went to trouble over -- banded readback, a cancellable generator, a pool
that hands textures back -- moves 130 ms of a 780 ms rebuild.

The cost is a PNG encode, so it follows the content rather than only the
size. Measured on its own, packing a byte image of pure noise against a
smooth ramp:

| Resolution | Noise | Gradient |
| --- | --- | --- |
| 1024² | 151 ms (3.5 MB) | 26 ms (0.0 MB) |
| 2048² | 636 ms (14.0 MB) | 132 ms (0.1 MB) |
| 4096² | 2561 ms (55.6 MB) | 532 ms (0.3 MB) |

A painted texture sits nearer the noise end. `Image.file_format` does not
change either number: a generated image packs as PNG whatever it is set
to, so there is no cheaper encoding to ask for.

What this means for the two rebuild paths is not the same. A manual
Update runs modally with a progress bar, and a freeze in its last step
reads as the end of work the user asked for. The auto-refresh path has no
such cover: at the default 2048² it would freeze the viewport for a
fraction of a second shortly after every stroke below a filter layer.

The pack is not there for the save -- `handlers.on_save_pre` already
packs every dirty Paint System image, including this one. It is there for
`image.copy()` and for undo-then-redo, both of which drop an unpacked
generated image's buffer while its ID properties survive. Deferring it
would trade the refresh hitch for a window in which those two cases lose
the result; `is_built` already degrades that to "reads as unbuilt" rather
than to wrong pixels, so the failure is a rebuild rather than a
corruption. Not taken here.

**Since PS-053, the build writes the PNG itself.** The encode pass draws
into a byte target, the readback bands are read as bytes, and each band
goes into a PNG stream (`filters/png.py`: the Up filter, zlib at level 1
with the run-length strategy) as it arrives. The commit hands the
finished file to `image.pack(data=...)`, switches the image to a file
source and frees its buffer, so the pack no longer encodes anything and
the image decodes the new file the next time something draws it. The
same stack as the first table, still best of three:

| Resolution | Total | Longest single unit | Decode at the next draw | Packed size |
| --- | --- | --- | --- | --- |
| 1024² | 67 | 28 | 27 | 3.0 MB |
| 2048² | 283 | 57 | 122 | 13.9 MB |
| 4096² | 1094 | 128 | 452 | 43.4 MB |

The source layer is noise, so the decode is at the slow end; a painted
texture decodes faster. The decode is the one blocking call left, and it
is a fifth of the pack it replaced. Storing through bytes rounds a few
values differently from the float readback it replaced, by one step in
255, which `FILTER_VERSION` deliberately does not count.

## Known gaps

- The Cycles fallback (Path B) is not built. A filter layer over a group
  layer, a surface-data layer, a linked mask or a blend mode without a
  parity test is refused. The auto job's message for it still starts
  "Update needed", which Update cannot satisfy yet.
- At Amount 1 a filter layer replaces the stack below outright, so a
  stroke below it does not show until the refresh lands, about half a
  second after the stroke ends plus the build. By design: showing the
  unfiltered stack meanwhile would flicker on every dab.
- A file opened in the UI may mark every built filter layer out of date
  once, if the post-load depsgraph evaluation reports each image as
  updated. Headless runs do not show it, and the cost would be one build
  per layer that ends in the no-op commit. Unverified.
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
  (`nodetree/tree.py`); the sweep collects their images, the
  nodes stay. Pre-existing, out of scope.
- `note_image_changed` marks a whole layer suspect, not a region, so a
  one-texel dab costs a full refresh.
- The decode of a new result cannot be sliced: the first draw after a
  rebuild reads the whole packed PNG in one call, about half a second at
  4096² -- see Measurements. The encode it replaced was five times that
  and sat inside the last unit of every rebuild.
- "Nothing can show it" is read as the layer's own `enabled` only. A
  filter layer inside a switched-off folder is just as invisible and is
  still refreshed. Folding that in means asking for the ancestors of
  every filter layer on every compile, in `_note_stale`, which is on the
  path `tests/test_perf.py` budgets -- so it wants the answer
  `panels/layers_panels.py` already computes for greying a row rather
  than a second walk. Amount 0 is deliberately not counted: fading a
  built filter to nothing is free and reversible by design, and a layer
  someone is sliding is the last one that should go stale.

## Acceptance

- An unbuilt filter layer over a two-layer stack bakes pixel-identical to
  the same stack without it, at 64², for every blend mode on the layer
  below.
- Filter Mix at Amount 1 renders the derived image exactly and at Amount
  0 the stack below exactly; with backdrop alpha 0.5 and filtered alpha
  0.25 at Amount 0.5 it matches the premultiplied-lerp reference within
  the bake's tolerance.
- `tests/test_filter_blend.py`: for every mode in `ALLOWED_BLEND_MODES`,
  the GPU blend matches a Cycles bake of the library group within 2/255.
  A mode that fails is not in the list and goes to the bake path
  instead.
- A clipped layer above a filter layer composites onto the filter's
  pixels, and the stack below the filter is still emitted — the artifact
  has the lower layer's blend group and `Prev Color` linked.
- Changing opacity, Mask or `enabled` recompiles and does not mark the
  layer out of date; changing a `fill_color` below does, and so does
  Clip, which swaps the stack below for the clip base's own content.
- A switched-off filter layer is not refreshed automatically. It stays
  marked out of date, and switching it back on is what asks for the
  refresh.
- Painting into an image below marks the filter out of date and the auto
  job refreshes it. A stroke undone before the refresh runs is refreshed
  by a build that finds the same pixels and leaves the image untouched.
- A saved and reopened file keeps a fresh filter layer fresh, and a file
  saved out of date reopens out of date and refreshes.
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
