# PS-096 Spikes for selection and transform

Epic J. Size S. Milestone M3b. Runs before every other Epic J ticket.

## Goal

The Epic J design (see BACKLOG) rests on a few Blender behaviours that
are not documented. Each spike is a throwaway script run windowed on
Blender 4.2 and 5.2 (both EEVEE Next; 4.2 names the engine
`BLENDER_EEVEE_NEXT`), plus the matrix alpha when it matters. Results, numbers and the Blender versions tested go
into this ticket's Findings section; the scripts are not committed. A
failed spike changes the named ticket before it starts.

## Spikes

1. **Live preview without shader recompiles** (PS-094). Build a material
   whose group mirrors a compiled artifact: an image texture sampled
   through an affine coordinate transform (Mapping, or Vector Math dot
   products for skew). In Material Preview, change the transform values
   from a timer every frame for five seconds.
   - Pass: `bpy.app.is_job_running('SHADER_COMPILATION')` never turns
     true and frames stay under 16 ms on a 4K image.
   - Also measure the one-off stall when the floating nodes are added
     to the artifact, with 5 and 50 layers.
   - If values do recompile: the preview moves to a draw handler that
     draws the floating content through the same shader code as the
     overlays (PS-091), and the material only changes on commit.
2. **Stencil Mask clips native strokes** (PS-091, PS-093). Set
   `image_paint.use_stencil_layer`, `stencil_image` to a soft mask and
   `mesh.uv_layer_stencil`, then run `paint.image_paint` with a stroke
   path in a 3D view override.
   - Pass: texels where the mask is 0 are unchanged, partial mask
     values give partial strength, and `invert_stencil` flips it.
   - Record which mask value means "paint here" and whether the image
     editor ignores the stencil (expected).
   - Check that `sync_canvas` (`handlers/paint_handlers.py`) does not
     undo the stencil settings when the active layer changes.
   - If it fails: painting inside a selection in 3D uses the post-stroke
     restore described for the image editor in PS-091.
3. **Cage gizmo from Python** (PS-094). A `GizmoGroup` with
   `gizmos.new("GIZMO_GT_cage_2d")` in the 3D view on a world-space
   plane (as Blender's area light size gizmo does) and in the image
   editor, bound through `target_set_handler("matrix", ...)` to float
   vector properties on a node.
   - Pass: translate, scale and rotate tweaks write the properties, each
     tweak is one undo step, and snapping with Ctrl behaves.
   - If rotation is missing: add a `GIZMO_GT_dial_3d` next to the cage.
4. **GPU passes and readback at 4K** (PS-050, PS-091, PS-092). With the
   `gpu` module:
   - Draw the evaluated factory cube into a 4096 x 4096 RGBA32F
     `GPUOffScreen` with UVs as positions, writing world position and
     coverage. Time the draw and a read back.
   - Time writing an 8-bit mask and an RGBA float image of 4K into
     `Image.pixels` with `foreach_set`. Target: under 150 ms for the
     mask, since it is written once per selection edit.
   - Check the same passes run under Xvfb with Mesa, which is where CI
     can run GPU tests.
5. **Per-image revision across undo** (PS-090). Paint into an image from
   Python, store a counter in an ID property on the `Image`, push undo,
   repeat, then undo.
   - Pass: memfile undo restores the counter and keeps the image's
     pixel buffer (the painted pixels are not reloaded from disk or lost).
   - Record whether `Image.session_uid` is stable across undo and reload.
   - If the buffer is dropped: revisions move to a scene-level counter
     and the undo handler reapplies the latest entry after each step.
6. **Surface move quality** (PS-094). Render the cube's layer from a view
   into a decal image, then project it back onto the texels without
   moving it, two ways: resampling the decal image (two resamples), and
   looking up the UV each decal pixel saw (one resample, seam edges
   can bleed).
   - Record the maximum and mean error on covered texels for both, with
     linear and closest interpolation, at matching and at 2x density.

## Findings

Run on 2026-09-16 with Blender 5.2.1 LTS and 4.2.23 LTS on Linux. GPUs:
NVIDIA GeForce RTX 2060 (OpenGL, driver 595.84; the Vulkan backend on
5.2 only), Intel UHD Graphics CML GT2 (Mesa 25.2.8) and llvmpipe (Mesa
25.2.8, `LIBGL_ALWAYS_SOFTWARE=1`). Spikes 1 to 4 ran windowed, 5 and 6
in background mode. The scripts stayed in `/tmp` and are not committed.

Outcome: 1 PASS, 2 PASS, 3 FAIL for the built-in cage (the binding and
the undo step pass), 4 PASS, 5 PASS, 6 decided for the UV lookup decal.
The decisions taken from them are listed at the end.

### Spike 1: live preview without recompiles (PASS)

Setup: a material salted per run (a unique value node) so the shader
cache cannot hide a compile, 4K image layers, 1, 5 and 50 layers (215
nodes at 50). Viewport changes were checked through
`screen.screenshot_area`, because `render.opengl` evaluates the scene on
its own and the splash screen covers the viewport at startup.

- Changing Mapping values from a timer never starts a
  `SHADER_COMPILATION` job on either version. Frame cost while values
  change, for 1/5/50 layers: 5.2 NVIDIA 12/14/27 ms (control without
  changes 9–10 ms); 4.2 NVIDIA 11/12/25 ms; 5.2 Intel 31–37 ms (control
  31 ms). The 16 ms target holds on a discrete GPU up to a handful of
  layers; the 50-layer cost is the material itself.
- Attribute path: an Attribute node with `attribute_type='VIEW_LAYER'`
  reading a custom property on the scene updates the viewport with no
  tag at all. `attribute_type='OBJECT'` reading an object property needs
  `object.update_tag()`; tagging the material's node tree does nothing
  for either. Cost at 50 layers: 9–11 ms per frame instead of 27, no
  compile. This is the preview path for PS-094.
- One-off compile when the floating nodes are added: 5.2 NVIDIA 0.3 s
  at 1–5 layers and 1.9 s at 50; 4.2 NVIDIA 1.0–3.0 s; 5.2 Intel
  0.36–1.9 s. The frame hitch reaches 0.7–0.8 s at 50 layers. Adding the
  same nodes again later is served from the cache (0 s). PS-094 adds
  the floating nodes when the tool activates, not when a drag starts.

### Spike 2: Stencil Mask clips native strokes (PASS in the 3D view)

- With a soft mask as `stencil_image`, `paint.image_paint` strokes leave
  texels at mask 0 exactly unchanged (maximum paint 0.000) and the
  painted strength follows the mask within 0.01 at every sampled ramp
  value. Same on 4.2.
- Blender's stencil protects where the mask is white, so PS-091 sets
  `invert_stencil=True` to get mask 1 = paint.
- The image editor ignores the stencil, as expected. `sync_canvas` in
  `handlers/paint_handlers.py` does not touch the stencil settings.

Follow-ups from the PS-091 milestone 1 probes (5.2.1 and 4.2.23, and 5.3
alpha on Vulkan where noted):

- Texture paint reads the stencil at 8 bits. A feathered mask painted
  through it matches the float mask within 0.002, the step of one
  level; a float stencil image gains nothing. Same on 5.3 alpha.
- A tiled (`<UDIM>`) stencil image is sampled from tile 1001 only: UVs
  in 1002 read 1001's pixels. One stencil cannot clip per tile, so PS-091
  blocks painting on UDIM layers with a selection.
- Memfile undo keeps tool settings (`use_stencil_layer`,
  `invert_stencil`, `canvas`, `stencil_color`) at their current values,
  but restores mesh data (`uv_layer_stencil_index`) and scene data. A
  `stencil_image` that survives the undo keeps its current value; one the
  undo frees falls back to the step's value rather than dangling. So a
  backup of tool settings on the scene is wrong after an undo, and
  PS-091 keeps it on the window manager.
- A generated stencil image written with `foreach_set` is dirty: saving
  packs it and quitting asks to save it. A FILE image read from a PNG
  stays clean.

### Pixel undo (spikes 2 and 5; changes PS-090)

- Memfile undo restores ID properties and RNA values and keeps every
  image's pixel buffer, but never restores pixels. A `foreach_set` write
  is in no undo step. On 5.2, undoing a native stroke made after a
  scripted write reverts the scripted write as well: the stroke's step
  restores from the previous image step's tiles.
- `bpy.ops.image.invert(invert_r=False, invert_g=False, invert_b=False,
  invert_a=False)` under `temp_override(edit_image=image)` changes no
  pixel and pushes an image undo step holding the image. With it,
  scripted writes, native strokes and memfile steps undo and redo in the
  right order on 5.2 and 4.2, in object and texture paint mode (every
  step of a scripted undo and redo walk matched its expected state).
- Cost per push on a 4K byte image: 5.2 75–120 ms and +88–90 MB per
  step; 4.2 40–70 ms and +96 MB. The steps live in Blender's undo memory
  (`undo_memory_limit`). Edit > Undo History labels them "Invert
  Channels".
- `Image.update()` refreshes the display and GPU buffers, `Image.save()`
  writes to disk and `ed.undo_push` pushes a memfile step: none of them
  records pixels.
- An operator with the `UNDO` option that also calls the invert makes
  two steps per call: the image step, then the operator's memfile step
  on top. Without the option there is exactly one step, and a document
  change made in the same call is not undoable.
- 4.2 in texture paint mode: `ed.undo_push` and the `UNDO` option push
  empty image steps, so no document change is undoable in that mode (a
  Blender limitation that already applies to the addon's layer
  operations there). 5.2 pushes memfile steps in paint mode.
- `Image.session_uid` is stable across undo and new after a file reload.
  An ID property on an image survives save and reload.
- Undo past the creation of a generated image, then redo, brings the ID
  back with black pixels unless the image was packed; a packed image
  comes back intact (5.2 and 4.2). Write-once images (PS-091 `RASTER`
  results, PS-094 sources) are packed right after their write.
- Side finding for PS-056: a float image with alpha below 1, packed and
  reloaded, has its colour channels multiplied by alpha again (0.8
  reloads as 0.4 at alpha 0.5, both versions). Byte images reload
  unchanged.

### Spike 3: cage gizmo from Python (built-in cage FAILS)

- `GIZMO_GT_cage_2d` bound with `target_set_handler("matrix", get, set)`
  works in the 3D view (group option `'3D'`, basis on the cube face) and
  in the image editor (2D group, basis in region pixels from
  `region.view2d`, recomputed in `draw_prepare`). The bound matrix is
  `matrix_offset`, applied after `matrix_basis`.
- One undo step per drag: `Gizmo.use_undo` (5.2 only) pushed a memfile
  step per drag and each Ctrl+Z restored the exact previous matrix. 4.2
  has no `use_undo`; calling `ed.undo_push` from a timer registered when
  `is_modal` turns false in `draw_prepare` gives the same result: one
  step per drag in both editors, each Ctrl+Z restoring the exact
  previous matrix and each redo the next, with the other editor's matrix
  untouched (checked in object mode; in texture paint mode 4.2 would
  push an empty image step, see the pixel undo findings).
- Failures, in both editors: the cage's modal ignores the Ctrl and Shift
  tweak flags (no snapping, no aspect lock); rotation never triggered in
  the 3D view and only once in the image editor; corner handles in the
  image editor registered once in several attempts (a hit zone a few
  pixels wide, under the rotate handle). Translate and edge scale were
  reliable. A `GIZMO_GT_dial_3d` next to the cage would add rotation but
  not the modifiers or the corners.
- Decision: PS-094 draws its own `bpy.types.Gizmo` subclass.

### Spike 4: GPU passes and readback at 4K (PASS)

Factory cube (12 triangles) and a 1M-triangle grid, 4096 x 4096:

| | 5.2 NVIDIA GL | 5.2 NVIDIA Vulkan | 5.2 Intel | 5.2 llvmpipe | 4.2 NVIDIA GL |
|---|---|---|---|---|---|
| draw texel map, cube | 1.2 ms | 1.5 ms | 18.8 ms | 50 ms | 1.2 ms |
| draw texel map, 1M tris | 6.1 ms | 5.8 ms | 55 ms | 210 ms | 56 ms |
| read back RGBA32F | 103–111 ms | 144–154 ms | 180 ms | 105 ms | 240–300 ms |
| mask pass to R8 | 0.9–1.4 ms | 12–25 ms | 9 ms | 19–21 ms | 12–24 ms |
| read back R8 | 7–14 ms | 12–13 ms | 12–17 ms | 7 ms | 24 ms |
| upload RGBA32F texture | 40 ms | 61 ms | 91 ms | 112 ms | 68 ms |
| R8 bytes to RGBA float32 (numpy) | 120 ms | 145 ms | 130 ms | 127 ms | 122 ms |
| `foreach_set`, 4K byte image | 50 ms | 64 ms | 49 ms | 56 ms | 63 ms |
| `foreach_set`, 4K float image | 18 ms | 20 ms | 21 ms | 19 ms | 21 ms |
| `foreach_get`, 4K byte image | 47 ms | 56 ms | 55 ms | 48 ms | 54 ms |

- The face-centre check passes on every configuration: maximum error
  9.77e-4, coverage 0.375.
- 4.2 buffer rule: a `gpu.types.Buffer` with more than one dimension
  reports its memoryview with reversed strides on 4.2. `np.frombuffer`
  raises `BufferError` and `memoryview.tobytes()` returns transposed
  data after 3.4–4.7 s at 4K. Reading into a preallocated
  one-dimensional buffer is correct and copy-free on both versions:
  `buf = gpu.types.Buffer('FLOAT', w * h * 4)`,
  `fb.read_color(0, 0, w, h, 4, 0, 'FLOAT', data=buf)`,
  `np.frombuffer(buf, np.float32)`. On 4.2 `GPUFrameBuffer.viewport_set`
  takes no arguments (binding sets the viewport). `gpu` buffers cannot be
  created in background mode.
- Mask write: R8 read back, expansion to a float RGBA array and
  `foreach_set` add up to about 150–200 ms per selection edit at 4K on
  every configuration, over the 150 ms target on byte images. PS-091
  keeps the mask on the GPU and writes the derived image only for the
  stencil, as a float image.
- Mesa: llvmpipe runs every pass. Xvfb itself was not run (none
  installed here); PS-080's windowed job confirms it.

### Spike 5: per-image revision across undo (PASS)

An ID property on the image is restored by every undo and redo (3, 2,
1, 2 across the walk), the pixel buffer is kept (`has_data` stays true
and the painted pixels remain), on 5.2 and 4.2. `session_uid` is stable
across undo and new after reload. The fallback is not needed, and after
the pixel undo findings above PS-090 needs no revision at all.

### Spike 6: surface move quality (decided: UV lookup decal)

Setup: the cube's layer captured from a fixed view into a 1024 x 1024
decal and projected back without moving, with a numpy raycast (the GPU
path will be faster; only the quality numbers matter). Errors are in
8-bit units over covered texels; "seam" texels lie within two texels of
a UV island boundary.

- Colour decal (two resamples), 1x density: mean error 17–31 with almost
  every texel above 2 (the second resample blurs). At 2x density with
  closest interpolation in both passes: mean 0.06, maximum 211 on 186
  seam texels.
- UV lookup decal (one resample) with a seam guard (UV distance or seam
  edge), 1x: mean 0.14, 459 texels above 2, all at seams. 2x with
  closest: every texel exact. 2x with linear: mean 0.07, p99 0.03,
  maximum 93 on 745 seam-adjacent texels; renormalising the linear
  weights across the seam edge brings that to mean 0.03, maximum 35.
- float16 UV storage is unusable: 0.4 texel mean error at 4K and 75% of
  closest lookups land on the wrong texel. The decal is RGBA32F.
- Decision: PS-094 captures the UV each decal pixel saw (RGBA32F, twice
  the view's texel density, seam-edge guard). `CLOSEST` moves content
  losslessly; `LINEAR` stays within 1/255 away from seams.

### Decisions (2026-09-16)

- PS-094 handles are a custom `bpy.types.Gizmo` subclass (draw,
  `test_select`, `invoke`, `modal`) instead of `GIZMO_GT_cage_2d`.
- Pixel writes register with Blender's image undo through the no-op
  invert. A commit is exactly one undo step and changes no document
  data; the transform's floating state is session state, and drag undo
  inside the tool is the tool's own history. PS-090 loses the numpy
  stack and the revision property.
- Preview values travel through a view-layer attribute; the floating
  nodes are added when the tool activates.
- Surface moves use the UV lookup decal at 2x density.
- Write-once images are packed right after their write.
