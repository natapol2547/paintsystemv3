"""The filter layer type and how it compiles (PS-057).

This covers the layer on its own: an unbuilt filter layer is an exact
pass-through, a built one substitutes its derived image for the stack
below through the Filter Mix group, and none of what the filter is asked
for reaches the compiler's fingerprints until a build has stamped the
image. Building the image is a separate concern and a separate test.
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, import_from,  # noqa: E402
                     pixel_at, register_addon, section)

register_addon()
core = import_from("compiler.core")
derived = import_from("filters.derived")
create_managed_image = import_from("compiler.bake").create_managed_image

SOLID = 'PaintSystemSolidColorLayerNode'
FILTER = 'PaintSystemFilterLayerNode'
SIZE = 4


def identifiers(tree):
    return {node.get("ps_identifier") for node in tree.compiled.nodes}


def node_by_id(tree, identifier):
    return next(node for node in tree.compiled.nodes
                if node.get("ps_identifier") == identifier)


def artifact_pixel(tree):
    rgba = bake_group(tree.compiled, size=SIZE)
    return pixel_at(rgba, 0.5, 0.5, SIZE)


def built_image(name, color):
    """An image that looks to the compiler like a finished filter result."""
    image = create_managed_image(name, 8, 8, colorspace='Non-Color')
    image.pixels.foreach_set(list(color) * (8 * 8))
    image.update()
    image[derived.BUILD_KEY] = f"build-of-{name}"
    image[derived.UV_MAP_KEY] = ""
    return image


try:
    section("an unbuilt filter layer is a pass-through")
    tree = bpy.data.node_groups.new("Filters", 'PaintSystemNodeTree')
    tree.initialize()
    with core.suspend_compile(tree):
        solid = tree.insert_layer_node(SOLID)
        solid.fill_color = (0.2, 0.6, 0.4, 1.0)
    core.flush_now()
    plain = artifact_pixel(tree)
    check(close(plain, (0.2, 0.6, 0.4, 1.0)), f"the stack below alone {fmt(plain)}")

    node = tree.insert_layer_node(FILTER)
    core.flush_now()
    check(node.derived_image is None and not derived.is_built(node.derived_image),
          "a new filter layer has no result yet")
    check(node.paint_image is None, "and is not paintable, so no brush can target it")
    got = artifact_pixel(tree)
    check(close(got, plain), f"adding it changes nothing on screen {fmt(got)}")
    check(f"{node.uuid}:fmix" in identifiers(tree), "it still compiles to a Filter Mix group")
    check(f"{node.uuid}:result" not in identifiers(tree),
          "with no image texture, because there is no image")
    fmix = node_by_id(tree, f"{node.uuid}:fmix")
    check(fmix.inputs['Amount'].default_value == 0.0, "Amount is forced to 0 until it is built")
    check(not fmix.inputs['Color'].is_linked, "and its Color input is left unlinked")

    section("a built filter layer replaces the stack below")
    image = built_image("Filter Result", (0.9, 0.1, 0.1, 1.0))
    node.derived_image = image
    core.flush_now()
    check(f"{node.uuid}:result" in identifiers(tree), "the derived image compiles to an image texture")
    fmix = node_by_id(tree, f"{node.uuid}:fmix")
    check(fmix.inputs['Amount'].default_value == 1.0, "Amount follows the layer's Opacity")
    got = artifact_pixel(tree)
    check(close(got, (0.9, 0.1, 0.1, 1.0)), f"at full Amount the result is the filtered pixels {fmt(got)}")

    node.opacity = 0.25
    core.flush_now()
    got = artifact_pixel(tree)
    want = tuple(a + (b - a) * 0.25 for a, b in zip((0.2, 0.6, 0.4), (0.9, 0.1, 0.1))) + (1.0,)
    check(close(got, want), f"Amount crossfades back towards the stack below {fmt(got)}")
    node.opacity = 1.0

    node.enabled = False
    core.flush_now()
    got = artifact_pixel(tree)
    check(close(got, plain), f"disabling it is a pass-through again {fmt(got)}")
    node.enabled = True

    node.derived_image = None
    core.flush_now()
    got = artifact_pixel(tree)
    check(close(got, plain), f"losing the image gives the original back, not a black band {fmt(got)}")
    node.derived_image = image
    core.flush_now()

    section("what the compiler's fingerprints see")
    before = core.build_ir(tree).ctx.subtree_hash(node)
    node.blur_sigma = 12.0
    node.resolution = '4096'
    node.uv_map = "SomeOtherMap"
    check(core.build_ir(tree).ctx.subtree_hash(node) == before,
          "asking for a different filter changes no hash before the rebuild")
    spare = built_image("Filter Result Spare", (0.0, 0.0, 1.0, 1.0))
    node.derived_image = spare
    check(core.build_ir(tree).ctx.subtree_hash(node) != before,
          "but different pixels do, so a cache above cannot go on showing the old ones")
    node.derived_image = image
    check(core.build_ir(tree).ctx.subtree_hash(node) == before, "and swapping back restores it")
    bpy.data.images.remove(spare)

    image[derived.BUILD_KEY] = "rebuilt"
    check(core.build_ir(tree).ctx.subtree_hash(node) != before,
          "a rebuild of the same image invalidates it too")
    image[derived.BUILD_KEY] = "build-of-Filter Result"

    section("the blend mode is not offered")
    check(not node.ps_shows_blend_mode and node.ps_opacity_label == "Amount",
          "a filter layer shows Amount and no blend mode")
    check(tree.nodes[solid.name].ps_shows_blend_mode, "an ordinary layer still shows one")

    section("as a clip base")
    clipped = tree.insert_layer_node(SOLID, target=node)
    clipped.fill_color = (1.0, 0.0, 1.0, 1.0)
    clipped.is_clip = True
    node.derived_image = None
    core.flush_now()
    got = artifact_pixel(tree)
    check(close(got, plain),
          f"clipped to an unbuilt filter there is nothing to clip to, so the stack below shows {fmt(got)}")
    node.derived_image = image
    core.flush_now()
    got = artifact_pixel(tree)
    check(close(got, (1.0, 0.0, 1.0, 1.0)),
          f"once built, the clipped layer shows over the filtered result {fmt(got)}")
    tree.remove_layer_node(clipped)
    core.flush_now()

    section("duplicating a filter layer")
    duplicate_tree = tree.copy()
    duplicate = duplicate_tree.nodes[node.name]
    check(duplicate.derived_image is not None and duplicate.derived_image != image,
          "the copy gets its own derived image, not a second pointer to the original's")
    check(duplicate.uuid != node.uuid, "and its own uuid")
    bpy.data.node_groups.remove(duplicate_tree)

except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("FILTER LAYER TEST")
