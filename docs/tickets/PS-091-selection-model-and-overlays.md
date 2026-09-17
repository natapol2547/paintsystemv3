# PS-091 Selection model and overlays

Epic J. Size L. Milestone M3b.

## Status

Built in slices, since the four halves of this ticket are independent.

1. **The document model. Done.** `props/selection.py` and
   `tests/test_selection_model.py`; `PaintSystemNodeTree.selection` holds
   it. 33 checks on 5.2.1 and 4.2.23 (65 after slice 4). Outlines turned
   out to want an ID property rather than a collection of typed point
   groups: a lasso carries hundreds of points and memfile undo copies
   every step, so a PropertyGroup per point would be copied on every
   push. Slice 2 replaced `ops_hash`'s rounded JSON with exact binary
   digests and removed `mask_hash`, which went stale after undo and
   reload.
2. **Rasterisation. Done.** `selection/outline.py`, `selection/raster.py`,
   `tests/test_selection_outline.py` and `tests/test_selection_raster.py`.
   One `R32F` pass per op evaluates coverage at each texel centre in
   closed form: box and ellipse analytically (Eberly's distance for the
   ellipse), lasso by even-odd parity and per-cell nearest-segment lists
   that numpy builds per outline. Neither the winding-number fan nor the
   jump flood was needed. Masks are cached under a digest of the ops, so
   undo and redo run no pass while the earlier mask is still cached. 148
   checks on 5.2.1 (Intel GL, headless and windowed), 4.2.23 windowed and
   5.3 alpha (NVIDIA Vulkan), and 149 on Vulkan through lavapipe on 5.2.1
   and 5.3 alpha, where one more check fails the run if Blender fell back
   to OpenGL. CI runs that lavapipe step from 5.2, because the OpenGL
   jobs miss Vulkan-only faults such as an `R32F` texture read back as
   bytes. Outlined `VIEW` ops raised `UNSUPPORTED` until PS-093 added
   them; `FACES`, `RASTER` and `TRANSFORM` ops still do until PS-097 and
   PS-094 add them. `gpu.init()` turned out to arrive in 5.2, not 5.0,
   and to crash Blender instead of raising when EGL cannot start.
3. **Clipping native strokes. Done.** `props/stencil.py`,
   `selection/stencil.py`, `tests/test_selection_stencil.py` (70 checks)
   and `tests/test_selection_stencil_ui.py` (17 checks, windowed). A
   native `paint.image_paint` stroke across a feathered box follows the
   mask within 0.003 on interior texels, and a stroke through the block
   image paints nothing.
4. **Overlays. Done.** `selection/overlay.py`,
   `selection/overlay_shader.py`, the Selection box in the add-on
   preferences, `tests/test_selection_overlay.py` (39 checks) and
   `tests/test_selection_overlay_ui.py` (19 checks, windowed).

Slices 3 and 4 shipped together as milestone 1 with the session below
and `paint_system.select_all` (`ops/selection_ops.py`), tested by
`tests/test_selection_session.py` (55 checks headless on 5.2.1) and
`tests/test_selection_session_ui.py` (17 checks, windowed). Check counts
are from 5.2.1; the builds without a background GPU context skip the
mask checks headless and run every GPU file again windowed. The whole
suite passes with `--ui` on 4.2.23, 4.5.13, 5.0.1, 5.1.2 and 5.2.1, and
the GPU files pass headless on Vulkan through lavapipe on 5.2.1. On 5.3
alpha two checks of `test_selection_overlay_ui.py` failed at the time,
"three strokes build no batch" and "a modifier change builds it once":
5.3 alpha reports an Object geometry update for every native stroke, so
the overlay rebuilt its batch after each stroke there. Milestone 2 fixed
that by keying the batch on the surface's content (see Overlays), and
both checks pass on 5.3 alpha. Blender 5.0 does not save a mesh's
stencil UV map at all, so on 5.0 a user's stencil UV map does not
survive a save, with or without a selection.

**Milestone 2. Done.** Selections drawn in the 3D view: the Rectangle,
Ellipse and Lasso Selection tools, outlined `VIEW` ops rasterised
through the texel map, and surface content keys for every per-object
GPU cache. PS-093 records the design, the measurements and what it
found; this ticket records what changed in the model, the session and
the consumers. The whole suite passes with `--ui` on 4.2.23, 4.5.13,
5.0.1, 5.1.2 and 5.2.1, the GPU files pass headless on Vulkan through
lavapipe on 5.2.1, and on 5.3 alpha the headless suite and every GPU,
selection and tools file windowed on Vulkan pass.

**The session (`selection/session.py`).** One selection is live at a
time: the selection of the active object's active tree, applied to the
active layer. Everything that can change what it shows calls
`session.notify()`, which only registers a timer, so a burst of calls
gives one sync and it is safe from operators, handlers, message bus
callbacks and draw callbacks. The tick resolves a `State` (scene, tree,
object and image `session_uid`, UV map, size, tile, digest, paint mode,
a reason, and whether the built mask is empty) and stops when it equals
the last one and the mask is still cached. The digest of a selection
with outlined `VIEW` ops includes the key of each op's surface
(PS-093), resolved here, so an edited mesh changes the digest. Otherwise
it builds the mask once, calls `stencil.sync`,
then `overlay.sync`, and tags the 3D views and image editors. A digest
that fails for a lasting reason is remembered (64 entries) so it is not
built on every tick, except the `raster.GEOMETRY_REASONS` (`SURFACE`,
`VIEW`, `EDIT_MODE`), which depend on objects rather than ops and
several object states share one digest; `GPU_ERROR` is retried 4 times at 0.25 s, then the
panel says "GPU error, change the selection to retry". A consumer that
raises is logged, and the next sync reaches the consumers again even
with an equal state. `notify(force=True)` forgets the last state, so
undo, redo, a file read and the stencil's own settings reconcile every
consumer although the state compares equal. Nothing else builds a mask:
the stencil and the overlay only read what the session built.

## Goal

A soft selection on the active layer's image that limits scripted image
tools (transform, fill, filters) and clips native brush strokes, shown
in the 3D view and the image editor.

## Model: the selection is compiled, like the material

The selection is document data; its mask is derived from it, the same
way the artifact is derived from the tree.

- `PaintSystemSelection` on the tree: `feather` and `antialias`
  defaults, and `ops`, an ordered collection of operations. It stores no
  image. The selection applies to the active layer, whichever that is,
  the way a Photoshop selection stays put while the user switches
  layers: the mask is built at the size of the active layer's image
  (`raster.image_size(image, tile)`) and sampled through that layer's
  UV map (`context.layer_uv_layer`: the map the layer names, else the
  active render map). A stored image pointer would go stale on a layer
  switch and need a sync of its own. The operations:
  - `kind`: `BOX`, `ELLIPSE`, `LASSO`, `FACES`, `RASTER`, `TRANSFORM`,
    `INVERT`, `ALL`.
  - `mode`: `REPLACE`, `ADD`, `SUBTRACT`, `INTERSECT`.
  - `space`: `UV` (image editor) or `VIEW` (3D view).
  - Shape data: `points` (region or UV coordinates), and for `VIEW` the
    region size and the view and projection matrices at the time, so the
    op can be rasterised again after undo or reload.
  - `feather` (in texels of the target image for `UV` ops, so a
    feather looks the same on the canvas at any layer resolution),
    `antialias`, `through` (ignore occlusion, like X-ray in mesh
    selection).
  - `feather` is limited to 1024 pixels. `INVERT` and `TRANSFORM` act on
    the mask before them, so their mode means nothing and `add_op` stores
    `ADD`.
  - `RASTER` points at a write-once greyscale `Image` (magic wand and
    other pixel-derived results). It is never modified, so undo only
    needs the pointer. It is packed right after the write: PS-096 found
    that an unpacked generated image comes back black after an undo past
    its creation and a redo.
  - `TRANSFORM` carries the matrix of a committed move (PS-094) so the
    selection follows the content without copying pixels.
- Because the ops are document data, selection undo is Blender's undo,
  and the selection is saved with the file like mesh selection. A
  `REPLACE` of a box, ellipse, lasso, faces, raster or all op drops the
  ops before it. The mask is cached under a digest of the ops, the size
  and the UDIM tile, so after undo, redo or a reload the restored ops
  find their mask or build it, and no stored hash can go stale. Memfile
  undo never restores pixels (PS-096), so the stencil image reads a file
  named after the digest, and the file path it last loaded is tracked by
  `session_uid` outside the file; an undo that restores an older path
  reloads it. Undo differs by version; see Undo below.
- `invert()` drops a trailing `INVERT` and otherwise appends one, so
  inverting twice gives back the same ops, digest and cached mask. An
  empty selection inverts to `ALL`, and a selection ending in an `ALL`
  with `REPLACE` or `ADD` inverts to empty, so inverting a full
  selection leaves painting unrestricted.
- **A mask that selects nothing is no selection** (milestone 2). When no
  texel of the built mask reaches 0.5/255, nothing the 8-bit stencil
  could keep, `State.empty` is set: the stencil is restored, so painting
  works everywhere, the overlay draws nothing, and the Selection section
  shows the info line "Nothing selected" with no error icon. The ops stay
  on the tree, so undo and redo stay consistent and a later Add drag
  builds on them. A tool drag over empty background is the usual cause.
  `SelectionMask.is_empty()` reads the 8-bit mask back once per mask and
  remembers it, sharing the read the stencil does anyway (about 15 ms at
  4K), and the session copies `empty` from its last state while the
  digest is unchanged, so emptiness is never checked per draw. Empty is
  not a reason: a selection that exists but cannot be used still blocks
  painting.

## Rasterisation (`selection/raster.py`)

GPU passes build the mask at the image's size, or at a UDIM tile's.

- One full-screen pass per op evaluates the op's coverage `c` at every
  texel centre and combines it with the previous mask `m` in the same
  draw: replace writes `c`, add `max(m, c)`, subtract `min(m, 1 - c)`,
  intersect `min(m, c)`, invert `1 - m`. Masks are `R32F` textures cached
  by content under a 512 MiB budget, so appending an op runs one pass, and
  undo, redo or removing the last op run none while the earlier mask is
  still cached.
- Coverage comes from a signed distance `s` in texels: `s > 0` for a hard
  edge, else the smoothstep of `(s + hw) / (2 hw)` with
  `hw = max(feather, antialias ? 1 : 0) / 2`, so a feather rises from 0
  to 1 over its width, centred on the outline. Box and ellipse distances
  are analytic. Lasso and polygon are filled by even-odd parity from
  per-row crossing tables and measure distance through per-cell segment
  lists. With a hard edge a box or ellipse leaves out texel centres that
  lie exactly on its outline, while the lasso's half-open even-odd rule
  counts a centre on a right or bottom edge but not on a left or top one.
  The same rectangle as a box and as a lasso can therefore differ by a
  column and a row when its edges pass through texel centres.
- Masks match the same formulas evaluated in float64 on the CPU
  (`tests/selection_reference.py`). Hard edges agree exactly on every
  texel further than 1e-3 from the outline. Soft edges agree within 1e-5
  for boxes and lassos, and for an ellipse within `max(1e-4, 6e-7 r)`,
  with `r` its longer radius in texels: the float32 root is off by about
  `3e-7 r` in distance, and which texels come closest to the bound
  differs between backends. The soft bounds hold from a half width of
  0.25 texels (anti-alias on, or a feather of 0.5 or more); below that
  they grow as `0.25 / hw`, because the slope `1.5 / (2 hw)` amplifies
  float32 distance error.
- `UV` ops rasterise in texel space. `VIEW` ops (milestone 2,
  `selection/view_raster.py`, PS-093) rasterise the shape in screen space
  at region size (feather measured in screen pixels, as the user drew
  it), then reach texels through the texel map (PS-092): each texel is
  projected with the stored view, sampled from the screen shape, and kept
  only when it faces the view and the painted object does not hide it.
  With `through` every texel inside the shape is kept, back faces and
  hidden faces included. The op also stores its `object` pointer and UV
  map name, its view as object-to-view so the selection stays on the
  same texels when the object moves, and its digest includes the
  surface's content key.
- `FACES` draws the UV triangles of the mesh's selected faces
  (`use_paint_mask` face selection) in texel space.
- Until PS-097 and PS-094 add them, `FACES`, `RASTER` and `TRANSFORM`
  ops make `get_mask` raise `MaskUnavailable` with reason `UNSUPPORTED`;
  a limited edit never runs as if there were no selection. The digests
  already include the `RASTER` image's `session_uid` and the `TRANSFORM`
  matrix.
- A `VIEW` op whose surface cannot be used raises one of
  `GEOMETRY_REASONS`: `SURFACE` when its object or UV map is gone (the
  object deleted or not in the view layer, not a mesh, the UV map
  renamed or removed, or dropped by a modifier), `VIEW` when its view
  cannot be used (a region size of 0, a world matrix or stored view that
  cannot be inverted) and `EDIT_MODE` while its mesh is in Edit Mode.
  `peek_mask` returns None for them, and the session does not remember
  them.
- Any other mask that cannot be built raises `MaskUnavailable` too, with
  a reason and a message fit for the UI. An op whose `points` ID property
  is not a flat array of even length, such as a list of pairs written to
  it directly, is `UNSUPPORTED` at that op with "A selection operation
  has malformed points", and `peek_mask` returns None for it rather than
  raising. `GPU_ERROR` means the GPU raised during the build, as it does
  with no active context in the tick of a file load on 5.3 alpha. It is
  transient and not remembered, so the next call tries again.
- `availability()` tells a `poll` or a draw callback whether `get_mask`
  can build, without building, running the self-test or starting a
  background GPU context. On 5.2.1 and 5.3 alpha `gpu.init()` crashes
  Blender instead of raising when EGL has no usable driver, so only a
  path about to draw may call it. An empty answer is therefore not a
  promise: an untested GPU or an unstarted background context reports
  available.
- The first build of a session runs a self-test: two chains, at 64 x 64
  and 140 x 64, compared at chosen texels with float64 values from
  `tests/selection_reference.py`. They check the soft and hard edge
  profiles, all four modes, `ALL` and `INVERT`, an add of two fractional
  values, the ellipse root, lasso distance tables in 32-texel cells, and
  lasso parity across three span columns and two rows of the key
  texture. A GPU that fails gets `SELF_TEST` for the rest of the session
  instead of wrong masks. A hard ellipse, cells wider than 32 texels,
  distance tables past one data texture row and tiles other than 1001
  are not checked. `VIEW` ops have a self-test of their own
  (`view_self_test`, PS-093), run before the first build that holds one;
  its failure gives only `VIEW` selections `SELF_TEST`.
- The stencil file is named by the same digest the session uses,
  surface keys included, so a `VIEW` selection gets a new file after its
  mesh changes.
- The mask stays a `GPUTexture` for passes and overlays. For the
  stencil, the session tick writes it once per selection change through
  `SelectionMask.read_bytes` (an `R8` pass and a UBYTE read, 7–24 ms at
  4K; a UBYTE read of the `R32F` texture itself returns zeros on Vulkan)
  to an 8-bit greyscale PNG, `<digest>.png` under
  `bpy.app.tempdir/paint_system_selection`, written atomically with
  numpy and zlib level 1. `.PS Selection Stencil` is a Non-Color FILE
  image that reads it. A generated image written with `foreach_set`
  does not work: it is dirty after every write, so saving packs it into
  the file and quitting asks to save "1 modified image", and clearing
  the flag by packing costs 550 ms at 4K on 5.2 and up to a second on
  4.2. A FILE image is never dirty, and a file named by digest survives
  undo. A file that already exists is reused rather than read back
  again. Files past 256 MB are pruned oldest first, never the current
  one or the block file. Build, read back, PNG and reload cost 108–155
  ms at 4K on 5.2 Intel and 0.4–1 s on 4.2 Intel, once per change,
  never per stroke.

## Clipping native strokes

`selection/stencil.py`, called by the session tick.

- 3D view: while a selection exists in texture paint mode, the mask
  file image is the Stencil Mask (`use_stencil_layer`, `stencil_image`,
  `mesh.uv_layer_stencil_index` set to the layer's UV map, and
  `invert_stencil=True`, because Blender's stencil protects where the
  mask is white). Every Blender brush is clipped with soft edges: PS-096
  spike 2 measured texels at mask 0 unchanged and the painted strength
  following the mask within 0.01, on 5.2 and 4.2. Texture paint reads the
  stencil at 8 bits, so a feather has 256 levels, with a maximum error
  of 0.002 against the float mask. `sync_canvas` leaves the settings
  alone.
- **Policy: the selection owns the stencil while it applies.** A user
  edit to those settings notifies the message bus
  (`ImagePaint.use_stencil_layer`, `invert_stencil`, `stencil_image`,
  `Mesh.uv_layer_stencil_index`), and a forced sync sets them back. The
  user's own values come back when the selection stops applying: it is
  cleared, texture paint mode is left, the scene switches, a file is
  saved or the add-on is disabled. Writes only happen where a value
  differs, so the notifications they cause settle after one more sync.
- **Backups live where undo treats them like the setting they back up.**
  Memfile undo keeps tool settings as they are but restores scene and
  mesh data (PS-096 findings below). The image paint settings are backed
  up on `WindowManager.paint_system` (`PaintSystemWindowManagerSettings`,
  one entry per scene), which undo keeps and files do not save, so
  undoing past a selection's creation still gives the user's stencil
  back. A mesh's stencil UV map is backed up on the scene
  (`PaintSystemSceneSettings.stencil_meshes`), which undo restores along
  with the mesh, by mesh pointer so a rename does not lose it. The window
  manager entry keeps a copy of the mesh backups, used only when the
  scene is removed while the selection holds it. A scene copied from a
  held scene carries the selection's settings, not the user's, so its
  backup is Blender's defaults.
- **Block mode.** When a selection exists but its mask cannot be used
  (`state.reason`: UDIM, a missing layer, image or UV map, an operation
  not supported yet, too complex, no GPU, a GPU error while it retries,
  and for `VIEW` ops an object or UV map that is gone, an invalid view
  or Edit Mode),
  the stencil image shows `block.png`, one black pixel, and nothing gets
  painted, so a selection that cannot be shown never lets paint land
  outside it. The panel says why. When the mask file cannot be written
  it blocks too, with a logged warning; when not even `block.png` can be written, `.PS Selection
  Block`, a generated black image with no pixels of its own and never
  dirty, blocks instead. Nothing retries the mask file until the state
  changes, and the panel shows no message for that case. A mask that
  selects nothing is not a reason and does not block: the stencil is
  restored as if there were no selection.
- **UDIM is unsupported.** A tiled stencil image is sampled from tile
  1001 only (UVs in 1002 read 1001's pixels), so clipping per tile is
  impossible with one stencil. A UDIM layer with a selection reports
  "UDIM layers are not supported yet" and blocks painting (PS-009).
- **Saving.** `save_pre` calls `restore_all()` first, so the file keeps
  the user's settings, the stencil image has no users and is not
  written. `save_post` runs a forced session sync, which applies the
  stencil again before the next stroke. `bpy.data.is_dirty` stays False
  and quitting does not prompt. Automatically Pack Resources and Pack
  Resources pack the stencil image anyway, and a packed image reloads
  its packed copy whatever its path, so the image is unpacked before it
  is pointed at another file.
- **Loading and crash recovery.** `load_post` calls `on_file_loaded()`:
  it forgets the previous file's backups and loaded paths, and a scene
  whose stencil image is the selection's (a file written without
  `save_pre`, such as an autosave) goes back to Blender's defaults with
  its mesh UV maps restored, because the user's settings went with the
  old session. It then subscribes again, since a file read clears the
  message bus. The forced sync that follows builds the mask from the
  saved ops and applies it.
- Image editor: Blender's 2D painting has no stencil, and M1 does not
  clip there. The tool header shows "Selection does not limit painting
  here" in Paint mode while the selection holds the stencil. It scrolls
  out of view in a narrow editor. The fallback that writes
  `mix(copy, image, mask)` back after a stroke belongs to the image
  editor tools in PS-097.

## Overlays (`selection/overlay.py`)

Draw handlers only, so showing or hiding a selection never changes the
material and never recompiles a shader.

- Both editors draw marching ants along the selection's outline, plus a
  wash inside it when "Tint Opacity" is above 0 (off by default, since
  any tint hides the true colour of what is being painted). The ants
  are found from mask samples one screen pixel to either side and
  dashed along the screen axis closest to the outline's tangent. The
  ants are composited over the wash: mixing their colours instead put
  the tint colour in the ants' soft edge even at opacity 0.
  `overlay_shader.ANT_GLSL` holds the dash math, so PS-093's drag
  preview can crawl in step with the committed ants
  (`overlay.ant_style(context)`). Every read is a `texelFetch`, because
  linear filtering of float textures is an optional device feature on
  Vulkan and Metal, and the push constants fit the 128 bytes Vulkan
  guarantees.
- 3D view (`POST_VIEW`), in texture paint mode only, on the object the
  selection applies to, and only while `show_selection_3d` is on. The
  evaluated mesh of that object, faces whose material uses the tree
  only, is drawn twice. First, a coverage pass writes the mask value of
  the front surface at the nearest texel into a region-size `RGBA16F`
  `GPUOffScreen`. Then a screen pass draws the mesh again over the
  viewport, depth-tested `LESS_EQUAL` without depth writes, and finds
  the outline from the four neighbouring screen pixels of that buffer.
  Neighbours in texture space drew ants along every UV seam; nearest
  coverage also keeps gutter texels out of a selected island. The screen
  pass shifts clip-space z by Blender's own polygon offset
  (`GPU_polygon_offset_calc`, distance 1), so it does not fight the
  surface in Solid mode. The batch is cached per object in local space,
  so moving the object costs nothing, and keyed by the surface's content
  key (below).
- Image editor (`POST_PIXEL`): one pass over the image the selection
  applies to, in View and Paint mode, with a manual bilinear read of the
  mask and neighbours at `±dFdx/dFdy`, which lines up with the texels at
  every zoom.
- Preference colours are sRGB (`COLOR_GAMMA`) and converted to linear
  before upload, because what a `GPUShaderCreateInfo` shader writes is
  treated as linear.
- An 8 Hz timer tags only the areas that show the selection while one
  is visible, and stops on the first tick that finds none.
- Handlers draw what `raster.peek_mask` returns and never build. When
  what they would draw no longer matches the session's state (the mask
  is missing, was built for other ops, or the image's live size differs
  from `state.size`, since `Image.scale` reports no event), they call
  `session.notify()` and draw nothing. That is the safety net for a
  writer that forgot to notify and the only thing that sees a resize.
  With no overlay visible, a resize waits for the next notify from
  another source.
- Failures show in the panel only, not in the viewport.
- Preferences (`PaintSystemPreferences`, "Selection" box):
  `show_selection_3d` ("Show Selection in 3D View", on),
  `selection_wash_color` ("Selection Tint", 0.25, 0.55, 1.0),
  `selection_wash_opacity` ("Tint Opacity", 0), `selection_ant_color_a`
  ("Outline Dash Color", black) and `selection_ant_color_b` ("Outline Gap
  Color", white). `overlay.settings()` falls back to these defaults when
  the add-on has no preferences entry.
- Milestone 1 dropped the batch after every undo, redo and entry to
  texture paint mode, which all report a geometry update, and rebuilt it
  lazily (48–98 ms at about 100k triangles). On 5.3 alpha every native
  stroke also reports an Object geometry update, so the batch was
  rebuilt after each stroke there. Since milestone 2 the batch is stored
  with the surface key it was built for (`gpu_passes/surface.py`,
  PS-093), and a geometry update only marks the surface suspect. A draw
  whose key is not fresh draws the cached batch and requests a resolve;
  the timer tick resolves it, and only a changed key rebuilds the batch.
  After a real surface change (a vertex edit, a modifier change, an undo
  past one) the ants therefore show the previous outline for one frame.
  A stroke on 5.3 alpha costs one array read and compare (23–26 ms at 1M
  triangles), and no rebuild. The same applies to the selection mask of
  a `VIEW` selection: draws peek at surface keys, the session resolves
  them.
- A selection that selects nothing (`state.empty`) draws nothing and
  starts no timer.

## UI

- The Paint System tab of the 3D view sidebar has a "Selection" section
  in texture paint mode, after Brush and Color: All, None and Invert
  (`paint_system.select_all`). When a selection exists but cannot be
  used, the header gets an error icon and the body a short line from
  `session.label(state)`, such as "Layer's UV map is missing". Milestone
  2 adds "Selection's object or UV map is gone", "Selection's view is
  invalid" and "Leave Edit Mode to use the selection". A selection whose
  mask selects nothing shows the line "Nothing selected" with an info
  icon and no error icon.
- The section does not draw feather or anti-alias: `ALL` and `INVERT`
  have no edge, and PS-093's tools carry feather, anti-alias and Through
  in their tool header.
- `select_all` polls only in Object and Texture Paint mode
  (`ops.selection_ops.undoable_mode`): an edit mode has an undo stack of
  its own, where a step for a selection edit restores nothing and costs
  a Ctrl+Z. `SELECT` does nothing when the ops are already one `ALL`, and
  `DESELECT` nothing when they are empty.
- No keymap items. A, Alt+A and Ctrl+I in texture paint belong to
  Blender's face mask selection (`paint.face_select_all`); binding them
  would shadow it. Users can bind `paint_system.select_all` themselves.

## Undo

Selection edits are undoable through Blender's memfile undo, except in
texture paint mode before 5.1:

| Blender | Texture paint mode | Object mode |
|---|---|---|
| 4.2, 4.5, 5.0 | Undo steps through image undo only and never restores tree data. A pushed step restores nothing and costs a Ctrl+Z that does nothing. | Undoable |
| 5.1, 5.2, 5.3 alpha | Undoable, in order with strokes | Undoable |

`ops.selection_ops.UNDO_OPTIONS` is `{'REGISTER', 'UNDO'}` from 5.1 and
`{'REGISTER'}` before, and `push_undo(context, message)` pushes a step by
hand only before 5.1 and outside texture paint mode. Every selection
operator uses both, and PS-093's tool drags follow the same rules as
`select_all`: from 5.1 a drag is an undo step in order with strokes, and
Adjust Last Operation replaces the last op. On 4.2 to 5.0 a selection
edit in texture paint mode therefore has no undo step: the selection
stays when strokes are undone, and there is no dead Ctrl+Z. Undo, redo
and a file read force a session sync, because memfile undo restores the
ops, `Image.filepath` and the mesh stencil UV map but keeps tool
settings.

## Acceptance

- A lasso on a 4K image shows the ants in both editors within
  one timer tick after release.
- Add, subtract, intersect and invert combine as the soft set operations
  above; undo and redo restore the previous mask exactly, without a pass
  when it is cached.
- A feathered box has mask values rising from 0 to 1 over the feather
  width.
- A native stroke across the selection edge in the 3D view leaves texels
  with mask 0 unchanged.
- With no selection, no timer runs and the draw handlers return at once.
- Reopening the file rebuilds the same mask from the saved ops.
- Saving with a selection leaves no stencil image in the file, and
  Blender quits without asking to save.
- Disabling the add-on while a selection clips strokes gives the user's
  Stencil Mask settings back in every scene.
- A UDIM layer with a selection reports that it is not supported and
  blocks painting.
- A mask that cannot be built, whether for an op not supported yet,
  malformed points or a GPU error, makes `get_mask` raise
  `MaskUnavailable` with a message; nothing edits the image as if there
  were no selection.
