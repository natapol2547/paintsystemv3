# PS-040 Multiple Paint System groups per material

Epic E. Size M. Milestone M1.

## v2 behaviour

`Material.ps_mat_data.groups` (`data.py:2931`) with `active_index`; each
group owns a node tree, a template kind, channels and coord defaults.
The header preset shows a groups popover when more than one exists
(`main_panels.py:124-137`); `move_group` reorders (`group_operators.py:466`).

## v3 design

- `PaintSystemMaterialSettings` (`context.py`) replaces the single `tree`
  pointer with `trees: CollectionProperty(TreeSlot)` where `TreeSlot` has
  `tree: PointerProperty(PaintSystemNodeTree)`, and `active_index`.
  `material_settings.tree` remains as a read-only property returning the
  active slot's tree so existing code keeps working.
- `PaintSystemNodeTree` gains `template: EnumProperty` (PS-041) and
  `coord_type` / `uv_map_name` defaults (PS-009), which v2 kept on the
  group.
- `get_active_tree` prefers the node editor tree, then the active slot.
- `link_tree_to_material` appends a slot; unlinking (PS-042) removes it.
- Slots whose tree was deleted are dropped in `normalize_all_trees`.

Why slots instead of scanning the material node tree for group nodes:
scanning cannot order the list or keep an active index, and it breaks
for the NONE template where the group may be unlinked.

## Acceptance

- Two trees on one material: the popover lists both, switching changes
  the channel and layer lists, `paint_system.new_group` appends.
- Deleting a tree datablock from the outliner leaves no dangling slot
  after the next normalize.
