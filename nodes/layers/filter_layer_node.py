from math import pi, tau

import bpy
from bpy.types import Node
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.utils import register_classes_factory

from .base_layer_node import (EMPTY_SOURCE, RESOLUTION_ITEMS, PaintSystemLayerNode, draw_uv_map,
                              emit_image_texture, emit_mix_group, update_painting)
from ..base_node import mark_tree_dirty
from ...common import blender_icon, icon_kwargs
from ...compiler.library import filter_mix_group
from ...filters.derived import FINGERPRINT_KEY, build_stamp, is_built, stamped_uv_map
from ...filters.freshness import PIXEL_REASON, fingerprint_parts, structure_reason
from ...filters.layer_specs import LAYER_FILTERS, layer_filter_items, layer_filter_params
from ...filters.painter.brushes import brush_items
from ...filters.registry import BLUR_MAX_EFFECTIVE_SIGMA
from ...filters import layer_job
from ...nodetree.stack_ops import feeding_link


def _auto_refresh_changed(self, context):
    """Clear the error and ask for a refresh when Auto Refresh is turned on.

    This is how a layer that the refresh job gave up on is retried.
    """
    if self.auto_refresh:
        self.derived_error = ""
        if self.enabled:
            layer_job.notify()


def _enabled_changed(self, context):
    """Ask for the held-back refresh when the layer is switched back on.

    This is not left to the compile that ``mark_tree_dirty`` schedules.
    That compile usually reaches ``_note_stale``, but not always. A layer
    behind its own valid bake cache is not emitted at all. ``enabled`` is
    hashed, so switching it off and on again restores the hash the cache
    was baked at. Without this call, the layer would stay out of date with
    Auto Refresh on and nothing happening.
    """
    mark_tree_dirty(self, context)
    if self.enabled and self.auto_refresh and self.stale_reason:
        layer_job.notify()


def _lock_changed(self, context):
    """Ask for the held-back refresh when the layer is unlocked.

    ``lock_layer`` does not mark the tree dirty. It runs
    ``update_painting``, because the lock changes where a stroke goes, not
    what the tree compiles to. So no compile follows an unlock. Without
    this call, the layer would wait for the next unrelated edit before it
    catches up.
    """
    update_painting(self, context)
    if not self.lock_layer and self.enabled and self.auto_refresh and self.stale_reason:
        layer_job.notify()


def _stale_pixels_changed(self, context):
    """Ask for a refresh when painting below the layer makes it stale.

    No compile runs for a stroke. ``filters.freshness`` sets this flag
    from the depsgraph and from the addon's own pixel writes. Neither
    marks the tree, so this write is the only notice the auto refresh
    gets.
    """
    if self.derived_stale_pixels and self.auto_refresh and self.enabled:
        layer_job.notify()


class PaintSystemFilterLayerNode(PaintSystemLayerNode, Node):
    """A filter over the layers below, kept as a layer instead of applied.

    The layer owns a derived image that holds the filtered result. It is
    not a cache of the stack below. The compiler puts it *in place of*
    that stack through the Filter Mix group. So the layer's Opacity fades
    between the two without rebuilding anything.

    Design: docs/tickets/PS-057-filter-layer.md.
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
    # so a blend mode would mean nothing.
    ps_shows_blend_mode = False
    ps_opacity_label = "Amount"

    # The compiler's fingerprints leave out the filter settings, the build
    # settings and the build results. None of them changes the compiled
    # artifact until a build runs, and a build shows up through
    # ``hash_parts`` instead. Without this, dragging a blur slider would
    # invalidate every node cache and channel bake above the layer before
    # any pixel changed.
    ps_unhashed_props = (
        'filter_type', 'invert_alpha', 'blur_sigma', 'sharpen_radius', 'sharpen_strength',
        'painter_brush', 'painter_largest_stroke', 'painter_smallest_stroke', 'painter_passes',
        'painter_first_opacity', 'painter_last_opacity', 'painter_coverage',
        'painter_edge_threshold', 'painter_smoothing', 'painter_rotation',
        'painter_random_rotation', 'painter_hue', 'painter_saturation', 'painter_value',
        'painter_seed',
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

    # Painterly settings. The defaults match v2's brush painter, except the
    # seed, which is always used (see ``filters.painter.plan``). Sizes,
    # coverage and the threshold are percentages here and fractions in
    # ``plan.Settings``, as in v2.
    painter_brush: EnumProperty(
        name="Brush", items=brush_items(), update=mark_tree_dirty,
        description="The brushes the strokes are stamped with")
    painter_largest_stroke: FloatProperty(
        name="Largest Stroke", default=10.0, min=0.1, max=100.0, soft_max=30.0,
        subtype='PERCENTAGE', precision=1, step=10, update=mark_tree_dirty,
        description="Size of the strokes in the first pass, the broadest, as a percentage "
                    "of the image. The strokes are the same at any resolution")
    painter_smallest_stroke: FloatProperty(
        name="Smallest Stroke", default=3.0, min=0.1, max=100.0, soft_max=10.0,
        subtype='PERCENTAGE', precision=1, step=10, update=mark_tree_dirty,
        description="Size of the strokes in the last pass, the finest, as a percentage "
                    "of the image")
    painter_passes: IntProperty(
        name="Passes", default=4, min=1, max=20, update=mark_tree_dirty,
        description="How many passes of strokes to paint, stepping from the largest "
                    "strokes down to the smallest. A single pass paints only the smallest")
    painter_first_opacity: FloatProperty(
        name="First Pass Opacity", default=0.4, min=0.0, max=1.0, subtype='FACTOR',
        update=mark_tree_dirty,
        description="Opacity of the strokes in the first pass. The passes between step "
                    "evenly from this to Last Pass Opacity")
    painter_last_opacity: FloatProperty(
        name="Last Pass Opacity", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=mark_tree_dirty,
        description="Opacity of the strokes in the last pass, and in the only pass when "
                    "there is one")
    painter_coverage: FloatProperty(
        name="Coverage", default=70.0, min=1.0, max=200.0, soft_min=10.0, soft_max=100.0,
        subtype='PERCENTAGE', precision=0, step=100, update=mark_tree_dirty,
        description="How much of the picture each pass covers with strokes. Lower values "
                    "leave more of the picture below showing between them")
    painter_edge_threshold: FloatProperty(
        name="Edge Threshold", default=0.0, min=0.0, max=100.0, soft_max=50.0,
        subtype='PERCENTAGE', precision=0, step=100, update=mark_tree_dirty,
        description="Place strokes only where the edge under them is at least this strong, "
                    "as a percentage of the picture's strongest edge. Away from the strong "
                    "edges the picture shows through, and 0 places strokes everywhere")
    painter_smoothing: FloatProperty(
        name="Smoothing", default=3.0, min=0.0, max=10.0, precision=1, step=10,
        update=mark_tree_dirty,
        description="How much fine detail the strokes ignore when they take their colour "
                    "and direction from the picture. Measured in pixels of a 2048 image "
                    "and scaled with the resolution, so the painting looks the same at "
                    "any resolution")
    painter_rotation: FloatProperty(
        name="Rotation", default=0.0, min=-pi, max=pi, subtype='ANGLE',
        update=mark_tree_dirty,
        description="Turn every stroke by this much from the direction of the edge under it")
    painter_random_rotation: FloatProperty(
        name="Random Rotation", default=0.0, min=0.0, max=tau, subtype='ANGLE',
        update=mark_tree_dirty,
        description="Turn each stroke by a random amount within a fan this wide, centred "
                    "on its direction. 0 turns no stroke at random, and a full turn points "
                    "strokes anywhere")
    painter_hue: FloatProperty(
        name="Hue", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=mark_tree_dirty,
        description="How far each stroke's hue can wander from the picture's")
    painter_saturation: FloatProperty(
        name="Saturation", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=mark_tree_dirty,
        description="How far each stroke's saturation can wander from the picture's")
    painter_value: FloatProperty(
        name="Value", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=mark_tree_dirty,
        description="How far each stroke's brightness can wander from the picture's")
    painter_seed: IntProperty(
        name="Seed", default=42, min=0, max=1000000, update=mark_tree_dirty,
        description="Which arrangement of strokes to paint. The same seed paints "
                    "the same strokes, so a refresh after painting below moves none")

    resolution: EnumProperty(
        name="Resolution", items=RESOLUTION_ITEMS, default='2048',
        update=mark_tree_dirty, description="Size of the image this filter builds")
    uv_map: StringProperty(
        name="UV Map", update=mark_tree_dirty,
        description="UV map the filter is built in (empty: active render UV map)")
    auto_refresh: BoolProperty(
        name="Auto Refresh", default=True, update=_auto_refresh_changed,
        description="Rebuild this layer shortly after the layers below it change")

    # Redeclared only to add the callbacks. The compiler and the layer
    # list treat both as they do for any other layer. Either one can hold
    # a refresh back, and neither reliably causes a compile that would
    # notice. See ``_enabled_changed`` and ``_lock_changed``.
    enabled: BoolProperty(name="Enabled", default=True, update=_enabled_changed)
    lock_layer: BoolProperty(
        name="Lock Layer", default=False, update=_lock_changed,
        description="Prevent changes to this layer's settings")

    # The filter's result. Stamps on the image describe its pixels, so the
    # node only needs to keep this pointer.
    derived_image: PointerProperty(
        type=bpy.types.Image, name="Result", update=mark_tree_dirty)
    # Written only by ``emit_source``, on every compile. It has no update
    # callback. It is the compiler's own result, and tagging the tree from
    # inside a compile would schedule another compile.
    derived_stale_reason: StringProperty(
        name="Out Of Date", description="Why this layer's image no longer matches what is below it")
    # Written outside the compile, by ``filters.freshness``. Every compile
    # recomputes the reason above, but nothing recomputes this flag after
    # a file loads. So its saved value matters. A file saved out of date
    # must reopen out of date, not look fresh over stale pixels.
    derived_stale_pixels: BoolProperty(
        name="Pixels Changed", update=_stale_pixels_changed,
        description="Painting below this layer has changed what it should show")
    # Why the last automatic refresh stopped. It is shown until the next
    # refresh is asked for. A timer has no other place to report a
    # refusal, because a popup from a background job would interrupt the
    # user.
    derived_error: StringProperty(
        name="Refresh Problem", description="Why this layer last failed to refresh itself")

    def copy(self, node):
        super().copy(node)
        # The derived image counts as this layer's content, not as an
        # artifact to rebuild like a cache. So the copy gets its own image
        # instead of sharing the original's pixels. The image is packed, so
        # the copy has the pixels and renders straight away.
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

        A structural change is checked first, because it names the more
        specific cause. "The pixels below changed" is what is left when
        nothing structural has changed. An unbuilt layer is never out of
        date.
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
        """Show a refreshing or out-of-date badge on the layer row.

        Out of date is the one layer state the viewport cannot show. Every
        other layer renders what it is. This one renders what it was built
        from. So the list row is the only place the difference can appear.
        """
        if layer_job.running_on(self):
            layout.label(text="", **icon_kwargs('FILE_REFRESH'))
        elif self.stale_reason:
            layout.label(text="", **icon_kwargs('ERROR'))

    def draw_source_settings(self, context, layout):
        layout.prop(self, "filter_type", text="")
        for entry in layer_filter_params(self.filter_type):
            if isinstance(entry, str):
                layout.prop(self, entry)
                continue
            heading, names = entry
            column = layout.column(align=True)
            column.label(text=heading)
            for name in names:
                column.prop(self, name)
        layout.prop(self, "resolution")
        draw_uv_map(context, layout, self)
        self.draw_result_settings(context, layout)

    def draw_result_settings(self, context, layout):
        """Draw the derived image as a label and two buttons.

        There is no ``template_ID``. The slot holds only what this layer
        built. A picker would let the user drop artwork in, with nothing to
        show it is read-only. The next Update would then overwrite it.
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
        elif stale and self.auto_refresh and not (self.enabled and not self.lock_layer):
            # Without this, the layer sits out of date with Auto Refresh on
            # and nothing happening. That looks broken, but it is a
            # deliberate saving. Check both conditions, because
            # ``layer_job._candidates`` needs both. Naming only one could
            # send the user to switch on a layer that still does not
            # refresh.
            held = "switched on" if not self.enabled else "unlocked"
            box.label(text=f"Waiting until the layer is {held}", **icon_kwargs('INFO'))
        elif stale:
            box.label(text=f"Rebuild: {stale}")
        elif image is not None:
            box.label(text=image.name)

    # -- compiler -----------------------------------------------------------------

    @property
    def amount(self) -> float:
        """Opacity, but 0 until the filter has pixels.

        So an unbuilt filter layer passes the stack through unchanged in
        every arrangement, including as a clip base. Adding one changes
        nothing on screen until it is built. Losing its image gives back
        the original, not a black band.
        """
        if not self.enabled or not is_built(self.derived_image):
            return 0.0
        return self.opacity

    def hash_parts(self, ctx):
        # The derived image hashes by name, like any other datablock. Add
        # the build stamp of its pixels, so a cache above this layer sees
        # a rebuild.
        return [build_stamp(self.derived_image)]

    def emit_source(self, ctx):
        image = self.derived_image
        self._note_stale(ctx, image)
        if not is_built(image):
            return EMPTY_SOURCE
        # Use the UV map stamped on the image, not the authored one.
        # Changing the setting moves nothing until the rebuild runs.
        return emit_image_texture(ctx, self, 'result', image, stamped_uv_map(image))

    def _note_stale(self, ctx, image):
        """Compare the stack below with what the image was built from.

        The compile is the one moment that has both a context to hash
        against and a reason to look. So the answer is worked out here and
        stored on the node for the panel to read.
        """
        if not is_built(image):
            reason = ""
        else:
            link = feeding_link(self.inputs['Color'])
            parts = fingerprint_parts(ctx, self, link.from_node if link else None)
            reason = structure_reason(str(image.get(FINGERPRINT_KEY, "")), parts)
        # Writing an RNA property tags the tree and the materials that
        # use it for an update, so only write when the value changes.
        if self.derived_stale_reason != reason:
            self.derived_stale_reason = reason
        # The compile is also where the auto refresh learns there is work.
        # Every way a filter layer goes out of date ends up here, whether
        # structural or painted. That includes work it already had.
        # Switching the layer back on marks the tree, so this runs and asks
        # for the refresh that was held back while the layer was off.
        if self.stale_reason:
            if self.auto_refresh and self.enabled:
                layer_job.notify()
        else:
            layer_job.settled(self)

    def emit_blend(self, ctx, color, alpha, *, clip=False):
        return emit_mix_group(ctx, self, 'fmix', filter_mix_group(), 'Amount', self.amount,
                              color, alpha, clip=clip)


classes = (
    PaintSystemFilterLayerNode,
)


register, unregister = register_classes_factory(classes)
