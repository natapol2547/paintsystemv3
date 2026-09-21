"""Clipping layers (PS-013).

A clipped layer composites onto the content of the first unclipped layer
below it, its base, with the blend group's Clip input on. The base then
blends the result over the stack below with its own blend mode, opacity
and visibility. Expected pixels come from the coverage rule in PS-001
applied by hand, run by run.
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, import_from, multiply_blend, over,  # noqa: E402
                     pixel_at, register_addon, section)

register_addon()
core = import_from("compiler.core")
IR = import_from("compiler.ir").IR
stack_ops = import_from("nodetree.stack_ops")

SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
TRANSPARENT = (0.0, 0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0, 1.0)
GREY = (0.5, 0.5, 0.5, 1.0)
RED = (1.0, 0.0, 0.0, 1.0)
HALF_RED = (1.0, 0.0, 0.0, 0.5)
GREEN = (0.0, 1.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
YELLOW = (1.0, 1.0, 0.0, 1.0)


def new_tree(name):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    return tree


def solid(tree, name, color, target=None, clip=False):
    node = tree.insert_layer_node(SOLID, target=target)
    node.name = name
    node.fill_color = color
    node.is_clip = clip
    return node


def folder(tree, name, target=None, clip=False):
    node = tree.insert_layer_node(FOLDER, target=target)
    node.name = name
    node.is_clip = clip
    return node


def composite(tree):
    core.compile_tree(tree)
    rgba = bake_group(tree.compiled, color="Color", alpha="Color Alpha", size=4)
    return pixel_at(rgba, 0.5, 0.5, 4)


def check_pixel(label, tree, want, tol=1e-3):
    got = composite(tree)
    check(close(got, want, tol), f"{label}: {fmt(got)} expected {fmt(want)}")


try:
    section("clip bases")
    tree = new_tree("Roles")
    bottom = solid(tree, "Bottom", WHITE)
    base = solid(tree, "Base", HALF_RED)
    lower = solid(tree, "Lower", GREEN, clip=True)
    upper = solid(tree, "Upper", BLUE, clip=True)
    top = solid(tree, "Top", GREY)
    check(stack_ops.layer_below(base) == bottom and stack_ops.layer_above(base) == lower,
          "layer_below and layer_above follow the stack")
    check(stack_ops.layer_below(bottom) is None and stack_ops.layer_above(top) is None,
          "no layer beyond the ends of the stack")
    check(stack_ops.clip_base(lower) == base and stack_ops.clip_base(upper) == base,
          "clipped layers clip to the first unclipped layer below")
    check(stack_ops.clip_base(base) is None and stack_ops.clip_base(top) is None,
          "unclipped layers have no base")
    check([stack_ops.feeds_clip_run(node) for node in (bottom, base, lower, upper, top)]
          == [False, True, True, False, False],
          "the base and the clipped layers under the top of the run feed the run")
    bottom.is_clip = True
    check(stack_ops.clip_base(bottom) is None, "a clipped layer with nothing below has no base")

    section("compositing")
    tree = new_tree("Clip Half")
    solid(tree, "Base", HALF_RED)
    clipped = solid(tree, "Clipped", BLUE, clip=True)
    run = over(HALF_RED, BLUE, clip=True)
    check_pixel("a clipped layer takes its base's alpha", tree, over(TRANSPARENT, run))
    clipped.is_clip = False
    check_pixel("unclipped it covers the stack", tree, over(HALF_RED, BLUE))

    tree = new_tree("Clip Opacity")
    solid(tree, "Bottom", WHITE)
    base = solid(tree, "Base", RED)
    base.opacity = 0.5
    solid(tree, "Clipped", BLUE, clip=True)
    check_pixel("red base at 50% opacity with a clipped blue layer", tree,
                over(WHITE, over(RED, BLUE, clip=True), 0.5))

    tree = new_tree("Clip Transparent Base")
    solid(tree, "Bottom", GREY)
    solid(tree, "Base", (1.0, 0.0, 0.0, 0.0))
    solid(tree, "Clipped", BLUE, clip=True)
    check_pixel("a transparent base hides its clipped layers", tree, GREY)

    tree = new_tree("Clip Run")
    solid(tree, "Bottom", WHITE)
    base = solid(tree, "Base", HALF_RED)
    lower = solid(tree, "Lower", GREEN, clip=True)
    upper = solid(tree, "Upper", BLUE, clip=True)
    upper.opacity = 0.5
    run = over(over(HALF_RED, GREEN, clip=True), BLUE, 0.5, clip=True)
    check_pixel("clipped layers stack in order on their base", tree, over(WHITE, run))
    lower.enabled = False
    check_pixel("a disabled clipped layer drops out of the run", tree,
                over(WHITE, over(HALF_RED, BLUE, 0.5, clip=True)))
    lower.enabled = True
    base.enabled = False
    check_pixel("a disabled base hides its clipped layers", tree, WHITE)
    base.enabled = True

    tree = new_tree("Clip Blend Mode")
    solid(tree, "Bottom", GREY)
    base = solid(tree, "Base", RED)
    base.blend_mode = 'MULTIPLY'
    solid(tree, "Clipped", YELLOW, clip=True)
    check_pixel("the base's blend mode applies to its clipped layers", tree,
                over(GREY, over(RED, YELLOW, clip=True), blend=multiply_blend))

    section("ends of the stack and folders")
    tree = new_tree("Clip Bottom")
    solid(tree, "Only", HALF_RED, clip=True)
    check_pixel("a clipped layer at the bottom composites normally", tree, HALF_RED)

    tree = new_tree("Clip Folder Bottom")
    solid(tree, "Bottom", RED)
    box = folder(tree, "Box")
    solid(tree, "Inner", (0.0, 0.0, 1.0, 0.5), target=box, clip=True)
    check_pixel("a clipped layer at the bottom of a folder composites normally", tree,
                over(RED, (0.0, 0.0, 1.0, 0.5)))

    tree = new_tree("Clip Onto Folder")
    box = folder(tree, "Box")
    solid(tree, "Inner", HALF_RED, target=box)
    solid(tree, "Clipped", BLUE, clip=True)
    check_pixel("a folder is a base for the layers clipped above it", tree,
                over(TRANSPARENT, over(HALF_RED, BLUE, clip=True)))

    tree = new_tree("Clip Onto Empty Folder")
    solid(tree, "Bottom", GREY)
    folder(tree, "Empty")
    solid(tree, "Clipped", BLUE, clip=True)
    check_pixel("layers clipped to an empty folder are hidden", tree, GREY)

    tree = new_tree("Clipped Folder")
    solid(tree, "Base", HALF_RED)
    box = folder(tree, "Box", clip=True)
    solid(tree, "Inner", BLUE, target=box)
    check_pixel("a clipped folder clips its content as one", tree,
                over(TRANSPARENT, over(HALF_RED, BLUE, clip=True)))

    section("moves")
    tree = new_tree("Clip Move")
    solid(tree, "Bottom", GREEN)
    base = solid(tree, "Base", HALF_RED)
    clipped = solid(tree, "Clipped", BLUE, clip=True)
    check_pixel("before the move", tree, over(GREEN, over(HALF_RED, BLUE, clip=True)))
    check(tree.move_layer_node(base, 'DOWN', 'SKIP'), "move the base below the bottom layer")
    check(clipped.is_clip and stack_ops.clip_base(clipped) == tree.nodes["Bottom"],
          "the clipped layer keeps is_clip and clips to the layer now below it")
    check_pixel("after the move", tree, over(HALF_RED, over(GREEN, BLUE, clip=True)))

    section("compile")
    tree = new_tree("Clip Fingerprint")
    solid(tree, "Base", RED)
    clipped = solid(tree, "Clipped", BLUE)
    unclipped = core.compile_tree(tree)
    clipped.is_clip = True
    check(core.artifact_fingerprint(tree) != unclipped, "toggling clip recompiles")
    clipped.is_clip = False
    check(core.artifact_fingerprint(tree) == unclipped, "toggling it back restores the artifact")

    section("caches")
    tree = new_tree("Clip Cache")
    solid(tree, "Bottom", WHITE)
    base = solid(tree, "Base", RED)
    clipped = solid(tree, "Clipped", BLUE, clip=True)
    cache = bpy.data.images.new("PS Test Clip Cache", 4, 4)
    for node in (base, clipped):
        node.cache_image = cache
        node.cache_enabled = True
        node.cache_hash = core.CompileContext(IR()).subtree_hash(node)
    ctx = core.CompileContext(IR())
    check(not ctx.is_cached(base), "a base's cache is not used: its outputs are the run")
    check(ctx.is_cached(clipped), "the top of a run uses its cache")
    core.compile_tree(tree)
    identifiers = {node.get("ps_identifier") for node in tree.compiled.nodes}
    check(f"{clipped.uuid}:cache" in identifiers and f"{base.uuid}:blend" not in identifiers,
          "a cached run's top replaces the run and the stack below it")
    clipped.cache_enabled = False
    core.compile_tree(tree)
    identifiers = {node.get("ps_identifier") for node in tree.compiled.nodes}
    check(f"{base.uuid}:cache" not in identifiers and f"{base.uuid}:blend" in identifiers,
          "an uncached run compiles its base live")
except Exception:
    import traceback
    traceback.print_exc()
    check(False, "clip test raised")

finish("CLIP TEST")
