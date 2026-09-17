# PS-067 Eraser brush in the sidebar (deferred)

Epic G. Size S. Nice to have, not scheduled.

## Status

Not started. Requested on 2026-09-17.

Before building it, ask the user to create the brush. They will make
the eraser brush themselves and put it in the add-on's library `.blend`.
The repository does not hold that brush or any library `.blend` yet.

## Goal

A one-click eraser in the Paint System sidebar, for artists new to
Blender. They may not know that erasing on an image layer means setting
a brush's blend mode to Erase Alpha, so the sidebar offers an Eraser
next to the brush picker.

## v3 design

To settle when the ticket is picked up:

- The brush is a custom brush with `Brush.blend = 'ERASE_ALPHA'`,
  created by the user and stored in the library `.blend`. The add-on
  appends it from there, the way PS-002 appends library node groups,
  whether or not PS-002 has landed by then.
- The Eraser control goes in the Brush section of the 3D view main
  panel (`panels/brush_panels.py`, PS-033), next to the brush picker.
- Brushes are datablocks set on `image_paint.brush` before 4.3, and
  assets activated with `brush.asset_activate` from 4.3. The appended
  brush has to be activatable on both paths; PS-033's picker already
  handles the split.
- Open questions: a sidebar button, a toolbar tool as well, or both;
  and whether pressing it again returns to the previous brush.

## Acceptance

- In texture paint mode on an image layer, choosing Eraser and painting
  a stroke makes the painted texels transparent, on 4.2 and on 4.3 and
  newer.
- Choosing it again, or saving and reopening the file, does not add a
  second copy of the brush.
- Blender's own brushes and the user's brush settings are unchanged.
