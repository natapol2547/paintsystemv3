"""A filter layer builds pixels, and the artifact shows them (PS-057).

`filters.layer_build` is the whole lifecycle in one call: composite the
stack below, run the layer's filter over it, encode it into the derived
image's storage, and commit. What is checked here is the two ends of
that -- the values that land in the image, and what the compiled artifact
renders once they have -- because everything between them is where a
colour space is easy to lose.

The arithmetic is spelled out rather than compared against another run of
the same code. Inverting a layer whose fill is 0.2 scene linear stores
``1 - srgb(0.2)`` and renders ``linear(1 - srgb(0.2))``; if either end
ever inverts the linear value directly, a mid grey goes almost white and
these numbers say so.

These need a GPU context. Blender 5.2 added `gpu.init()`, which builds
one in background mode, so they run in the ordinary headless job there.
Background 4.2 to 5.1 have no way to get one and skip; that coverage
comes from the windowed job.
"""
import os
import sys
import traceback

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, finish, fmt, import_from,  # noqa: E402
                     register_addon, section, skip)

register_addon()
gpu_core = import_from("gpu_passes.core")
core = import_from("compiler.core")
derived = import_from("filters.derived")
filters_core = import_from("filters.core")
layer_build = import_from("filters.layer_build")
create_managed_image = import_from("compiler.bake").create_managed_image

SOLID = 'PaintSystemSolidColorLayerNode'
IMAGE = 'PaintSystemImageLayerNode'
FILTER = 'PaintSystemFilterLayerNode'

# The derived image is byte, so a stored value is only ever exact to half
# a step; the bake of it carries the same error into linear.
TOL = 1.5 / 255.0
# The bake plane's UV covers 0..1 exactly. The stacks compared that way
# are all one colour, so the size only has to be cheap.
BAKE_SIZE = 8


def to_srgb(value):
    c = np.clip(np.asarray(value, dtype=np.float64), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1.0 / 2.4) - 0.055)


def to_linear(value):
    c = np.clip(np.asarray(value, dtype=np.float64), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def texel(image, index=0):
    """Texel *index* of *image*, in the values the image stores."""
    return tuple(image.pixels[index * 4:index * 4 + 4])


def artifact_pixel(tree):
    """The middle texel of a Cycles bake of what *tree* compiles to."""
    baked = bake_group(tree.compiled, size=BAKE_SIZE).reshape(BAKE_SIZE, BAKE_SIZE, 4)
    return tuple(float(v) for v in baked[BAKE_SIZE // 2, BAKE_SIZE // 2])


def agrees(got, want, label):
    worst = float(np.abs(np.asarray(got) - np.asarray(want)).max())
    check(worst <= TOL, f"{label}: {fmt(got)} vs {fmt(tuple(float(v) for v in want))}, "
                        f"worst {worst * 255:.2f}/255")


def refusal(call):
    try:
        call()
    except filters_core.Refused as error:
        return str(error)
    return ""


if available():
    try:
        section("a filter layer builds")
        tree = bpy.data.node_groups.new("Build", 'PaintSystemNodeTree')
        tree.initialize()
        with core.suspend_compile(tree):
            bottom = tree.insert_layer_node(SOLID)
            bottom.fill_color = (0.2, 0.6, 0.4, 1.0)
            node = tree.insert_layer_node(FILTER)
            # The smallest the enum offers; every check here is on one
            # colour, so the size only costs time.
            node.resolution = '1024'
        core.flush_now()

        image = layer_build.build_layer(bpy.context, tree, node)
        check(node.derived_image == image, f"the layer points at what it built ({image.name})")
        check(tuple(image.size) == (1024, 1024), f"built at the asked-for size {tuple(image.size)}")
        check(not image.is_float and image.colorspace_settings.name == 'sRGB',
              f"byte and sRGB like a painted layer ({image.colorspace_settings.name})")
        check(image.packed_file is not None, "and packed, so it survives into the next .blend")
        check(derived.is_built(image), "the stamps say it is built")
        check(image[derived.OWNER_KEY] == f"{tree.uuid}:{node.uuid}",
              "and name the layer that built it")
        check(derived.stamped_uv_map(image) == "",
              "no layer below named a UV map, so the result is in the active render one")

        section("what it stored")
        want = (*(1.0 - to_srgb([0.2, 0.6, 0.4])), 1.0)
        agrees(texel(image), want, "the inverse of the sRGB encoding, stored as the image's own")

        section("what the artifact renders")
        core.flush_now()
        agrees(artifact_pixel(tree), (*to_linear(want[:3]), 1.0),
               "decoded back to the colour that was filtered")

        node.opacity = 0.5
        core.flush_now()
        faded = 0.5 * np.asarray(to_linear(want[:3])) + 0.5 * np.asarray([0.2, 0.6, 0.4])
        agrees(artifact_pixel(tree), (*faded, 1.0), "Amount fades between the two, unrebuilt")
        node.opacity = 1.0
        core.flush_now()

        section("alpha")
        bottom.fill_color = (0.2, 0.6, 0.4, 0.6)
        core.flush_now()
        image = layer_build.build_layer(bpy.context, tree, node)
        agrees(texel(image)[3:], [0.6], "Invert leaves transparency alone by default")

        node.invert_alpha = True
        image = layer_build.build_layer(bpy.context, tree, node)
        agrees(texel(image)[3:], [0.4], "and inverts it when asked")
        node.invert_alpha = False
        bottom.fill_color = (0.2, 0.6, 0.4, 1.0)
        core.flush_now()

        section("the build stamp")
        with core.suspend_compile(tree):
            picture = tree.insert_layer_node(IMAGE, target=bottom)
            picture.image = create_managed_image("Build Source", 8, 8)
            picture.image.pixels.foreach_set([0.3, 0.5, 0.7, 1.0] * 64)
            picture.image.update()
        core.flush_now()

        layer_build.build_layer(bpy.context, tree, node)
        first = dict(image.items())
        layer_build.build_layer(bpy.context, tree, node)
        check(image[derived.BUILD_KEY] == first[derived.BUILD_KEY],
              "building the same stack twice stamps the same build")

        picture.image.pixels.foreach_set([0.8, 0.2, 0.1, 1.0] * 64)
        picture.image.update()
        layer_build.build_layer(bpy.context, tree, node)
        check(image[derived.FINGERPRINT_KEY] == first[derived.FINGERPRINT_KEY],
              "painting below moves no property, so the structural fingerprint holds")
        check(image[derived.BUILD_KEY] != first[derived.BUILD_KEY],
              "but the build stamp changes, so a cache above cannot show the old pixels")

        agrees(texel(image), (*(1.0 - to_srgb(to_linear([0.8, 0.2, 0.1]))), 1.0),
               "and the image layer below is what came out inverted")

        section("the datablock is reused")
        node.resolution = '2048'
        again = layer_build.build_layer(bpy.context, tree, node)
        check(again == image, "a resolution change scales the image rather than replacing it")
        check(tuple(again.size) == (2048, 2048), f"to {tuple(again.size)}")

        section("what it refuses")
        alone = bpy.data.node_groups.new("Build Empty", 'PaintSystemNodeTree')
        alone.initialize()
        lonely = alone.insert_layer_node(FILTER)
        core.flush_now()
        check("nothing below" in refusal(
            lambda: layer_build.build_layer(bpy.context, alone, lonely)),
            "a filter layer with nothing under it")

        inner = bpy.data.node_groups.new("Build Inner", 'PaintSystemNodeTree')
        inner.initialize()
        group = tree.nodes.new('PaintSystemGroupLayerNode')
        group.node_tree = inner
        tree.links.new(group.outputs['Color'], picture.inputs['Color'])
        core.flush_now()
        message = refusal(lambda: layer_build.build_layer(bpy.context, tree, node))
        check("Cycles bake" in message, f"and one whose input needs a render: {message}")

    except Exception:
        traceback.print_exc()
        check(False, "unexpected exception")

# Python's own teardown would free them after the GPU context has gone,
# which segfaults a background Blender.
import_from("filters.composite").release()
import_from("filters.blend_glsl").release()
filters_core.release()

finish("FILTER BUILD TEST")
