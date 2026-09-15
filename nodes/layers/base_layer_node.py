import bpy
from bpy.props import BoolProperty, FloatProperty, EnumProperty, PointerProperty, StringProperty

from ..base_node import PaintSystemBaseNode, mark_tree_dirty
from ...compiler.library import layer_blend_group


BLEND_MODE_ITEMS = []
for blend_mode in bpy.types.ShaderNodeMix.bl_rna.properties['blend_type'].enum_items:
    BLEND_MODE_ITEMS.append(
        (blend_mode.identifier, blend_mode.name, blend_mode.description))
    if blend_mode.identifier in ["MIX", "COLOR_BURN", "ADD", "LINEAR_LIGHT", "DIVIDE"]:
        BLEND_MODE_ITEMS.append(None)


def emit_image_texture(ctx, node, role: str, image, uv_map: str = ""):
    """Emit an Image Texture (plus UV Map when set). Returns (color_ref, alpha_ref)."""
    tid = ctx.emit_node(node, role, 'ShaderNodeTexImage', properties={
        'image': image,
        'interpolation': 'Linear',
        'extension': 'REPEAT',
    })
    if uv_map:
        uid = ctx.emit_node(node, f"{role}:uv", 'ShaderNodeUVMap',
                            properties={'uv_map': uv_map})
        ctx.link((uid, 'UV'), tid, 'Vector')
    return (tid, 'Color'), (tid, 'Alpha')


class PaintSystemLayerNode(PaintSystemBaseNode):
    """A layer: takes the previous stack (Color/Alpha), produces a new stack.

    Subclasses implement ``emit_source(ctx)`` returning ``(color, alpha)``
    where each is an IR ref or a constant. Blending is shared.
    """
    bl_width_default = 200
    is_layer_node = True

    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0,
                           subtype='FACTOR', update=mark_tree_dirty)
    blend_mode: EnumProperty(name="Blend Mode", items=BLEND_MODE_ITEMS,
                             default='MIX', update=mark_tree_dirty)
    enabled: BoolProperty(name="Enabled", default=True, update=mark_tree_dirty)

    # Editing state only; the compiler ignores both (core._HASH_EXCLUDED_PROPS).
    lock_layer: BoolProperty(name="Lock Layer", default=False,
                             description="Prevent changes to this layer's settings")
    lock_alpha: BoolProperty(name="Lock Alpha", default=False,
                             description="Paint without changing this layer's transparency")

    # Cache (hybrid bake). When valid, the compiler replaces this node and its
    # whole upstream with a single image texture.
    cache_enabled: BoolProperty(
        name="Use Cache", default=False, update=mark_tree_dirty,
        description="Replace this layer and everything below it with its baked image while the bake is up to date")
    cache_image: PointerProperty(type=bpy.types.Image, name="Cache Image",
                                 update=mark_tree_dirty)
    cache_hash: StringProperty(name="Cache Fingerprint")
    cache_uv_map: StringProperty(name="Cache UV Map", update=mark_tree_dirty)
    cache_stale: BoolProperty(name="Cache Stale", default=False,
                              options={'SKIP_SAVE'})

    def init(self, context):
        super().init(context)
        color_in = self.inputs.new('NodeSocketColor', "Color")
        color_in.default_value = (0, 0, 0, 0)
        color_in.hide_value = True
        alpha_in = self.inputs.new('NodeSocketFloat', "Alpha")
        alpha_in.default_value = 0.0
        alpha_in.hide_value = True
        mask_in = self.inputs.new('NodeSocketFloat', "Mask")
        mask_in.default_value = 1.0
        mask_in.hide_value = True
        self.outputs.new('NodeSocketColor', "Color")
        self.outputs.new('NodeSocketFloat', "Alpha")

    # -- ui ---------------------------------------------------------------------

    def draw_layer_settings(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "lock_layer", text="", icon='LOCKED' if self.lock_layer else 'UNLOCKED')
        settings = row.row(align=True)
        settings.enabled = not self.lock_layer
        settings.prop(self, "enabled", text="")
        settings.prop(self, "opacity")
        settings = layout.column()
        settings.enabled = not self.lock_layer
        settings.prop(self, "blend_mode", text="")

    def draw_cache_settings(self, context, layout):
        box = layout.box()
        row = box.row(align=True)
        row.prop(self, "cache_enabled", text="Cache")
        if self.cache_image is not None and self.cache_enabled:
            if self.cache_stale:
                row.label(text="Stale", icon='ERROR')
            else:
                row.label(text="Baked", icon='CHECKMARK')
        row.operator("paint_system.bake_cache", text="", icon='RENDER_STILL')
        if self.cache_enabled:
            box.template_ID(self, "cache_image")

    # -- compiler -----------------------------------------------------------------

    def emit_source(self, ctx):
        """Return (color, alpha): IR refs or constants for this layer's own content."""
        return (0.0, 0.0, 0.0, 1.0), 0.0

    def emit(self, ctx):
        if ctx.is_cached(self):
            self.cache_stale = False
            color, alpha = emit_image_texture(ctx, self, 'cache', self.cache_image,
                                              self.cache_uv_map)
            ctx.alias_output(self, 'Color', color)
            ctx.alias_output(self, 'Alpha', alpha)
            return
        self.cache_stale = bool(self.cache_enabled and self.cache_image is not None)

        color, alpha = self.emit_source(ctx)
        self.emit_blend(ctx, color, alpha)

    def emit_blend(self, ctx, color, alpha):
        blend = ctx.emit_node(self, 'blend', 'ShaderNodeGroup', properties={
            'node_tree': layer_blend_group(self.blend_mode),
        })
        ctx.connect_input(self.inputs['Color'], blend, 'Prev Color')
        ctx.connect_input(self.inputs['Alpha'], blend, 'Prev Alpha')
        ctx.connect_input(self.inputs['Mask'], blend, 'Mask')
        ctx.link_or_set(color, blend, 'Color')
        ctx.link_or_set(alpha, blend, 'Alpha')
        ctx.ir.set_input(blend, 'Opacity',
                         default_value=self.opacity if self.enabled else 0.0)
        ctx.set_output(self, 'Color', blend, 'Color')
        ctx.set_output(self, 'Alpha', blend, 'Alpha')
