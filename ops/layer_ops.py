import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.utils import register_classes_factory

from ..common import icon_kwargs
from ..context import button_layer, get_active_tree, parse_context, update_active_image
from ..compiler.bake import bake_node_cache
from ..compiler.core import suspend_compile
from ..filters.layer_specs import layer_filter_items
from ..nodes.layers.base_layer_node import RESOLUTION_ITEMS
from ..nodes.layers.registry import layer_type, layer_type_items
from ..nodetree.stack_ops import (Position, arrange_stack, channel_of, movement_options, pair_count,
                                  remove_pair, removal)
from ..undo import undo_restores_data


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
            node_class.create(ps.tree, target=target, ps_object=ps.ps_object, **options)
        update_active_image(context)
        return {'FINISHED'}

    def invoke(self, context, event):
        if layer_type(self.layer_type).ps_add_options:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        for name in layer_type(self.layer_type).ps_add_options:
            self.layout.prop(self, name)


def _filter_results(positions: list[Position]) -> list:
    """The filter result images of the pairs at *positions*, which are about to go.

    A filter result counts as the pair's content, not as an artifact
    that can be rebuilt, so removing the pair removes it too. Cache
    images are left out on purpose, because they can always be baked
    again.
    """
    return [layer.pairs[pair].derived_image for layer, pair in positions
            if getattr(layer, 'ps_type', "") == 'FILTER'
            and layer.pairs[pair].derived_image is not None]


class PAINTSYSTEM_OT_remove_layer(Operator):
    bl_idname = "paint_system.remove_layer"
    bl_label = "Remove Layer"
    bl_description = ("Remove the active layer from this channel, and a folder's content with it, "
                      "and close the gap. A layer linked into other channels stays there")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ps = parse_context(context)
        if ps.layer is None:
            return False
        if ps.stack_item is None:
            # Which pair to take out follows from the channel's stack.
            cls.poll_message_set("The active layer is not in this channel")
            return False
        return True

    def invoke(self, context, event):
        # The dialog draws on every redraw, so work out what it shows once
        # here.
        ps = parse_context(context)
        node = ps.layer
        plan = removal(node, ps.stack_item.pair)
        self.layer_name = node.name
        self.content_count = len(plan.deleted - {node.name})
        self.stays = node.name not in plan.deleted
        taken = {position.pair for position in plan.positions if position.node == node}
        channels = (channel_of(ps.tree, node, pair) for pair in range(pair_count(node))
                    if pair not in taken)
        self.other_stacks = len({channel.name for channel in channels
                                 if channel is not None and channel != ps.channel})
        self.filter_count = len(_filter_results(plan.positions))
        self.undo_restores = undo_restores_data(context)
        return context.window_manager.invoke_props_dialog(self, confirm_text="Remove")

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Remove '{self.layer_name}'?", **icon_kwargs('ERROR'))
        if self.other_stacks == 1:
            layout.label(text="It stays in the other stack it is linked into.")
        elif self.other_stacks > 1:
            layout.label(text=f"It stays in the {self.other_stacks} other stacks it is linked into.")
        elif self.stays:
            layout.label(text="It stays in the node tree, where its other pairs are still linked.")
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
        tree, node, pair = ps.tree, ps.layer, ps.stack_item.pair
        # The row that moves up into the removed row's place becomes
        # active. That is the first row after the layer's content. If the
        # removed row was at the bottom, the new last row becomes active.
        items = tree.stack()
        position = next((index for index, item in enumerate(items) if item.node == node), len(items))
        end = position + 1
        while end < len(items) and items[end].level > items[position].level:
            end += 1
        rows = [item.node.name for item in items[:position] + items[end:]]
        next_active = rows[position] if position < len(rows) else (rows[-1] if rows else None)

        # Remove the filter results before the pairs. Once a pair is
        # gone, nothing points at its image, and only the next file read
        # would delete it. This is done here and not in ``Node.free``,
        # because ``free`` also runs when undo tears nodes down. This
        # operator has the UNDO option, so its undo step holds the node
        # and the packed image together, and one Ctrl+Z brings both back.
        for image in _filter_results(removal(node, pair).positions):
            bpy.data.images.remove(image)

        tree.remove_layer_node(node)
        if next_active is not None:
            tree.activate_layer_node(tree.nodes[next_active])
            update_active_image(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_add_pair(Operator):
    bl_idname = "paint_system.add_pair"
    bl_label = "Add Pair"
    bl_description = ("Give the layer another pair of sockets. Link its output into another stack "
                      "to show the layer there too")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return button_layer(context, get_active_tree(context)) is not None

    def execute(self, context):
        node = button_layer(context, get_active_tree(context))
        node.add_pair()
        node.active_pair_index = pair_count(node) - 1
        return {'FINISHED'}


class PAINTSYSTEM_OT_remove_pair(Operator):
    bl_idname = "paint_system.remove_pair"
    bl_label = "Remove Pair"
    bl_description = ("Take the layer out of the selected pair's stack, close the gap, and remove "
                      "the pair. A filter layer's image for that stack goes with it")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        node = button_layer(context, get_active_tree(context))
        if node is None:
            return False
        if pair_count(node) == 1:
            cls.poll_message_set("A layer keeps at least one pair. Remove the layer instead")
            return False
        return True

    def execute(self, context):
        tree = get_active_tree(context)
        node = button_layer(context, tree)
        pair = min(node.active_pair_index, pair_count(node) - 1)
        channel = channel_of(tree, node, pair)
        # As in Remove Layer, before the pair is gone (``_filter_results``).
        for image in _filter_results([Position(node, pair)]):
            bpy.data.images.remove(image)
        with suspend_compile(tree):
            remove_pair(tree, node, pair)
            if channel is not None:
                arrange_stack(tree, channel.name)
        node.active_pair_index = min(pair, pair_count(node) - 1)
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


# Why ``move_layer_node`` left the stack as it was, by the reason it returns.
MOVE_REFUSALS = {
    'NOT_OFFERED': "This move is not possible here",
    'LOOP': "This move would make a loop through a mask link",
    'REPEAT': "This move would put the layer in one channel twice",
}


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
        if action not in {option.action for option in options}:
            return {'CANCELLED'}
        result = ps.tree.move_layer_node(ps.layer, self.direction, action)
        if result != 'MOVED':
            self.report({'WARNING'}, MOVE_REFUSALS[result])
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
    bl_description = ("Bake this layer's output (including everything below it) into its cache "
                      "image. A linked layer bakes its cache for the active channel")
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
                                    pair=tree.pair_in_stack(node), width=size, height=size,
                                    margin=self.margin, uv_map=self.uv_map)
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        node.cache_enabled = True
        self.report({'INFO'}, f"Baked {image.name}")
        return {'FINISHED'}

    def invoke(self, context, event):
        tree = get_active_tree(context)
        node = button_layer(context, tree)
        image = node.pairs[tree.pair_in_stack(node)].cache_image if node is not None else None
        if image is not None:
            self.resolution = str(image.size[0]) if str(image.size[0]) in {
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
    PAINTSYSTEM_OT_add_pair,
    PAINTSYSTEM_OT_remove_pair,
    PAINTSYSTEM_OT_move_layer_up,
    PAINTSYSTEM_OT_move_layer_down,
    PAINTSYSTEM_OT_bake_cache,
)


register, unregister = register_classes_factory(classes)
