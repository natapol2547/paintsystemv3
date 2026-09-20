import bpy
from bpy.types import Node
from bpy.props import EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.utils import register_classes_factory

from .base_layer_node import PaintSystemLayerNode, emit_image_texture
from ..base_node import mark_tree_dirty
from ...common import blender_icon
from ...compiler.library import filter_mix_group
from ...filters.derived import build_stamp, is_built, stamped_uv_map
from ...filters.layer_specs import LAYER_FILTERS, layer_filter_items, layer_filter_params
from ...ops.node_tree_ops import RESOLUTION_ITEMS


class PaintSystemFilterLayerNode(PaintSystemLayerNode, Node):
    """A filter over the layers below, kept as a layer rather than applied.

    The layer owns a derived image holding the filtered result. It is not
    a cache of the stack below: the compiler substitutes it *for* that
    stack through the Filter Mix group, so the layer's own Opacity fades
    between the two without rebuilding anything.
    """
    bl_idname = 'PaintSystemFilterLayerNode'
    bl_label = 'Filter'
    bl_icon = blender_icon('SHADERFX')
    header_color = (0.25, 0.33, 0.45)

    ps_type = 'FILTER'
    ps_label = "Filter"
    ps_description = "Filter the layers below it, and stay editable"
    # Placeholder icon.
    ps_icon = ('SHADERFX',)
    ps_menu_section = 'EFFECT'
    ps_add_options = ('filter_type', 'resolution')

    # Opacity is the filter's Amount. A filter replaces what is below it,
    # so there is nothing for a blend mode to mean.
    ps_shows_blend_mode = False
    ps_opacity_label = "Amount"

    # What the filter is asked for, what it was built at, and what it
    # produced are all invisible to the compiler's fingerprints: none of
    # them changes the compiled artifact until a build has run, and a
    # build announces itself through ``hash_parts`` instead. Without this,
    # dragging a blur slider would invalidate every node cache and channel
    # bake above the layer before a single pixel had changed.
    ps_unhashed_props = (
        'filter_type', 'blur_sigma', 'resolution', 'uv_map', 'derived_image',
    )

    filter_type: EnumProperty(
        name="Filter", items=layer_filter_items(), update=mark_tree_dirty,
        description="What this layer does to the layers below it")
    blur_sigma: FloatProperty(
        name="Sigma", default=4.0, min=0.0, soft_max=64.0, subtype='PIXEL',
        update=mark_tree_dirty,
        description="Blur radius in pixels of the built image")

    resolution: EnumProperty(
        name="Resolution", items=RESOLUTION_ITEMS, default='2048',
        update=mark_tree_dirty, description="Size of the image this filter builds")
    uv_map: StringProperty(
        name="UV Map", update=mark_tree_dirty,
        description="UV map the filter is built in (empty: active render UV map)")

    # Derived. The pixels describe themselves through the stamps on the
    # image, so this pointer is all the node has to keep.
    derived_image: PointerProperty(
        type=bpy.types.Image, name="Result", update=mark_tree_dirty)

    def copy(self, node):
        super().copy(node)
        # The derived image is this layer's content and not a re-derivable
        # artifact, so the duplicate gets its own rather than sharing the
        # original's pixels with it. Unlike a cache it is packed, so the
        # copy arrives with the pixels and keeps rendering straight away.
        if self.derived_image is not None:
            self.derived_image = self.derived_image.copy()

    @classmethod
    def create(cls, tree, target=None, filter_type='BLUR', resolution='2048', **options):
        node = super().create(tree, target=target)
        node.filter_type = filter_type
        node.resolution = resolution
        return node

    # -- ui ---------------------------------------------------------------------

    def draw_label(self):
        super().draw_label()
        spec = LAYER_FILTERS.get(self.filter_type)
        return spec.label if spec is not None else self.bl_label

    def draw_source_settings(self, context, layout):
        layout.prop(self, "filter_type", text="")
        for name in layer_filter_params(self.filter_type):
            layout.prop(self, name)
        layout.prop(self, "resolution")
        obj = getattr(context, 'object', None)
        if obj is not None and obj.type == 'MESH':
            layout.prop_search(self, "uv_map", obj.data, "uv_layers", text="UV")
        else:
            layout.prop(self, "uv_map")

    # -- compiler -----------------------------------------------------------------

    @property
    def amount(self) -> float:
        """Opacity, but 0 until the filter has pixels.

        An unbuilt filter layer is an exact pass-through in every
        arrangement, including as a clip base, so adding one changes
        nothing on screen until it is built, and losing its image gives
        the original back rather than a black band.
        """
        if not self.enabled or not is_built(self.derived_image):
            return 0.0
        return self.opacity

    def hash_parts(self, ctx):
        # The derived image hashes by name like any other datablock, so the
        # stamp of its pixels is what a cache above this layer has to see.
        return [build_stamp(self.derived_image)]

    def emit_source(self, ctx):
        image = self.derived_image
        if not is_built(image):
            return (0.0, 0.0, 0.0, 1.0), 0.0
        # The UV map stamped on the image, not the authored one: changing
        # the setting relocates nothing until the rebuild runs.
        return emit_image_texture(ctx, self, 'result', image, stamped_uv_map(image))

    def emit_blend(self, ctx, color, alpha, *, clip=False):
        fmix = ctx.emit_node(self, 'fmix', 'ShaderNodeGroup', properties={
            'node_tree': filter_mix_group(),
        })
        ctx.connect_input(self.inputs['Color'], fmix, 'Prev Color')
        ctx.connect_input(self.inputs['Alpha'], fmix, 'Prev Alpha')
        ctx.connect_input(self.inputs['Mask'], fmix, 'Mask')
        ctx.link_or_set(color, fmix, 'Color')
        ctx.link_or_set(alpha, fmix, 'Alpha')
        ctx.ir.set_input(fmix, 'Amount', default_value=self.amount)
        # Always set: the artifact reuses nodes by id and keeps the values
        # of inputs the IR leaves out.
        ctx.ir.set_input(fmix, 'Clip', default_value=1.0 if clip else 0.0)
        return (fmix, 'Color'), (fmix, 'Alpha')


classes = (
    PaintSystemFilterLayerNode,
)


register, unregister = register_classes_factory(classes)
