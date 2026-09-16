# PS-090 Pixel undo for scripted image edits

Epic J. Size S. Milestone M3b.

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
  - `write_pixels(image, pixels)`: `foreach_set`, `image.update()`,
    then `register(image)`. Marks the image dirty so PS-056 saves or
    packs it.
  - `register(image)`: calls `bpy.ops.image.invert` with all four
    channels off under `temp_override(edit_image=image)`. The operator
    changes no pixel and pushes an image undo step that snapshots the
    image, exactly as a stroke does. Blender undoes and redoes the step
    itself, in order with native strokes and memfile steps, on 5.2 and
    4.2, in object and texture paint mode.
  - `pack_write_once(image)`: packs a write-once image so it survives
    undo past its creation and redo.
- Rules for callers:
  - An operator that writes pixels has no `UNDO` option. With it,
    Blender pushes a memfile step on top of the image step and every
    commit costs two Ctrl+Z.
  - The operator changes no document data in the same call. A document
    change that belongs with a pixel write (PS-094's selection follow)
    is its own step, pushed before the write with `ed.undo_push`, so
    each Ctrl+Z has a visible effect.
  - Nothing infers pixel state from document state after undo. Owners of
    derived images rebuild them from their inputs in `undo_post` and
    `redo_post` (PS-091 for the selection mask).
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
- The registration adds no more than 150 ms on a 4K byte image.
