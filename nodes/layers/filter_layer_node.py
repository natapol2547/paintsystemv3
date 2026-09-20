import bpy
from bpy.types import Node
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, PointerProperty,
                       StringProperty)
from bpy.utils import register_classes_factory

from .base_layer_node import PaintSystemLayerNode, emit_image_texture
from ..base_node import mark_tree_dirty
from ...common import blender_icon, icon_kwargs
from ...compiler.library import filter_mix_group
from ...filters.derived import FINGERPRINT_KEY, build_stamp, is_built, stamped_uv_map
from ...filters.freshness import PIXEL_REASON, fingerprint_parts, structure_reason
from ...filters.layer_specs import LAYER_FILTERS, layer_filter_items, layer_filter_params
from ...filters.registry import BLUR_MAX_EFFECTIVE_SIGMA
from ...filters import layer_job
from ...ops.node_tree_ops import RESOLUTION_ITEMS


def _auto_refresh_changed(self, context):
    """Turning it back on is how a layer the job gave up on is retried."""
    if self.auto_refresh:
        self.derived_error = ""
        layer_job.notify()


def _stale_pixels_changed(self, context):
    """A stroke below the layer, which no compile has to run for.

    `filters.freshness` writes this flag from the depsgraph and from the
    addon's own pixel writes, neither of which marks the tree -- so the
    write is the only notice the auto path gets.
    """
    if self.derived_stale_pixels and self.auto_refresh:
        layer_job.notify()


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
        'filter_type', 'invert_alpha', 'blur_sigma', 'sharpen_radius', 'sharpen_strength',
        'resolution', 'uv_map', 'auto_refresh',
        'derived_image', 'derived_stale_reason', 'derived_stale_pixels', 'derived_error',
    )

    filter_type: EnumProperty(
        name="Filter", items=layer_filter_items(), update=mark_tree_dirty,
        description="What this layer does to the layers below it")
    invert_alpha: BoolProperty(
        name="Invert Alpha", default=False, update=mark_tree_dirty,
        description="Invert transparency as well as colour")

    blur_sigma: FloatProperty(
        name="Blur", default=4.0, min=0.0, max=BLUR_MAX_EFFECTIVE_SIGMA,
        subtype='PIXEL', update=mark_tree_dirty,
        description="Width of the blur, in pixels of the image this layer builds. "
                    "A layer set to a higher resolution therefore blurs less of "
                    "the picture for the same number")

    sharpen_radius: FloatProperty(
        name="Radius", default=1.0, min=0.0, max=16.0,
        subtype='PIXEL', update=mark_tree_dirty,
        description="How far from an edge the detail to bring out is, in pixels "
                    "of the image this layer builds")
    sharpen_strength: FloatProperty(
        name="Strength", default=1.0, min=0.0, soft_max=3.0, max=10.0,
        update=mark_tree_dirty,
        description="How much of that detail to add back")

    resolution: EnumProperty(
        name="Resolution", items=RESOLUTION_ITEMS, default='2048',
        update=mark_tree_dirty, description="Size of the image this filter builds")
    uv_map: StringProperty(
        name="UV Map", update=mark_tree_dirty,
        description="UV map the filter is built in (empty: active render UV map)")
    auto_refresh: BoolProperty(
        name="Auto Refresh", default=True, update=_auto_refresh_changed,
        description="Rebuild this layer shortly after the layers below it change")

    # Derived. The pixels describe themselves through the stamps on the
    # image, so this pointer is all the node has to keep.
    derived_image: PointerProperty(
        type=bpy.types.Image, name="Result", update=mark_tree_dirty)
    # Written by `emit_source` on every compile, and by nothing else. No
    # update callback: it is the compiler's own answer, and tagging the
    # tree from inside a compile would schedule another one.
    derived_stale_reason: StringProperty(
        name="Out Of Date", description="Why this layer's image no longer matches what is below it")
    # Written from outside the compile, by `filters.freshness`. Saved,
    # unlike the reason above: nothing recomputes it for free after a file
    # read, and a file saved out of date has to reopen out of date rather
    # than show a fresh badge over pixels that are not.
    derived_stale_pixels: BoolProperty(
        name="Pixels Changed", update=_stale_pixels_changed,
        description="Painting below this layer has changed what it should show")
    # Why the last automatic refresh stopped, shown until the next one is
    # asked for. A refusal from a timer has nowhere else to go: a popup
    # from a background job would interrupt whatever the user was doing.
    derived_error: StringProperty(
        name="Refresh Problem", description="Why this layer last failed to refresh itself")

    def copy(self, node):
        super().copy(node)
        # The derived image is this layer's content and not a re-derivable
        # artifact, so the duplicate gets its own rather than sharing the
        # original's pixels with it. Unlike a cache it is packed, so the
        # copy arrives with the pixels and keeps rendering straight away.
        if self.derived_image is not None:
            self.derived_image = self.derived_image.copy()

    @classmethod
    def create(cls, tree, target=None, filter_type='INVERT', resolution='2048', **options):
        node = super().create(tree, target=target)
        node.filter_type = filter_type
        node.resolution = resolution
        return node

    @property
    def stale_reason(self) -> str:
        """Why the built image no longer matches, or "" when it does.

        The two halves of freshness in the order that names the more
        specific cause: a structural change explains itself, while
        "the pixels below changed" is what is left once nothing
        structural has moved. An unbuilt layer has no claim to be out of
        date about.
        """
        if not is_built(self.derived_image):
            return ""
        if self.derived_stale_reason:
            return self.derived_stale_reason
        return PIXEL_REASON if self.derived_stale_pixels else ""

    # -- ui ---------------------------------------------------------------------

    def draw_label(self):
        super().draw_label()
        spec = LAYER_FILTERS.get(self.filter_type)
        return spec.label if spec is not None else self.bl_label

    def draw_row_state(self, layout):
        """Out of date is the one layer state the viewport cannot show.

        Every other layer renders what it is; this one renders what it
        was built from, so the list row is the only place a difference
        can appear at all.
        """
        if layer_job.running_on(self):
            layout.label(text="", **icon_kwargs('FILE_REFRESH'))
        elif self.stale_reason:
            layout.label(text="", **icon_kwargs('ERROR'))

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
        self.draw_result_settings(context, layout)

    def draw_result_settings(self, context, layout):
        """The derived image, as a label and two buttons.

        No ``template_ID``. The slot holds what this layer built and
        nothing else; a picker on it would let artwork be dropped in with
        nothing marking it read-only, and the next Update would overwrite
        it.
        """
        image = self.derived_image
        stale = self.stale_reason
        running = layer_job.running_on(self)
        box = layout.box()
        row = box.row(align=True)
        if running:
            row.label(text="Refreshing", **icon_kwargs('FILE_REFRESH'))
        elif not is_built(image):
            row.label(text="Not built", **icon_kwargs('ERROR'))
        elif stale:
            row.label(text="Out of date", **icon_kwargs('ERROR'))
        else:
            row.label(text=f"{image.size[0]} x {image.size[1]}", **icon_kwargs('CHECKMARK'))
        if running:
            row.operator("paint_system.cancel_filter_refresh", text="Cancel",
                         **icon_kwargs('X'))
        else:
            row.operator("paint_system.rebuild_filter_layer", text="Update",
                         **icon_kwargs('FILE_REFRESH'))
            clear = row.row(align=True)
            clear.enabled = image is not None
            clear.operator("paint_system.clear_filter_result", text="", **icon_kwargs('X'))
        box.prop(self, "auto_refresh")
        if self.derived_error:
            box.label(text=self.derived_error, **icon_kwargs('ERROR'))
        elif stale:
            box.label(text=f"Rebuild: {stale}")
        elif image is not None:
            box.label(text=image.name)

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
        self._note_stale(ctx, image)
        if not is_built(image):
            return (0.0, 0.0, 0.0, 1.0), 0.0
        # The UV map stamped on the image, not the authored one: changing
        # the setting relocates nothing until the rebuild runs.
        return emit_image_texture(ctx, self, 'result', image, stamped_uv_map(image))

    def _note_stale(self, ctx, image):
        """Compare the stack below with what the image was built from.

        The compile is the one moment that has both a context to hash
        against and a reason to look, so the answer is worked out here
        and parked on the node for the panel to read.
        """
        if not is_built(image):
            reason = ""
        else:
            link = ctx.incoming_link(self.inputs['Color'])
            parts = fingerprint_parts(ctx, self, link.from_node if link else None)
            reason = structure_reason(str(image.get(FINGERPRINT_KEY, "")), parts)
        # Writing an RNA property tags the tree and the materials using
        # it, so only write when the value actually changes.
        if self.derived_stale_reason != reason:
            self.derived_stale_reason = reason
        # The compile is also where the auto path learns there is work:
        # every way a filter layer goes out of date ends up here, the
        # structural ones and the painted one alike.
        if self.stale_reason:
            if self.auto_refresh:
                layer_job.notify()
        else:
            layer_job.settled(self)

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
