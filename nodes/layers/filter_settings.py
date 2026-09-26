"""The settings of each kind of filter a filter layer runs (PS-057).

Each kind keeps its settings in a group of its own on the layer, such as
``node.blur.sigma`` or ``node.painter.seed``. `filters.layer_specs` says
which group belongs to which kind, and which of its settings to draw.

Every setting marks the tree dirty. The compile that follows compares
the layer's build fingerprint with its image, which is how a changed
setting makes the layer out of date. None of them is hashed by the
compiler (``ps_unhashed_props``). A group could not be anyway: every
group of a type reads back with the same ``repr``, so `compiler.ir`
would hash them all alike.

Blender cannot make a path to a group inside a node. So these settings
cannot be keyframed or driven from the UI, and ``nodes.layers.links``
finds a group's layer by searching the tree.
"""
from math import pi, tau

from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

from ..base_node import mark_tree_dirty
from ...filters.painter.brushes import brush_items
from ...filters.registry import BLUR_MAX_EFFECTIVE_SIGMA


class PaintSystemInvertSettings(PropertyGroup):
    alpha: BoolProperty(
        name="Invert Alpha", default=False, update=mark_tree_dirty,
        description="Invert transparency as well as colour")


class PaintSystemBlurSettings(PropertyGroup):
    sigma: FloatProperty(
        name="Blur", default=4.0, min=0.0, max=BLUR_MAX_EFFECTIVE_SIGMA,
        subtype='PIXEL', update=mark_tree_dirty,
        description="Width of the blur, in pixels of the image this layer builds. "
                    "A layer set to a higher resolution therefore blurs less of "
                    "the picture for the same number")


class PaintSystemSharpenSettings(PropertyGroup):
    radius: FloatProperty(
        name="Radius", default=1.0, min=0.0, max=16.0,
        subtype='PIXEL', update=mark_tree_dirty,
        description="How far from an edge the detail to bring out is, in pixels "
                    "of the image this layer builds")
    strength: FloatProperty(
        name="Strength", default=1.0, min=0.0, soft_max=3.0, max=10.0,
        update=mark_tree_dirty,
        description="How much of that detail to add back")


class PaintSystemPainterSettings(PropertyGroup):
    """Painterly settings.

    The defaults match v2's brush painter, except the seed, which is
    always used (see ``filters.painter.plan``). Sizes, coverage and the
    threshold are percentages here and fractions in ``plan.Settings``,
    as in v2.
    """

    brush: EnumProperty(
        name="Brush", items=brush_items(), update=mark_tree_dirty,
        description="The brushes the strokes are stamped with")
    largest_stroke: FloatProperty(
        name="Largest Stroke", default=10.0, min=0.1, max=100.0, soft_max=30.0,
        subtype='PERCENTAGE', precision=1, step=10, update=mark_tree_dirty,
        description="Size of the strokes in the first pass, the broadest, as a percentage "
                    "of the image. The strokes are the same at any resolution")
    smallest_stroke: FloatProperty(
        name="Smallest Stroke", default=3.0, min=0.1, max=100.0, soft_max=10.0,
        subtype='PERCENTAGE', precision=1, step=10, update=mark_tree_dirty,
        description="Size of the strokes in the last pass, the finest, as a percentage "
                    "of the image")
    passes: IntProperty(
        name="Passes", default=4, min=1, max=20, update=mark_tree_dirty,
        description="How many passes of strokes to paint, stepping from the largest "
                    "strokes down to the smallest. A single pass paints only the smallest")
    first_opacity: FloatProperty(
        name="First Pass Opacity", default=0.4, min=0.0, max=1.0, subtype='FACTOR',
        update=mark_tree_dirty,
        description="Opacity of the strokes in the first pass. The passes between step "
                    "evenly from this to Last Pass Opacity")
    last_opacity: FloatProperty(
        name="Last Pass Opacity", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=mark_tree_dirty,
        description="Opacity of the strokes in the last pass, and in the only pass when "
                    "there is one")
    coverage: FloatProperty(
        name="Coverage", default=70.0, min=1.0, max=200.0, soft_min=10.0, soft_max=100.0,
        subtype='PERCENTAGE', precision=0, step=100, update=mark_tree_dirty,
        description="How much of the picture each pass covers with strokes. Lower values "
                    "leave more of the picture below showing between them")
    edge_threshold: FloatProperty(
        name="Edge Threshold", default=0.0, min=0.0, max=100.0, soft_max=50.0,
        subtype='PERCENTAGE', precision=0, step=100, update=mark_tree_dirty,
        description="Place strokes only where the edge under them is at least this strong, "
                    "as a percentage of the picture's strongest edge. Away from the strong "
                    "edges the picture shows through, and 0 places strokes everywhere")
    smoothing: FloatProperty(
        name="Smoothing", default=3.0, min=0.0, max=10.0, precision=1, step=10,
        update=mark_tree_dirty,
        description="How much fine detail the strokes ignore when they take their colour "
                    "and direction from the picture. Measured in pixels of a 2048 image "
                    "and scaled with the resolution, so the painting looks the same at "
                    "any resolution")
    rotation: FloatProperty(
        name="Rotation", default=0.0, min=-pi, max=pi, subtype='ANGLE',
        update=mark_tree_dirty,
        description="Turn every stroke by this much from the direction of the edge under it")
    random_rotation: FloatProperty(
        name="Random Rotation", default=0.0, min=0.0, max=tau, subtype='ANGLE',
        update=mark_tree_dirty,
        description="Turn each stroke by a random amount within a fan this wide, centred "
                    "on its direction. 0 turns no stroke at random, and a full turn points "
                    "strokes anywhere")
    hue: FloatProperty(
        name="Hue", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=mark_tree_dirty,
        description="How far each stroke's hue can wander from the picture's")
    saturation: FloatProperty(
        name="Saturation", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=mark_tree_dirty,
        description="How far each stroke's saturation can wander from the picture's")
    value: FloatProperty(
        name="Value", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=mark_tree_dirty,
        description="How far each stroke's brightness can wander from the picture's")
    seed: IntProperty(
        name="Seed", default=42, min=0, max=1000000, update=mark_tree_dirty,
        description="Which arrangement of strokes to paint. The same seed paints "
                    "the same strokes, so a refresh after painting below moves none")


classes = (
    PaintSystemInvertSettings,
    PaintSystemBlurSettings,
    PaintSystemSharpenSettings,
    PaintSystemPainterSettings,
)
