# PS-042 Delete group (dissolve template nodes) and move group

Epic E. Size S. Milestone M1.

## v2 behaviour

`delete_group` (`operators/group_operators.py:405-460`): BASIC and
PAINT_OVER remove the whole basic setup (group, Shader to RGB, Mix Shader,
Transparent, Material Output; `find_basic_setup_nodes`, `:384`);
PBR/NORMAL/NONE remove only the group node. `dissolve_nodes` reconnects
pass-through links. Then removes the group from the list. Confirmed
through `MAT_MT_DeleteGroupMenu` (alert). `move_group` (`:466`) reorders.

## v3 design

- `Template.dissolve(material, group_node)` per PS-041; nodes created by
  the template are tagged with `ps_template_uuid = tree.uuid` at creation
  so dissolve finds them without pattern matching.
- `paint_system.delete_group`: dissolve, remove the slot (PS-040), then
  remove the tree datablock and its artifact if no other material uses
  them (`tree.users` check), otherwise leave the datablock.
- `paint_system.move_group` up/down over `trees`.

## Acceptance

- Delete after each template restores a material whose Output is fed by
  the original shader (headless assertion), no orphan nodes or
  datablocks.
