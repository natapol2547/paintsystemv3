# PS-090 Pixel undo stack for scripted image edits

Epic J. Size M. Milestone M3.

## Problem

Blender's undo only records image pixels for strokes made in a paint
mode. Pixels written from Python through `foreach_set` are not captured
by a memfile step, so `ed.undo_push` on its own gives a step that
restores nothing. v2 avoided the problem by writing every filter result
to a copied image. v3 edits images in place (PS-050), so every GPU
filter, fill, transform and brush-painter stroke needs its own undo.

## Reference

Pixel Art Studio (`ps_undo.py`, GPL-3.0-or-later) solves this with an
addon-owned stack that rides Blender's undo:

- Each entry stores rect-cropped before/after pixel blocks per image,
  plus optional selection before/after.
- Pushing an entry bumps an integer `undo_serial` on the scene and calls
  `ed.undo_push(message=...)` from a zero-delay timer. The serial lands
  in the memfile step.
- An `undo_post` / `redo_post` handler compares the scene serial with the
  stack cursor and replays or reverts entries until they match.
- `TileSnapshot` (`core/snapshot.py`) keeps 64x64 copy-on-write tiles so
  a brush-sized edit records only the tiles it touched.
- `MAX_ENTRIES` caps memory; oldest entries drop.

## v3 design

- `undo/pixels.py`:
  - `PixelEntry(label, blocks: list[(image_name, tile, rect, before, after)], sel_before, sel_after, resume)`.
    `before`/`after` are numpy float32 (h, w, 4) crops. `resume` is an
    opaque payload a tool may store to re-enter an in-progress edit
    (used by the transform tool, PS-094).
  - `push(entry)`: appends, truncates redo history, bumps
    `scene.paint_system.undo_serial`, schedules `ed.undo_push`.
  - `capture(image, tile, rect) -> Snapshot` and `Snapshot.diff(image)`
    helpers so tools call `snap = capture(...)`, edit, `push(snap.diff())`.
  - Handlers on `undo_post` and `redo_post` walk the stack to the scene
    serial and write blocks back with `foreach_set` + `image.update()`.
    `load_post` clears the stack.
  - Entries older than a configurable count or total byte budget are
    dropped from the front (preferences: `pixel_undo_steps`,
    `pixel_undo_mb`).
- Image identity is by name plus UDIM tile number. A renamed or deleted
  image invalidates entries that reference it.
- Images edited by the stack are marked dirty so PS-056 saves or packs
  them.
- PS-050's `apply_filter`, PS-052's fill/clear, PS-053's brush painter,
  PS-094's transform and PS-095's fill all push through this module.
  Selection changes (PS-091) push entries with pixel blocks empty.

## Acceptance

- Apply a GPU filter, undo, redo: pixels match the captured before and
  after exactly.
- Interleave a scripted edit with a native brush stroke and a layer
  property change; undo steps back through all three in order.
- A 4K image with a 64x64 edit stores under 200 KB for the entry.
- Undo past the oldest retained entry does not raise and leaves the
  image at the oldest recorded state.
