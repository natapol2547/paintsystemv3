import bpy
from bpy.props import BoolProperty, FloatProperty, EnumProperty, PointerProperty, StringProperty

from ..base_node import PaintSystemBaseNode, mark_tree_dirty
from ...common import icon_kwargs
from ...compiler.library import layer_blend_group
from ...context import update_active_image
from ...nodetree.stack_ops import clip_base, feeds_clip_run


def update_painting(self, context):
    """``update=`` callback for settings that change where painting on a layer goes."""
    update_active_image(context)


def update_tree_and_painting(self, context):
    mark_tree_dirty(self, context)
    update_active_image(context)


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

    Types offered in the Add Layer menu fill in the ``ps_*`` attributes and
    are listed in ``nodes/layers/registry.py``.
    """
    bl_width_default = 200
    is_layer_node = True

    # v2 layer type identifier, which migration maps v2 layers through.
    ps_type = ''
    ps_label = ''
    ps_description = ''
    # Icon names for ``common.icon_kwargs``, newest Blender name first.
    ps_icon: tuple[str, ...] = ('BLANK1',)
    # Menu entries of different sections are separated.
    ps_menu_section = ''
    # ``paint_system.add_layer`` properties ``create`` reads; adding a type
    # that has any asks for them in a dialog first.
    ps_add_options: tuple[str, ...] = ()
    # False for a type whose ``emit_blend`` does not composite, so the
    # setting would be a control that does nothing.
    ps_shows_blend_mode = True
    # Label on ``opacity``: a type that replaces what is below it fades
    # between the two rather than making itself see-through.
    ps_opacity_label = "Opacity"

    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0,
                           subtype='FACTOR', update=mark_tree_dirty)
    blend_mode: EnumProperty(name="Blend Mode", items=BLEND_MODE_ITEMS,
                             default='MIX', update=mark_tree_dirty)
    enabled: BoolProperty(name="Enabled", default=True, update=mark_tree_dirty)
    is_clip: BoolProperty(
        name="Clip", default=False, update=mark_tree_dirty,
        description="Show this layer only where the layer below it is visible")

    # Editing state only; the compiler ignores both (core._HASH_EXCLUDED_PROPS).
    lock_layer: BoolProperty(name="Lock Layer", default=False, update=update_painting,
                             description="Prevent changes to this layer's settings")
    lock_alpha: BoolProperty(name="Lock Alpha", default=False, update=update_painting,
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

    @property
    def paint_image(self):
        """The image texture painting on this layer draws into, or None."""
        return None

    def copy(self, node):
        super().copy(node)
        # Blender copies the pointer, so both layers would share one cache
        # image, and ``bake_node_cache`` reuses whatever ``cache_image``
        # holds: baking either copy would overwrite the other's pixels with
        # no warning. A cache is derived, so the copy arrives without one and
        # bakes its own. Authored content stays shared on purpose.
        if self.cache_image is None and not self.cache_enabled:
            return
        # ``cache_enabled``, ``cache_image`` and ``cache_uv_map`` recompile on
        # assignment, so they are only written when there is a cache to clear.
        self.cache_enabled = False
        self.cache_image = None
        self.cache_uv_map = ""
        self.cache_hash = ""
        self.cache_stale = False

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

    @classmethod
    def create(cls, tree, target=None, **options):
        """Add a layer of this type to *tree*'s active channel and return it.

        *target* places it as in ``PaintSystemNodeTree.insert_layer_node``;
        *options* carries the ``ps_add_options`` values by name.
        """
        return tree.insert_layer_node(cls.bl_idname, target=target)

    # -- ui ---------------------------------------------------------------------

    def draw_buttons(self, context, layout):
        self.draw_layer_settings(context, layout)
        self.draw_source_settings(context, layout)
        self.draw_cache_settings(context, layout)

    def draw_row_icon(self, layout):
        """The type icon at the start of this layer's row in the layer list."""
        layout.label(text="", **icon_kwargs(*self.ps_icon))

    def draw_source_settings(self, context, layout):
        """Settings of this layer's own content, shared by the node and the Layer Settings panel."""

    def draw_layer_settings(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "lock_layer", text="", **icon_kwargs('LOCKED' if self.lock_layer else 'UNLOCKED'))
        settings = row.row(align=True)
        settings.enabled = not self.lock_layer
        settings.prop(self, "is_clip", text="", **icon_kwargs('SELECT_INTERSECT'))
        settings.prop(self, "enabled", text="")
        settings.prop(self, "opacity", text=self.ps_opacity_label)
        if self.ps_shows_blend_mode:
            settings = layout.column()
            settings.enabled = not self.lock_layer
            settings.prop(self, "blend_mode", text="")

    def draw_cache_settings(self, context, layout):
        box = layout.box()
        row = box.row(align=True)
        row.prop(self, "cache_enabled", text="Cache")
        if self.cache_image is not None and self.cache_enabled:
            if self.cache_stale:
                row.label(text="Stale", **icon_kwargs('ERROR'))
            else:
                row.label(text="Baked", **icon_kwargs('CHECKMARK'))
        row.operator("paint_system.bake_cache", text="", **icon_kwargs('RENDER_STILL'))
        if self.cache_enabled:
            box.template_ID(self, "cache_image")

    # -- compiler -----------------------------------------------------------------

    def emit_source(self, ctx):
        """Return (color, alpha): IR refs or constants for this layer's own content."""
        return (0.0, 0.0, 0.0, 1.0), 0.0

    def emit(self, ctx):
        if ctx.is_cached(self):
            # Writing an RNA property tags the tree and the materials using
            # it, so only write when the value actually changes.
            if self.cache_stale:
                self.cache_stale = False
            color, alpha = emit_image_texture(ctx, self, 'cache', self.cache_image,
                                              self.cache_uv_map)
            ctx.alias_output(self, 'Color', color)
            ctx.alias_output(self, 'Alpha', alpha)
            return
        stale = bool(self.cache_enabled and self.cache_image is not None)
        if self.cache_stale != stale:
            self.cache_stale = stale

        color, alpha = self.emit_source(ctx)
        base = clip_base(self)
        if base is not None:
            color, alpha = self.emit_blend(ctx, color, alpha, clip=True)
            if not feeds_clip_run(self):
                # Top of the run: blend the base with everything clipped to it.
                color, alpha = base.emit_blend(ctx, color, alpha)
        elif not feeds_clip_run(self):
            color, alpha = self.emit_blend(ctx, color, alpha)
        # Otherwise this is a base: the clipped layers above composite onto
        # its content, and the top one blends the result with its settings.
        ctx.alias_output(self, 'Color', color)
        ctx.alias_output(self, 'Alpha', alpha)

    def emit_blend(self, ctx, color, alpha, *, clip=False):
        """Blend (color, alpha) over this layer's ``Color``/``Alpha`` inputs.

        Uses this layer's blend mode, opacity and mask, and returns the
        result's colour and alpha refs. *clip* keeps the backdrop's alpha.
        """
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
        # Always set: the artifact reuses nodes by id and keeps the values of
        # inputs the IR leaves out.
        ctx.ir.set_input(blend, 'Clip', default_value=1.0 if clip else 0.0)
        return (blend, 'Color'), (blend, 'Alpha')
