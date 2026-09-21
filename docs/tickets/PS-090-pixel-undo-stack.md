# PS-090 Pixel undo for scripted image edits

Epic J. Size S. Milestone M3b.

## Status

Done. `undo/pixels.py` and `tests/test_pixel_undo.py`; the handlers in
`handlers/node_tree_handlers.py` clear its session state after undo, redo
and a file read. 24 checks pass on 5.2.1 and 4.2.23.

Two things the spikes had not reached, found while building it:

- Undo and redo restore the pixels recorded by the step they arrive at, so
  an image needs a step holding the pixels from *before* the first scripted
  write or that write is not undoable (the spikes always had a native
  stroke in front of the write, which hid this). `write_pixels` pushes that
  baseline the first time it touches an image, and the handlers forget the
  record after undo, redo and a file read, since the step may no longer be
  on the stack. N writes to an image therefore cost N+1 steps, every one of
  them a visible Ctrl+Z.
- `bpy.ops.image.invert` crashes a background Blender that has no undo
  stack, on 4.2 and 5.2. Blender builds the stack when something first
  pushes to it and does that for itself only in an interactive session, and
  reading a file frees it again. `ensure_undo_stack` pushes one memfile
  step in background mode before the first registration. Without it any
  headless use of the addon that writes pixels would take Blender down.

Measured on this machine, background, first registration on a 4K byte
image: 146 ms on 5.2, 208 ms on 4.2. The spikes' 40-120 ms were repeat
registrations in a windowed session, where the buffer is already realised.
The test's ceiling is 500 ms so CI on a slow runner does not flake.

## Problem

Blender's undo records image pixels only in image undo steps, which
paint strokes push. Pixels written from Python through `foreach_set`
are in no step: Ctrl+Z after a scripted edit restores nothing, and on
5.2 undoing a later native stroke reverts the scripted write as well
(PS-096).

The Epic J design keeps this ticket small. Selections are document
data, which memfile undo covers. The floating transform is session
state (PS-094) and needs no entry. Only writes that change a layer's
pixels need registering: a transform commit, a fill, a filter (PS-050),
a merge, a cut. Write-once images (a wand result, a paste source) are
packed after their write, so a memfile redo that recreates them brings
their pixels back.

## v3 design

Decided 2026-09-16 from PS-096: Blender's own image undo holds the
pixels, and the addon only registers its writes with it.

- `undo/pixels.py`:
  - `write_pixels(image, pixels)`: pushes a baseline step when the image
    has none, then `foreach_set`, `image.update()` and a second
    `push_undo_step`. Marks the image dirty so PS-056 saves or packs it.
    Raises `ValueError` on a buffer that is not
    `width * height * channels` long; returns False when the write landed
    but could not be registered, so the caller can say the edit is there
    and not undoable.
  - `push_undo_step(image)`: calls `bpy.ops.image.invert` with all four
    channels off under `temp_override(edit_image=image)`. The operator
    changes no pixel and pushes an image undo step that snapshots the
    image, exactly as a stroke does. Blender undoes and redoes the step
    itself, in order with native strokes and memfile steps, on 5.2 and
    4.2, in object and texture paint mode.
  - `ensure_undo_stack()`: in background mode only, pushes one memfile
    step so the stack exists. See the Status section.
  - `forget_baselines()` after undo and redo, `forget_undo_state()` after
    a file read: both called from `handlers/node_tree_handlers.py`.
  - `pack_write_once(image)`, not built yet: packs a write-once image so
    it survives undo past its creation and redo. Used instead of
    `write_pixels`, not with it: such an image takes no step of its own.
    It comes with the first write-once image tool (a wand result, a paste
    source), and `save_image`'s pack branch shares its pack-and-warn code
    then. `tests/test_pixel_undo.py` already checks the Blender behaviour
    it relies on, with a plain `Image.pack()`.
- Rules for callers:
  - An operator that writes pixels has no `UNDO` option. With it,
    Blender pushes a memfile step on top of the image step and every
    commit costs two Ctrl+Z.
  - The operator changes no document data in the same call. A document
    change that belongs with a pixel write (PS-094's selection follow)
    is its own step, pushed before the write with `ed.undo_push`, so
    each Ctrl+Z has a visible effect.
  - Nothing infers pixel state from document state after undo. Owners of
    derived images rebuild them from their inputs after undo and redo.
    PS-091 keys the selection mask by a digest of the ops, so the
    restored ops find or rebuild theirs, and its stencil image is
    rewritten when its recorded digest no longer matches.
  - Fetch datablocks by name or `session_uid` after undo; Python
    references may dangle.
- Cost: 75–120 ms and about 90 MB per registration on a 4K byte image
  (5.2; 40–70 ms and 96 MB on 4.2), inside Blender's `undo_memory_limit`
  preference. Edit > Undo History labels the step "Invert Channels".
- Limitation recorded by PS-096: on 4.2 in texture paint mode no
  document change is undoable, because Blender pushes empty image steps
  there; pixel writes are. 5.2 pushes memfile steps in paint mode.
- Not built: the numpy crop stack, per-image revisions and undo handlers
  of the earlier design. Pixel Art Studio uses such a stack (a serial in
  a scene property, `ed.undo_push` from a timer, replay in `undo_post`,
  and a private stack in paint modes) because it owns its brush and never
  meets Blender's image undo. v3 paints with Blender's brushes, so the
  pixels have to live in Blender's own steps.

## Acceptance

- Commit a transform, undo, redo: pixels match before and after exactly,
  with one Ctrl+Z per direction.
- Interleave a scripted write on image A, a native stroke on image B and
  a layer property change; undo steps back through all three in order
  on 5.2 (object and texture paint mode) and on 4.2 in object mode. On
  4.2 in texture paint mode the two pixel steps undo and the property
  change is documented as not undoable.
- Undo a native stroke made after a scripted write: the scripted write
  stays.
- A write-once image created, undone past and redone keeps its pixels.
- The registration adds no more than 250 ms on a 4K byte image.
