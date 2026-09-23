import bpy
from bpy.props import BoolProperty, FloatProperty, EnumProperty, PointerProperty, StringProperty

from ..base_node import PaintSystemBaseNode, mark_tree_dirty
from ...common import icon_kwargs
from ...compiler.library import layer_blend_group
from ...context import update_active_image
from ...nodetree.stack_ops import below_input, clip_base, feeds_clip_run, stack_output


def update_painting(self, context):
    """``update=`` callback for settings that change where painting goes."""
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


# Square image sizes offered for a new image layer, a filter layer's
# result and a baked cache.
RESOLUTION_ITEMS = [
    ('1024', "1024", ""),
    ('2048', "2048", ""),
    ('4096', "4096", ""),
]


# What a layer with no content emits. Its alpha is 0, so the stack below
# shows through.
EMPTY_SOURCE = ((0.0, 0.0, 0.0, 1.0), 0.0)


def new_hidden_input(node, socket_type: str, name: str, default):
    """Add an input to *node* that is only ever linked, with no value field."""
    socket = node.inputs.new(socket_type, name)
    socket.default_value = default
    socket.hide_value = True
    return socket


def draw_uv_map(context, layout, node, obj=None, prop="uv_map"):
    """Draw *node*'s UV map field, the property *prop*.

    The field searches the UV maps of the mesh *obj*, else of the active
    mesh. With neither, it is a plain text field.
    """
    if obj is None or obj.type != 'MESH':
        obj = getattr(context, 'object', None)
    if obj is not None and obj.type == 'MESH':
        layout.prop_search(node, prop, obj.data, "uv_layers", text="UV")
    else:
        layout.prop(node, prop)


def emit_image_texture(ctx, node, role: str, image, uv_map: str = ""):
    """Emit an Image Texture node, plus a UV Map node when *uv_map* is set.

    Returns (color_ref, alpha_ref).
    """
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


def emit_mix_group(ctx, node, role: str, group, amount_socket: str, amount: float,
                   color, alpha, *, clip: bool):
    """Emit the library mix *group* to put (color, alpha) over *node*'s stack.

    The group reads the stack below from *node*'s ``Color`` input, and the
    mask from its ``Mask`` input. *amount* goes to the *amount_socket*
    input. *clip* keeps the backdrop's alpha. Returns the colour and alpha
    refs of the result.
    """
    mix = ctx.emit_node(node, role, 'ShaderNodeGroup', properties={'node_tree': group})
    prev_color, prev_alpha = ctx.rgba_input(below_input(node))
    ctx.link_or_set(prev_color, mix, 'Prev Color')
    ctx.link_or_set(prev_alpha, mix, 'Prev Alpha')
    ctx.connect_input(node.inputs['Mask'], mix, 'Mask')
    ctx.link_or_set(color, mix, 'Color')
    ctx.link_or_set(alpha, mix, 'Alpha')
    ctx.ir.set_input(mix, amount_socket, default_value=amount)
    # Always set Clip, even to 0. The artifact reuses nodes by identifier
    # and keeps the old values of inputs the IR leaves out.
    ctx.ir.set_input(mix, 'Clip', default_value=1.0 if clip else 0.0)
    return (mix, 'Color'), (mix, 'Alpha')


class PaintSystemLayerNode(PaintSystemBaseNode):
    """A layer. It takes the stack below on ``Color`` and outputs a new stack.

    Each link carries the stack as one RGBA value. ``Mask`` is always the
    last input.

    Subclasses implement ``emit_source(ctx)``, which returns
    ``(color, alpha)``. Each is an IR ref or a constant. Blending is shared
    by every layer type.

    Types offered in the Add Layer menu set the ``ps_*`` attributes and are
    listed in ``nodes/layers/registry.py``.
    """
    bl_width_default = 200
    is_layer_node = True

    # Layer type identifier. It uses v2's identifiers, so migration can map
    # v2 layers through it.
    ps_type = ''
    ps_label = ''
    ps_description = ''
    # Icon names for ``common.icon_kwargs``, newest Blender name first.
    ps_icon: tuple[str, ...] = ('BLANK1',)
    # Add Layer menu section. Entries in different sections are separated.
    ps_menu_section = ''
    # Names of the ``paint_system.add_layer`` properties that ``create``
    # reads. Adding a type that has any first asks for them in a dialog.
    ps_add_options: tuple[str, ...] = ()
    # False for a type whose ``emit_blend`` does not composite. There the
    # blend mode setting would do nothing, so it is hidden.
    ps_shows_blend_mode = True
    # Label for ``opacity``. A type that replaces what is below it uses
    # opacity to fade between the two, not to make itself see-through.
    ps_opacity_label = "Opacity"

    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0,
                           subtype='FACTOR', update=mark_tree_dirty)
    blend_mode: EnumProperty(name="Blend Mode", items=BLEND_MODE_ITEMS,
                             default='MIX', update=mark_tree_dirty)
    enabled: BoolProperty(name="Enabled", default=True, update=mark_tree_dirty)
    is_clip: BoolProperty(
        name="Clip", default=False, update=mark_tree_dirty,
        description="Show this layer only where the layer below it is visible")

    # Editing state only. The compiler ignores both
    # (``core._HASH_EXCLUDED_PROPS``).
    lock_layer: BoolProperty(name="Lock Layer", default=False, update=update_painting,
                             description="Prevent changes to this layer's settings")
    lock_alpha: BoolProperty(name="Lock Alpha", default=False, update=update_painting,
                             description="Paint without changing this layer's transparency")

    # Baked cache. While it is valid, the compiler replaces this node and
    # everything upstream of it with one image texture.
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
        # image. ``bake_node_cache`` reuses whatever ``cache_image`` holds,
        # so baking either copy would silently overwrite the other's
        # pixels. A cache can be baked again, so the copy starts without
        # one and bakes its own. Authored content stays shared on purpose.
        if self.cache_image is None and not self.cache_enabled:
            return
        # Assigning ``cache_enabled``, ``cache_image`` or ``cache_uv_map``
        # triggers a recompile, so only write them when there is a cache to
        # clear.
        self.cache_enabled = False
        self.cache_image = None
        self.cache_uv_map = ""
        self.cache_hash = ""
        self.cache_stale = False

    def init(self, context):
        super().init(context)
        new_hidden_input(self, 'NodeSocketColor', "Color", (0, 0, 0, 0))
        new_hidden_input(self, 'NodeSocketFloat', "Mask", 1.0)
        self.outputs.new('NodeSocketColor', "Color")

    @classmethod
    def create(cls, tree, target=None, ps_object=None, **options):
        """Add a layer of this type to *tree*'s active channel and return it.

        *target* places it as in ``PaintSystemNodeTree.insert_layer_node``.
        *ps_object* is the mesh the user is working on, from
        ``context.get_ps_object``, or None. Most layers ignore it.
        *options* holds the ``ps_add_options`` values by name.
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

    def draw_row_state(self, layout):
        """Draw an optional badge at the end of this layer's row.

        Nothing by default. A filter layer uses it to show that its result
        no longer matches the layers below it. By design, the viewport does
        not show that.
        """

    def draw_source_settings(self, context, layout):
        """Draw the settings of this layer's own content.

        The node and the Layer Settings panel both use it.
        """

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
        """Return (color, alpha) for this layer's own content.

        Each is an IR ref or a constant.
        """
        return EMPTY_SOURCE

    def emit(self, ctx):
        if ctx.is_cached(self):
            # Writing an RNA property tags the tree and the materials that
            # use it for an update, so only write when the value changes.
            if self.cache_stale:
                self.cache_stale = False
            color, alpha = emit_image_texture(ctx, self, 'cache', self.cache_image,
                                              self.cache_uv_map)
            ctx.set_output(stack_output(self), color, alpha)
            return
        stale = bool(self.cache_enabled and self.cache_image is not None)
        if self.cache_stale != stale:
            self.cache_stale = stale

        color, alpha = self.emit_source(ctx)
        base = clip_base(self)
        if base is not None:
            color, alpha = self.emit_blend(ctx, color, alpha, clip=True)
            if not feeds_clip_run(self):
                # Top of the run. Blend the base, with everything clipped to
                # it, over the stack below the base.
                color, alpha = base.emit_blend(ctx, color, alpha)
        elif not feeds_clip_run(self):
            color, alpha = self.emit_blend(ctx, color, alpha)
        # Otherwise this is a clip base. The clipped layers above composite
        # onto its content. The top clipped layer then blends the result
        # using this base's settings.
        ctx.set_output(stack_output(self), color, alpha)

    def emit_blend(self, ctx, color, alpha, *, clip=False):
        """Blend (color, alpha) over the stack on this layer's ``Color`` input.

        Uses this layer's blend mode, opacity and mask, and returns the
        result's colour and alpha refs. *clip* keeps the backdrop's alpha.
        """
        return emit_mix_group(ctx, self, 'blend', layer_blend_group(self.blend_mode),
                              'Opacity', self.opacity if self.enabled else 0.0,
                              color, alpha, clip=clip)
