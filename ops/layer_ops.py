import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.utils import register_classes_factory

from .selection_ops import undo_restores_data
from ..common import icon_kwargs
from ..context import button_layer, get_active_tree, parse_context, update_active_image
from ..compiler.bake import bake_node_cache
from ..compiler.core import suspend_compile
from ..filters.layer_specs import layer_filter_items
from ..nodes.layers.base_layer_node import RESOLUTION_ITEMS
from ..nodes.layers.registry import layer_type, layer_type_items
from ..nodetree.stack_ops import descendants, is_layer, movement_options


class PAINTSYSTEM_OT_add_layer(Operator):
    bl_idname = "paint_system.add_layer"
    bl_label = "Add Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer_type: EnumProperty(name="Type", items=layer_type_items(), default='IMAGE')
    resolution: EnumProperty(name="Resolution", items=RESOLUTION_ITEMS, default='2048')
    filter_type: EnumProperty(name="Filter", items=layer_filter_items(), default='INVERT')

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


def _filter_results(node) -> list:
    """The derived images *node* and its content own, if any (PS-057).

    A filter result is the layer's content rather than a re-derivable
    artifact, so removing the layer removes it. A cache image is not in
    here on purpose: it can always be baked again.
    """
    nodes = [node, *descendants(node)] if node.is_folder else [node]
    return [layer.derived_image for layer in nodes
            if getattr(layer, 'ps_type', "") == 'FILTER' and layer.derived_image is not None]


class PAINTSYSTEM_OT_remove_layer(Operator):
    bl_idname = "paint_system.remove_layer"
    bl_label = "Remove Layer"
    bl_description = "Remove the active layer, and a folder's content with it, and close the gap"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return tree is not None and is_layer(tree.nodes.active)

    def invoke(self, context, event):
        # The dialog draws on every redraw; resolve what it shows once.
        node = parse_context(context).layer
        self.layer_name = node.name
        self.content_count = len(descendants(node)) if node.is_folder else 0
        self.filter_count = len(_filter_results(node))
        self.undo_restores = undo_restores_data(context)
        return context.window_manager.invoke_props_dialog(self, confirm_text="Remove")

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Remove '{self.layer_name}'?", **icon_kwargs('ERROR'))
        if self.content_count == 1:
            layout.label(text="The layer inside it goes with it.")
        elif self.content_count > 1:
            layout.label(text=f"The {self.content_count} layers inside it go with it.")
        if self.filter_count == 1:
            layout.label(text="Its filtered image goes with it.")
        elif self.filter_count > 1:
            layout.label(text=f"The {self.filter_count} filtered images go with it.")
        if not self.undo_restores:
            layout.label(text="Undo cannot bring it back in this mode.")

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

        # Before the node goes: once its pointer is gone the image is an
        # orphan nothing can name, and only the next file read would
        # sweep it. Here rather than in ``Node.free`` because ``free``
        # also runs on undo-driven teardown, while this operator carries
        # UNDO -- so the step records the node and the packed image
        # together and one Ctrl+Z brings back both (PS-057).
        for image in _filter_results(node):
            bpy.data.images.remove(image)

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


class PAINTSYSTEM_OT_bake_cache(Operator):
    bl_idname = "paint_system.bake_cache"
    bl_label = "Bake Layer Cache"
    bl_description = "Bake this layer's output (including everything below it) into its cache image"
    bl_options = {'REGISTER', 'UNDO'}

    resolution: EnumProperty(name="Resolution", items=RESOLUTION_ITEMS, default='2048')
    margin: IntProperty(name="Margin", default=8, min=0, max=64)
    uv_map: StringProperty(name="UV Map")

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return button_layer(context, tree) is not None and context.object is not None

    def execute(self, context):
        tree = get_active_tree(context)
        node = button_layer(context, tree)
        size = int(self.resolution)
        try:
            image = bake_node_cache(context, tree, node, context.object,
                                    width=size, height=size, margin=self.margin,
                                    uv_map=self.uv_map)
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        node.cache_enabled = True
        self.report({'INFO'}, f"Baked {image.name}")
        return {'FINISHED'}

    def invoke(self, context, event):
        tree = get_active_tree(context)
        node = button_layer(context, tree)
        if node is not None and node.cache_image is not None:
            self.resolution = str(node.cache_image.size[0]) if str(node.cache_image.size[0]) in {
                i[0] for i in RESOLUTION_ITEMS} else self.resolution
            self.uv_map = node.cache_uv_map
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "resolution")
        layout.prop(self, "margin")
        obj = context.object
        if obj is not None and obj.type == 'MESH':
            layout.prop_search(self, "uv_map", obj.data, "uv_layers")


classes = (
    PAINTSYSTEM_OT_add_layer,
    PAINTSYSTEM_OT_remove_layer,
    PAINTSYSTEM_OT_move_layer_up,
    PAINTSYSTEM_OT_move_layer_down,
    PAINTSYSTEM_OT_bake_cache,
)


register, unregister = register_classes_factory(classes)
