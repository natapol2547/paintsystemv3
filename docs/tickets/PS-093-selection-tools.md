# PS-093 Selection tools

Epic J. Size M. Milestone M3b.

## Status

Milestone 2 of PS-091 is done: Rectangle, Ellipse and Lasso Selection
in the 3D view in texture paint mode, the `VIEW` rasteriser behind them
and the surface content keys both need. The polygon lasso, the magic
wand, select by faces, UV island or material, the image editor tools
and clipping in the image editor moved to PS-097 on 2026-09-17, as nice
to have and not scheduled.

Built in three parts, merged in order, each leaving the suite green:

- **Surface keys** (`gpu_passes/surface.py`, `tests/test_surface.py`).
  Texel maps, depth batches and overlay batches are keyed by the content
  of the evaluated surface, so a stroke, a stroke undo or entering
  texture paint no longer drops them. This also fixed PS-091's open 5.3
  alpha failure, where the overlay rebuilt its batch after every stroke.
- **The view rasteriser** (`selection/view_raster.py`, the `VIEW` branch
  of `selection/raster.py`, `tests/test_selection_view_raster.py` and
  the windowed `tests/test_selection_view_windowed.py`).
- **The tools** (`tools/`, `tests/test_selection_tools.py` and the
  windowed, event-simulated `tests/test_selection_tools_ui.py`).

Check counts on 5.2.1: `test_surface.py` 61,
`test_selection_view_raster.py` 79 headless and 22 in
`test_selection_view_windowed.py`, `test_selection_tools.py` 67 and
`test_selection_tools_ui.py` 59 (52 on 4.2 to 5.0, which skip the undo
step and Adjust Last Operation checks). The whole suite passes with
`--ui` on 4.2.23, 4.5.13, 5.0.1, 5.1.2 and 5.2.1. The GPU files pass
headless on Vulkan through lavapipe on 5.2.1, and the tools test passes
windowed on Vulkan on 5.2.1. On 5.3 alpha the headless suite passes, and
every GPU, selection and tools file passes windowed on Vulkan. The
first `--ui` runs on 4.2.23 and 4.5.13 failed one check, the preview's
dash crawl: a missed timer tick drew the second frame one whole dash
period later, so both frames looked the same. The check now looks at up
to three later frames and passed on repeated runs on both builds.

Decisions taken with the user while building it:

- **Through selects everything inside the shape**, back faces and faces
  hidden behind others included, like X-ray select in Edit Mode. It skips
  both the facing and the depth test.
- **Only the painted object occludes.** Other objects in front of it do
  not hide texels from a selection.
- **The overlay shows the previous outline for one frame** after a real
  surface change (a vertex edit, a modifier change, an undo past one).
  Masks and stroke clipping always use the fresh surface; only the ants
  lag.
- **Tool labels are "Rectangle Selection", "Ellipse Selection" and
  "Lasso Selection"**, so they read differently from Blender's own Box
  Select and Lasso Select, which appear next to them with the same icons
  when Paint Mask is on.
- **A `VIEW` op keeps a pointer to its object.** See Surface and object
  below for what that costs.
- **A selection whose mask selects nothing is no selection.** See Empty
  selections below.

Found while building it:

- The facing test's surface step at a UV island edge. The texel map's
  forward neighbour of an edge texel can lie on another face, and its
  bent geometric normal failed the facing test for a whole texel row on
  a face seen at a grazing angle. `surface_step` now takes, of the two
  neighbours that are covered, the step lying more nearly along the
  surface; a neighbour outside the map counts as uncovered, and with
  neither covered the step is zero. `tests/selection_reference.py`
  mirrors it, and a headless check selects a grazing face up to its
  edges. The self-test cannot see this.
- Texels within about two pixels of a projected face edge can be lost
  to the four-tap depth filter and `TEXEL_SLOPE_CAP` on a face seen
  nearly edge on, as designed. The windowed region check leaves them
  out and reports how many.
- An op drawn on an object scaled to zero stored a view that cannot be
  inverted, and the build raised out of the session timer. A singular or
  non-finite world matrix or stored view is now `VIEW`, and the tools
  refuse to add an op on an object scaled to zero.
- A linked duplicate in Edit Mode turns the shared mesh into an edit
  mesh without a position attribute. Surface keys treat any mesh in Edit
  Mode as no surface, and the op reports `EDIT_MODE`.
- While a `VIEW` selection shows `SURFACE`, `VIEW` or `EDIT_MODE`, any
  depsgraph update notifies the session, because the fix (scaling the
  object back, linking it into the scene again) reports no geometry
  update.
- `frame_change_post` runs on the render job's thread for every rendered
  frame. The handler returns at once for a render depsgraph.

Known limitations:

- Disabling the add-on while a workspace that no window shows is still
  in texture paint with one of these tools active: when that workspace
  is shown again it has the brush id but not the brush keymap, so drags
  do nothing until the user picks a tool or re-enters texture paint.
  Setting up a tool in a workspace no window shows crashes Blender 5.2,
  and the only other fix changes that workspace's mode.
- A tree shared by two objects: when an object with `VIEW` ops that is
  not the active object is deleted, nothing notifies the session, so the
  selection keeps its last state until the next notify.
- Selection undo in texture paint mode on 4.2 to 5.0, as for every
  selection edit (PS-091, Undo).
- Metal is not tested at all. 4.3 and 4.4 are covered by CI only.

## Goal

Box, ellipse and lasso selection in the 3D view in texture paint mode,
and select all, none and invert. The polygon lasso, the magic wand,
select by face or UV island and the image editor tools are PS-097.

## v3 design

### Tools and operators (`tools/`)

- A `tools` package: `__init__` (registration and the unregister
  reset), `shapes` (outline geometry, no `bpy`), `preview` (the drag
  preview), `select_ops` (the operators) and `workspace_tools` (the
  `WorkSpaceTool` classes). The top-level `__init__.py` registers it
  after `keymaps`. `paint_system.select_all` lives in
  `ops/selection_ops.py` (PS-091 milestone 1).
- `WorkSpaceTool` subclasses for `VIEW_3D` in `PAINT_TEXTURE` mode:
  `paint_system.select_box` ("Rectangle Selection"),
  `paint_system.select_ellipse` ("Ellipse Selection") and
  `paint_system.select_lasso` ("Lasso Selection"), with Blender's
  `ops.generic.select_box`, `select_circle` and `select_lasso` icons.
  They form one group, registered after `builtin_brush.mask` with a
  separator. 4.2 has no such item, so the group goes to the end of the
  toolbar and Blender prints "could not find 'after'".
- Each operator only appends a `VIEW` op to the selection (PS-091), then
  calls `push_undo(context, bl_label)` and `selection.session.notify()`,
  and returns without GPU work. The session tick builds the mask and
  brings the stencil and overlays in step, so every tool shares feather,
  anti-alias and undo.
- `execute` works from the operator's stored properties alone (points,
  region size, view and window matrix, all `HIDDEN` and `SKIP_SAVE`), so
  Adjust Last Operation and the tests run it without a region. It
  cancels with a warning when `session.resolve_target` gives a reason,
  when there is no object, and when the object is scaled to zero.
  Tests that repeat the last operation call
  `bpy.ops.ed.undo_redo('EXEC_DEFAULT', True)`; without the second
  argument the repeat pushes no undo step.
- `bl_options = ops.selection_ops.UNDO_OPTIONS` plus `push_undo`, as
  PS-091's Undo section describes. Selection edits in texture paint mode
  are not undoable on 4.2, 4.5 and 5.0; the alternative is a Ctrl+Z
  that does nothing.
  Outlines go in through `add_op(points=...)` or `set_points`, which
  store a flat float array. A list of pairs written straight to
  `op['points']` is malformed, and PS-091 reports the op as
  `UNSUPPORTED`.
- The tool keymap (`bl_keymap`, created by Blender in the add-on
  keyconfig and active only while the tool is) has exactly five items:
  | Input | Result |
  |---|---|
  | LMB drag | the tool's operator, `mode='REPLACE'` |
  | Shift + LMB drag | `ADD` |
  | Ctrl + LMB drag | `SUBTRACT` |
  | Shift+Ctrl + LMB drag | `INTERSECT` |
  | LMB click | `paint_system.select_all(action='DESELECT')` |

  It binds no A, Alt+A or Ctrl+I: in texture paint those belong to
  Blender's face mask selection (`paint.face_select_all`), which a tool
  keymap would shadow, and Blender's own select tools bind no select-all
  keys either. `select_all` is reached from PS-091's Selection section
  and F3 search.
- Default items the tool keymap shadows while one of the tools is
  active, by select mouse preset:
  - Left click select (factory): the paint curve `transform.translate`
    and a plain click's `view3d.select`. Shift and Ctrl clicks still
    reach `view3d.select` for face masking.
  - Right click select: in "3D View", `view3d.cursor3d` (LMB click) and
    `view3d.select_lasso` on Ctrl+LMB drag (add) and Shift+Ctrl+LMB drag
    (subtract); in "Paint Face Mask (Weight, Vertex, Texture)",
    `view3d.select_lasso` on Shift+Ctrl+LMB drag. The brush tool already
    takes those gestures in texture paint.
- The tool header shows feather (0 to 1024 screen pixels), anti-alias
  and Through ("Select faces hidden behind others too"). The operator
  saves them per tool, which is what the header edits. No mode row: the
  modifiers pick the mode, which is `SKIP_SAVE`, so a mode row would look
  live but do nothing on a drag.
- `invoke` needs `context.region_data`. The start point comes from
  `mouse_prev_press_x/y`, where the button went down before the drag
  threshold. Esc or a right click during a drag cancels with no op and
  no undo step.
- Box and ellipse take Shift pressed after the drag starts for a square
  or circle, and Alt for drawing from the centre, the way Blender's add
  object tool does. A modifier held from the start counts only once it
  has been released and pressed again, so the Shift of an Add drag does
  not also make a square.
- The lasso keeps a point once it is `LASSO_STEP` (2) pixels times the
  UI scale from the last one. Past `LASSO_MAX_POINTS` (4096) every other
  point is dropped and the step doubles, so the spacing stays even along
  the whole outline.
- While dragging, `tools.preview.Preview` draws the outline in the
  region with a `POST_PIXEL` handler, through `overlay_shader.ANT_GLSL`
  with the colours and phase of `overlay.ant_style(context)`, so it
  crawls in step with the committed ants. The outline is turned
  clockwise first, because the dash direction follows the tangent, and
  each segment is a `TRIS` quad `round(2 * LINE_HALF_WIDTH * ui_scale)`
  wide, extended by half a width at each end: 1 pixel lines read thinner
  than the ants at UI scale 2. The overlay's redraw timer runs only while
  a selection shows, so the preview owns a window timer at
  `overlay.REDRAW_INTERVAL` and the dashes keep moving while the mouse
  is still. The mask is built once, after release.
- A shape that encloses nothing is cancelled, not stored: a box or
  ellipse with zero width or height, and a lasso with fewer than three
  distinct points or with every point on one line.
- **Unregistering.** A workspace keeps the id of its active tool per mode
  after the tool is unregistered, and Blender does not reset it: the dead
  tool stays active with no keymap and drags stop painting. `unregister`
  first sets the brush (`builtin.brush` from 4.3, `builtin_brush.Draw`
  before) in every window whose object is in texture paint with one of
  these tools, through `wm.tool_set_by_id` under a `temp_override` of
  that window's own 3D view. This also works when the add-on is disabled
  from the Preferences window; passing `screen=` for a temporary screen
  raises instead. It then writes the brush id into every workspace's
  texture paint tool ref, which takes effect when texture paint is next
  entered there (see the limitation above).
- Selection operators build no mask. The session calls `get_mask`
  inside `try/except MaskUnavailable`, and the Selection section shows
  the reason. Tools that edit pixels through the selection (PS-094,
  PS-095) check `availability()` in `poll` or `invoke` and call
  `get_mask` the same way, reporting its message. An empty
  `availability()` is not a promise: it runs no self-test and starts no
  background GPU context, because `gpu.init()` crashes Blender instead of
  raising when EGL cannot start. `GPU_ERROR` is transient, so a later try
  can succeed; `SELF_TEST` lasts for the session, and `availability()`
  reports it from then on.

### Empty selections

A tool drag that covers no texels (empty background, or only back faces
without Through) still appends its op. A mask with no texel at or above
0.5/255, which is nothing the 8-bit stencil could keep, counts as no
selection: the stencil is restored and painting works everywhere, the
overlay draws nothing, and the Selection section shows the info line
"Nothing selected" with no error icon. The ops stay on the tree, so undo
and redo stay consistent and a later Add drag builds on them. This
applies to every mask, `UV` and `VIEW` ops alike, and matches PS-091's
rule that inverting a full selection leaves painting unrestricted.

`SelectionMask.is_empty()` is read back once per mask and remembered,
sharing the 8-bit read the stencil does anyway (about 15 ms at 4K), and
the session copies `State.empty` from its last state while the digest
is unchanged. Nothing checks emptiness per draw. A read back that raises
is `GPU_ERROR` and retried.

### Select all

`paint_system.select_all(action='SELECT'|'DESELECT'|'INVERT')` shipped
with PS-091 milestone 1, with no keymap items (see above). `SELECT`
appends `ALL` with `REPLACE`, `DESELECT` clears the ops, and `INVERT`
calls `PaintSystemSelection.invert()`, which toggles a trailing `INVERT`
and turns nothing and `ALL` into each other.

### Surface keys (`gpu_passes/surface.py`)

A surface key is a 16-byte BLAKE2b digest of what a per-object GPU cache
is built from: evaluated positions, `.corner_vert`, face offsets, one UV
map, material indices, sharp faces and edges and custom normals
(`corner_normals` when `has_custom_normals` on 4.2 to 4.4, the
`custom_normal` attribute from 4.5). The world matrix is not in it.

- Arrays are read through the attribute API, never `to_mesh()`,
  `MeshLoop.vertex_index` or `MeshPolygon.material_index`, which cost 5
  to 25 times as much (`loops.foreach_get('vertex_index')` alone is
  127–134 ms at 1M triangles).
- Three costs. `peek_key` (draw callbacks) returns the last key and
  whether it is fresh: resolved, not marked suspect since, and the
  identity token (counts and data pointers of the evaluated mesh)
  unchanged; about 36 µs, never reads arrays. `resolve_key` (timers,
  operators) returns a fresh key as it is, else reads the arrays and
  compares them with the ones the key was made from, and hashes only on
  a real change: 0.3 ms at 10k triangles, 23–26 ms to read and compare
  at 1M, 60–64 ms with the hash on 5.2.1, about 73 ms at 1M on 4.2.23.
  `mark_suspect` (handlers) sets a flag and never evaluates.
- UVs compare within `UV_TOLERANCE` (1e-6), NaN-aware: 4.2 to 4.5
  re-evaluate a Subdivision Surface with UVs that differ by up to 6e-8.
- `depsgraph_update_post` marks an object with a geometry update suspect,
  `undo_post`, `redo_post` and `frame_change_post` mark every object
  suspect (an animated deformation fires no depsgraph update), and
  `load_post` and `load_post_fail` forget every entry.
- Entries are keyed by object, UV map and view layer (scene
  `session_uid` and view layer name), because each view layer's
  depsgraph evaluates the mesh with its own data pointers. A draw's
  `request` resolves on its own view layer's depsgraph in a zero-interval
  timer, which tags 3D view and image editor redraws and notifies the
  session only when an entry's resolved key changed.
- An entry records that it was resolved even when there is no surface (a
  non-mesh, a mesh in Edit Mode through any object, an evaluated mesh
  without the UV map), so `peek_key` reports that None as fresh and a
  draw caches its empty result instead of asking on every redraw.
- Memory: the arrays cost about 35 MB per entry at 1M triangles. At most
  `ENTRY_LIMIT` (8) entries keep them, about 280 MB in the worst case,
  least recently resolved first to lose them. An entry without arrays
  keeps its key and token, so it still peeks fresh and its next read
  gives the same key; `KEY_LIMIT` (256) caps entries at all.
- Who pays: the session's digest calls the provider for outlined `VIEW`
  ops only, but the overlay keys every batch it draws by surface key, so
  on 5.3 alpha, which reports an Object geometry update for every
  stroke, any selection that draws ants pays one resolve per stroke.
  That is still cheaper than rebuilding the batch per stroke.

### Rasterising `VIEW` ops (`selection/view_raster.py`)

- `VIEW` ops store the `object` pointer and the resolved `uv_map` name
  along with the outline in region pixels, the region size, the view as
  **object-to-view** (`rv3d.view_matrix @ matrix_world` at commit) and
  `rv3d.window_matrix`. Views are stored as recorded, which is exact
  under quad view, region overlap and UI scale 2. Feather is in region
  pixels.
- The digest of an outlined `VIEW` op adds Through, the region size, the
  matrices, the UV map name and the surface key (`raster.view_key`), or
  `NO_SURFACE_KEY` without one; `session_uid` is never hashed.
  `DIGEST_TAG` is version 2. Timers, operators and the stencil pass the
  resolving provider, so a committed mask never uses a stale surface;
  draw callbacks pass `view_key(peek=True)`, which requests a resolve
  and returns the last key (the one-frame lag above). The stencil file
  is named by the same provider digest.
- **Object-anchored views.** At build time `ViewSpec.from_op` uses the
  stored object-to-view times the inverse of the object's world matrix
  now, so the selection stays on the texels it was drawn over when the
  object moves, rotates or scales, as a `UV` op does. A transform
  changes no digest and needs no rebuild; view-space depth is unchanged
  by it. `view_eye` gives the eye: in perspective the translation of the
  inverse view, in orthographic the normalised column 2 of the inverse
  view. Row 2 of the view is the direction only while its 3x3 part is
  rigid, and flipped facing for thousands of texels after a non-uniform
  or mirrored scale.
- Three stages per op:
  - A: raster's shape and lasso shaders, compiled again with
    `SIGNED_DISTANCE`, write the outline's signed distance at region size
    (`R32F`), clamped to the soft edge's half width plus `REACH_PAD`
    (1.5) pixels.
  - B, skipped with Through: the painted object's depth at region size,
    `q` (`-1/d` in perspective, `d` in orthographic) and its screen slope
    in an `RG32F` colour target with a `DEPTH_COMPONENT32F` depth target.
  - C: a banded pass at mask size projects each texel-map position
    (PS-092), reads A with a manual bilinear read, tests facing against
    the geometric normal and depth with a four-tap percentage-closer
    filter over B, and combines like a `UV` op. Margin texels (coverage
    at most `MARGIN_ALPHA`) skip the depth test, so the margin band next
    to a selected island is selected with it. With Through, C keeps only
    the shape coverage.
  Two `MAT4` alone fill the 128 bytes of push constants Vulkan
  guarantees, so the matrices go through a std140 uniform buffer
  (`PSViewBlock`, 176 bytes) and push constants keep the ints.
- **Depth bias.** Relative `DEPTH_REL_BIAS` (1e-5) everywhere; the
  absolute `DEPTH_ABS_BIAS` (1e-6) in orthographic views only. In
  perspective a fixed bias on `-1/d` grows with the square of the
  distance, and at 100 times scale selected a layer 0.2 units behind the
  visible surface with Through off. The orthographic term at large scene
  scale, and the perspective change on Vulkan and Metal, are not probed.
- **Region clipping.** Texels that project outside the recorded region
  are 0, so a shape dragged past the viewport edge shows ants along that
  edge.
- **Region targets.** `_region_targets` keeps textures only, for at most
  `TARGET_SETS` (2) region sizes, least recently used dropped: 2 x 127
  MiB at 4K. The depth `GPUFrameBuffer` is created per build, because an
  OpenGL framebuffer only works in the context that created it.
- `texel_map.get_texel_map` and `get_position_batch` are called with
  `fallback_to_active=False`: a silent fallback to another UV map would
  change the selection without telling the user.
- **Cost.** The first build per surface pays for the texel map and depth
  batch: 170–620 ms at 100k triangles on Intel GL, in the tick after
  release. A warm 4K build takes 14–94 ms on Intel GL and 6–35 ms on an
  NVIDIA GPU under Vulkan.

### Surface and object

`raster._problem` checks outlined `VIEW` ops before any GPU work and
returns one of `raster.GEOMETRY_REASONS`, which the session never
remembers, because several object states share one digest and fixing
the cause changes no op:

- `SURFACE` ("The object or UV map this selection was drawn on is gone";
  panel "Selection's object or UV map is gone"): no object, not a mesh,
  the object not in the view layer (compared by identity, so a linked
  object sharing a local object's name still counts), the UV map missing
  from the mesh, or no surface key outside Edit Mode (an evaluated mesh
  that lost the UV map, such as through Remesh or Geometry Nodes).
- `VIEW` ("This selection's view is invalid"): a region size of 0 or
  less, or a world matrix or stored view that cannot be inverted.
- `EDIT_MODE` ("Leave Edit Mode to use this selection"): no surface key
  and the mesh is in Edit Mode, through this object or a linked
  duplicate.

As for every unusable selection, painting is blocked and the panel says
why. `peek_mask` returns None for such an op, since the digest alone
would still find the old mask.

The op's `object` is a real ID pointer, so deleting the object in the
viewport leaves it in `bpy.data` with the op as its only user: it is
saved with the file, Purge does not remove it while the op exists, and
appending the material or tree brings the object along. In exchange a
rename keeps working and undoing the delete brings the selection back.

### Self-test

`raster.view_self_test()` runs `view_raster.self_test_chain` for an
orthographic and a perspective scene (view depth 1.50–2.27), each with
and without Through, and compares 32 x 32 results with float64 values
from `tests/selection_reference.py` within `SELF_TEST_TOLERANCE` (1e-4).
It is memoised apart from PS-091's self-test: a failure gives `VIEW` ops
`SELF_TEST` for the session and leaves `UV` selections working. The
worst error measured is 2.5e-6 orthographic and 4.0e-6 perspective.

It cannot catch: dropping one of the two depth slope terms, `REACH_PAD`,
a smooth normal used for facing, the `clip.w > 0` in-front test, an eye
vector used as the eye position, a missing perspective divide in the
texel slope, an eye that ignores view rotation, or the surface step
choice at island edges. `view_eye` runs on the CPU and the tests check
it on 1000 random orthographic views with scaled objects.

### Merge order

The tools could not ship before the `VIEW` rasteriser: until then a
`VIEW` op was `UNSUPPORTED`, and PS-091 blocks painting for a selection
it cannot use. That order was held by merging the rasteriser first and
by the check in `test_selection_tools_ui.py` that a box dragged over the
cube builds a usable mask, which no software renderer gate may skip.

## Acceptance

- Each tool works in the 3D view; Shift adds, Ctrl subtracts, Shift+Ctrl
  intersects. Done.
- A box over a cube corner selects the matching texels on all three
  visible faces across UV islands, and none on the faces behind. Done.
- With `through` on, the same box also selects the hidden faces. Done.
- A 200-point lasso on a 4K image is ready in under 50 ms after release
  on a hardware GPU, not counting the first build of a session (shader
  compile and self-test) or of a surface (texel map). CI runs llvmpipe
  and asserts only 2 s.
