# PS-090 Pixel undo for scripted image edits

Epic J. Size M. Milestone M3b.

## Problem

Blender's undo records image pixels only for strokes made in a paint
mode. Pixels written from Python through `foreach_set` are not in any
undo step, so Ctrl+Z after a scripted edit restores nothing.

The Epic J design keeps this ticket small. Selections, floating
transforms and their parameters are document data, which Blender's
memfile undo already covers. Only writes that change a layer's pixels
need this stack: a transform commit, a fill, a filter (PS-050), a
merge. Images written once and never changed (a wand result, a paste
source) need no entry either: undo restores the pointer to them.

## v3 design

- `undo/pixels.py`:
  - `PixelEntry(image_uid, tile, rect, before, after, label)`: numpy
    crops of the changed rectangle, `uint8` for byte images and
    `float32` for float images.
  - `record(image, tile, rect)` returns a context manager: it copies
    `before` on entry, `after` on exit, stores the entry and bumps the
    image's revision. Tools wrap their write in it, inside an operator
    with the `UNDO` option, so the operator's own undo step carries the
    new revision. No timer and no extra `ed.undo_push`.
- Revisions live on the image: an ID property `ps_pixel_rev` on the
  `Image`, restored by memfile undo with the rest of the ID. The module
  keeps the revision each image's buffer currently shows, keyed by
  `Image.session_uid`.
- `undo_post` and `redo_post`: for each image whose restored
  `ps_pixel_rev` differs from the buffer's revision, write `before`
  crops (undo) or `after` crops (redo) of that image's entries until
  they match, then `image.update()`. Images are handled independently,
  so an undo that crosses edits to other images or native strokes stays
  correct.
- `load_post` clears the stack. A deleted image drops its entries.
- Limits from preferences: `pixel_undo_mb` (total bytes) and
  `pixel_undo_steps`. The oldest entries go first. Undo past the oldest
  entry leaves the image at the oldest recorded state and logs nothing
  more than a debug line.
- Images the stack writes are marked dirty so PS-056 saves or packs
  them.

Depends on PS-096 spike 5; the fallback is recorded there.

## Acceptance

- Commit a transform, undo, redo: pixels match `before` and `after`
  exactly.
- Interleave a scripted edit on image A, a native stroke on image B and a
  layer property change; undo steps back through all three in order.
- A 64 x 64 edit on a 4K byte image stores under 20 KB.
- Undo past the oldest retained entry does not raise.
