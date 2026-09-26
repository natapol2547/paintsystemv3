# PS-017 Clipboard: copy, copy all, paste, paste linked, unlink

Epic B. Size M. Milestone M2.

## v2 behaviour

`copy_layer` / `copy_all_layers` (`layers_operators.py:820, 843`) store
`(material, uid)` pairs in `scene.ps_scene_data.clipboard_layers`.
`paste_layer` (`:867`) resolves each source, creates a same-type layer and
`copy_layer_data`, or a linked layer when `linked=True`; first entry at
CURSOR, rest AFTER; parents remapped through `new_layer_id_map`, roots
placed under the active folder. Menu entries in `MAT_MT_LayerMenu`
(`layers_panels.py:724-785`).

A plain paste gives the copy its own image: `copy_layer_data` calls
`duplicate_layer_data` (`paintsystem/data.py:1492-1500`), which saves the
source image and assigns `image.copy()`. Only a linked paste shares it.

## v3 design

Built in a8749b5, with linked layers (PS-016). `nodes/layers/clipboard.py`
describes it in the code, and `ops/clipboard_ops.py` has the operators.

- The clipboard is module state, a list of (tree uuid, layer uuid).
  Uuids survive renames and undo, which pointers do not.
  `compiler.core.normalize_all_trees` keeps tree uuids unique, and
  `normalize_tree` keeps node uuids unique in their tree. It is not saved
  with the file.
- Copy Layer records the active layer. Copy All Layers records the
  top-level layers of the active channel; folder content comes with its
  folder.
- A paste copies the layers as they are at paste time. A layer deleted
  since the copy is passed over, and Paste is greyed out when none is
  left. A layer moved into a copied folder since is pasted once, in the
  folder.
- A paste goes where Add Layer puts a new layer: above the active layer,
  into it at the top when it is a folder, or on top of the stack. The
  pasted layers keep their order and folder nesting, and the first
  becomes active. Every copy is made before any is placed, so a folder
  pasted into itself holds its content as it was.
- Masks among the copied layers are linked again. A mask from a layer
  that was not copied is left out, and so is one that would make a loop:
  the layers are placed in the order they were copied in, so a mask
  linked after a reorder may come from a layer that now sits above the
  one it masks.
- Paste works into any Paint System tree. Each pasted layer gets a new
  uuid and its own copy of content such as images
  (`compiler.bake.duplicate_image`, which keeps unsaved painting).
  Layers copied from one linked group are pasted as a new group, linked
  only with each other and sharing one new image.
- Paste Linked links each new layer with the layer it copies, which
  shares the image. Links stay in one tree (PS-016), so it is a separate
  operator whose poll greys it out in any other tree.
- The layer list's side column has v2's Layer menu (DOWNARROW_HLT):
  Unlink Layer when the active layer is linked, then Copy Layer, Copy All
  Layers, Paste Layer(s) and Paste Linked Layer(s).
- Unlink is PS-016.

## Acceptance

- A folder with nested content and a mask, pasted in another material,
  keeps its order and levels, with new uuids and the mask among the
  copies relinked (`tests/test_links.py`).
- A plain paste copies images with their painted pixels; Paste Linked
  shares them and stays in sync (`tests/test_links.py`).
- Paste Linked is greyed out in another tree; Paste passes over deleted
  layers (`tests/test_links.py`).
