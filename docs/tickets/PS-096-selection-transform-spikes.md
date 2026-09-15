# PS-096 Spikes for selection and transform

Epic J. Size S. Milestone M3b. Runs before every other Epic J ticket.

## Goal

The Epic J design (see BACKLOG) rests on a few Blender behaviours that
are not documented. Each spike is a throwaway script run windowed on
Blender 4.2 (EEVEE Legacy) and 5.2 (EEVEE Next), plus the matrix alpha
when it matters. Results, numbers and the Blender versions tested go
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

Not run yet.
