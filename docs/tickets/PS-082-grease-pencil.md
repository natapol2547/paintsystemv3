# PS-082 Grease Pencil support (deferred)

Epic I. Size L. Milestone M4 or later.

## v2 behaviour

The Layers panel has a GREASEPENCIL branch (`layers_panels.py:536-600`):
`template_grease_pencil_layer_tree`, add/group/remove/move sidebar,
"Layer Settings" with masks, lock, blend mode, opacity, lights, onion
skinning. `get_ps_object` accepts GREASEPENCIL; `toggle_paint_mode` enters
PAINT_GREASE_PENCIL; brush colour panels have a GP variant with colour
mode, material popover and swatch flip.

## v3 design

None of this touches the compiler. Port the panels once the mesh
workflow is stable. Until then, `PSContext.ps_object` returns None for
grease pencil objects so panels stay hidden.

## Acceptance

- Deferred. Reopen when M3 is complete.
