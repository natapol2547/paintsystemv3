from bpy.types import Operator
from bpy.props import EnumProperty
from bpy.utils import register_classes_factory

from .node_tree_ops import RESOLUTION_ITEMS
from ..context import get_active_tree, parse_context, update_active_image
from ..compiler.core import suspend_compile
from ..nodes.layers.registry import layer_type, layer_type_items
from ..nodetree.stack_ops import descendants, is_layer, movement_options


class PAINTSYSTEM_OT_add_layer(Operator):
    bl_idname = "paint_system.add_layer"
    bl_label = "Add Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer_type: EnumProperty(name="Type", items=layer_type_items(), default='IMAGE')
    resolution: EnumProperty(name="Resolution", items=RESOLUTION_ITEMS, default='2048')

    @classmethod
    def description(cls, context, properties):
        return (f"{layer_type(properties.layer_type).ps_description}. Added above the active layer, "
                "inside it when it is a folder, or on top of the stack when no layer is active")

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return tree is not None and len(tree.channels) > 0

    def execute(self, context):
        ps = parse_context(context)
        node_class = layer_type(self.layer_type)
        options = {name: getattr(self, name) for name in node_class.ps_add_options}
        target = ps.layer if ps.stack_item is not None else None
        with suspend_compile(ps.tree):
            node_class.create(ps.tree, target=target, **options)
        update_active_image(context)
        return {'FINISHED'}

    def invoke(self, context, event):
        if layer_type(self.layer_type).ps_add_options:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        for name in layer_type(self.layer_type).ps_add_options:
            self.layout.prop(self, name)


class PAINTSYSTEM_OT_remove_layer(Operator):
    bl_idname = "paint_system.remove_layer"
    bl_label = "Remove Layer"
    bl_description = "Remove the active layer, and a folder's content with it, and close the gap"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return tree is not None and is_layer(tree.nodes.active)

    def execute(self, context):
        ps = parse_context(context)
        tree, node = ps.tree, ps.layer
        # The row that moves up into the removed row stays active, or the
        # last row when the removed one was at the bottom.
        rows = [item.node.name for item in tree.stack()]
        gone = {node.name, *(child.name for child in descendants(node))} if node.is_folder else {node.name}
        position = rows.index(node.name) if node.name in rows else len(rows)
        after = [name for name in rows[position:] if name not in gone]
        before = [name for name in rows[:position] if name not in gone]
        next_active = after[0] if after else (before[-1] if before else None)

        tree.remove_layer_node(node)
        if next_active is not None:
            tree.activate_layer_node(tree.nodes[next_active])
            update_active_image(context)
        return {'FINISHED'}


MOVE_ACTION_ITEMS = [
    ('AUTO', "Auto", "The only move on offer"),
    ('SKIP', "Skip", "Move past the neighbouring layer"),
    ('MOVE_INTO', "Move Into", "Move to the bottom of the empty folder above"),
    ('MOVE_INTO_TOP', "Move Into Top", "Move to the top of the folder below"),
    ('MOVE_OUT', "Move Out", "Move out of the folder, above it"),
    ('MOVE_OUT_BOTTOM', "Move Out Bottom", "Move out of the folder, below it"),
    ('MOVE_ADJACENT', "Move Adjacent", "Move to the level of the neighbouring layer"),
]


def move_label(option) -> str:
    if option.action == 'SKIP':
        return f"Skip over '{option.target.name}'"
    if option.action in {'MOVE_OUT', 'MOVE_OUT_BOTTOM'}:
        return f"Move out of '{option.folder.name}'"
    if option.folder is None:
        return "Move to top level"
    return f"Move into '{option.folder.name}'"


class LayerMoveOperator:
    """Move the active layer a row, asking which way when several moves are on offer."""
    bl_options = {'REGISTER', 'UNDO'}
    direction = ''

    action: EnumProperty(name="Action", items=MOVE_ACTION_ITEMS, default='AUTO',
                         options={'SKIP_SAVE'})

    @classmethod
    def move_options(cls, context):
        ps = parse_context(context)
        if ps.stack_item is None:
            return ps, []
        return ps, movement_options(ps.tree.stack(), ps.layer, cls.direction)

    @classmethod
    def poll(cls, context):
        return bool(cls.move_options(context)[1])

    def execute(self, context):
        ps, options = self.move_options(context)
        action = self.action
        if action == 'AUTO':
            if len(options) != 1:
                self.report({'WARNING'}, "Several moves are possible; choose one")
                return {'CANCELLED'}
            action = options[0].action
        if not ps.tree.move_layer_node(ps.layer, self.direction, action):
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        _ps, options = self.move_options(context)
        if self.action != 'AUTO' or len(options) == 1:
            return self.execute(context)
        if not options:
            return {'CANCELLED'}
        bl_idname = self.bl_idname

        def draw(menu, _context):
            for option in options:
                menu.layout.operator(bl_idname, text=move_label(option)).action = option.action

        context.window_manager.popup_menu(draw, title="Move Layer")
        return {'INTERFACE'}


class PAINTSYSTEM_OT_move_layer_up(LayerMoveOperator, Operator):
    bl_idname = "paint_system.move_layer_up"
    bl_label = "Move Layer Up"
    bl_description = "Move the active layer up a row, into or out of folders on the way"
    direction = 'UP'


class PAINTSYSTEM_OT_move_layer_down(LayerMoveOperator, Operator):
    bl_idname = "paint_system.move_layer_down"
    bl_label = "Move Layer Down"
    bl_description = "Move the active layer down a row, into or out of folders on the way"
    direction = 'DOWN'


classes = (
    PAINTSYSTEM_OT_add_layer,
    PAINTSYSTEM_OT_remove_layer,
    PAINTSYSTEM_OT_move_layer_up,
    PAINTSYSTEM_OT_move_layer_down,
)


register, unregister = register_classes_factory(classes)
