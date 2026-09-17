# PS-083 A recreated datablock leaves a stale artifact (deferred)

Epic I. Size S. Milestone M3 or later. Found while profiling PS-081 on
2026-09-18; older than that work.

## What happens

A layer points at an image. The user deletes that image and then creates
or loads another one, which Blender gives the freed name. The layer is
pointed at the new image and compiles, but the artifact's Image Texture
keeps `image = None`: the layer renders as nothing, and no amount of
re-setting the image repairs it. Editing anything else in the tree does,
because that moves the fingerprint.

## Why

Deleting a datablock runs no Paint System code. Blender clears the
pointer on the layer node and on the artifact's Image Texture, and the
fingerprint stored on the artifact still describes the state that had the
image, so at that moment the artifact is already stale and claims to be
current.

Recreating the image makes it permanent. `IR._serialize` identifies a
datablock as `["id", <type>, name_full]` (`compiler/ir.py`), so the new
image serialises exactly like the deleted one. `compile_tree` finds the
stored fingerprint equal to the fresh one, skips `IR.apply` and returns.
Nothing revalidates the artifact's ID pointers after a fingerprint match.

## Reproduce

Confirmed on Blender 5.2.1 LTS with a script; the panel path is the same
sequence.

1. Set up a tree with an image layer pointing at `Probe Image`, compile.
   The artifact's Image Texture holds `Probe Image`.
2. `bpy.data.images.remove(image)`. The layer's image and the artifact's
   are `None`, and `artifact_fingerprint(tree)` has not changed.
3. Create a new image, which is named `Probe Image` again, and set it on
   the layer. The property update compiles.
4. The artifact's Image Texture is still `None` and
   `compiler.core.last_build_stats` is `None`: nothing was applied.
5. Add any layer. The artifact's Image Texture holds the new image again.

## Fix sketch

Options, cheapest first:

- Revalidate the artifact's ID pointers after a fingerprint match: the IR
  knows every datablock value it declared, so a match can walk just those
  and compare them by identity against the artifact, applying when one
  differs. That is a short walk over the IR's ID values, not a build.
- Or key a datablock in the payload by something the recreation cannot
  reproduce, such as `session_uid`. That makes every fingerprint
  session-local, which breaks the saved `cache_hash` values in
  `compiler/bake.py`, so it is the worse trade.
- Either way, a `bpy.app.handlers.undo_post`-style repair does not cover
  this: the trigger is a datablock deletion, which reports nothing.

Related: PS-084, the same fingerprint-by-name assumption seen from the
rename side.

## Acceptance

- Deleting an image and recreating it under the same name leaves the
  artifact holding the new image after the next compile.
- A test in `tests/test_parity.py` covers the sequence above.
- Not scheduled. Reopen when it is worth the extra walk per compile.
