"""Pixel tests for the layer blend library groups (PS-001).

Every case bakes a ``.PS Lib Layer Blend [<MODE>]`` group with constant
inputs and compares the texel with the closed form of the compositing
rule for that backdrop. With backdrop ``cb, ab``, layer colour ``cs`` and
layer coverage ``es = Alpha * Opacity * Mask``:

- transparent backdrop: the layer alone, ``cs`` at alpha ``es``
- opaque backdrop: ``mix(cb, B(cb, cs), es)`` at alpha 1, clipped or not
- half-transparent backdrop: ``mix(cs, B, 0.5)`` composited over the
  backdrop (W3C), or clipped: ``mix(cb, B, es)`` at alpha ``ab``

``B(cb, cs)`` is baked from a plain ShaderNodeMix of the same blend type
at factor 1, so these tests check the compositing and not Blender's blend
functions.
"""
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, import_from, pixel_at,  # noqa: E402
                     register_addon, section)

register_addon()
library = import_from("compiler.library")
BLEND_MODES = [item[0] for item in import_from("nodes.layers.base_layer_node").BLEND_MODE_ITEMS if item]

# Chosen so that no blend mode goes negative (SUBTRACT and LINEAR_LIGHT)
# and DIVIDE, DODGE and BURN stay away from their divide-by-zero branches.
# Values above 1 (ADD, DIVIDE) are fine: bakes are float.
CB = (0.8, 0.7, 0.6)
CS = (0.3, 0.4, 0.25)
TOL = 1e-3
SIZE = 4


def mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def over(color, alpha, backdrop, backdrop_alpha):
    """Straight-alpha source-over."""
    out_alpha = alpha + backdrop_alpha * (1.0 - alpha)
    if out_alpha == 0.0:
        return backdrop + (0.0,)
    return tuple((c * alpha + b * backdrop_alpha * (1.0 - alpha)) / out_alpha
                 for c, b in zip(color, backdrop)) + (out_alpha,)


def sample(group, **inputs):
    rgba = bake_group(group, color="Color", alpha="Alpha", inputs=inputs, size=SIZE)
    return pixel_at(rgba, 0.5, 0.5, SIZE)


def blend_function(mode):
    """B(CB, CS) for a blend mode, baked from a bare Mix node."""
    ref = bpy.data.node_groups.new(f"PS Test Blend Function {mode}", 'ShaderNodeTree')
    try:
        ref.interface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
        out = ref.nodes.new('NodeGroupOutput')
        node = ref.nodes.new('ShaderNodeMix')
        node.data_type = 'RGBA'
        node.blend_type = mode
        node.clamp_result = False
        node.inputs[library.MIX_IN_FACTOR].default_value = 1.0
        node.inputs[library.MIX_IN_A_COLOR].default_value = CB + (1.0,)
        node.inputs[library.MIX_IN_B_COLOR].default_value = CS + (1.0,)
        ref.links.new(node.outputs[library.MIX_OUT_COLOR], out.inputs['Color'])
        rgba = bake_group(ref, color="Color", alpha="Color", size=SIZE)
        return pixel_at(rgba, 0.5, 0.5, SIZE)[:3]
    finally:
        bpy.data.node_groups.remove(ref)


def check_pixel(label, got, want):
    check(close(got, want, TOL), f"{label}: {fmt(got)} expected {fmt(want)}")


section("blend function references")
functions = {mode: blend_function(mode) for mode in BLEND_MODES}
check(close(functions['MIX'], CS, TOL), f"MIX reference is the layer colour {fmt(functions['MIX'])}")
negative = {mode: fmt(f) for mode, f in functions.items() if min(f) < 0.0}
check(not negative, f"blend function references are non-negative {negative}")

for mode in BLEND_MODES:
    section(f"{mode}")
    group = library.layer_blend_group(mode)
    B = functions[mode]
    layer = {"Prev Color": CB + (1.0,), "Color": CS + (1.0,)}

    for opacity in (1.0, 0.4):
        es = opacity
        got = sample(group, **layer, **{"Prev Alpha": 0.0, "Opacity": opacity})
        check_pixel(f"{mode} over a transparent backdrop at opacity {opacity} is the layer alone",
                    got, CS + (es,))

        got = sample(group, **layer, **{"Prev Alpha": 1.0, "Opacity": opacity})
        check_pixel(f"{mode} over an opaque backdrop at opacity {opacity}", got, mix(CB, B, es) + (1.0,))

        got = sample(group, **layer, **{"Prev Alpha": 0.5, "Opacity": opacity})
        check_pixel(f"{mode} over a half-transparent backdrop at opacity {opacity}",
                    got, over(mix(CS, B, 0.5), es, CB, 0.5))

        got = sample(group, **layer, **{"Prev Alpha": 1.0, "Opacity": opacity, "Clip": 1.0})
        check_pixel(f"{mode} clipped over an opaque backdrop at opacity {opacity}",
                    got, mix(CB, B, es) + (1.0,))

        got = sample(group, **layer, **{"Prev Alpha": 0.5, "Opacity": opacity, "Clip": 1.0})
        check_pixel(f"{mode} clipped over a half-transparent backdrop at opacity {opacity}",
                    got, mix(CB, B, es) + (0.5,))

    got = sample(group, **layer, **{"Prev Alpha": 0.5, "Alpha": 0.5, "Mask": 0.5})
    check_pixel(f"{mode} layer alpha and mask scale the coverage", got, over(mix(CS, B, 0.5), 0.25, CB, 0.5))

    got = sample(group, **layer, **{"Prev Alpha": 0.0, "Clip": 1.0})
    check(abs(got[3]) <= TOL and all(math.isfinite(c) for c in got),
          f"{mode} clipped over a transparent backdrop is transparent without NaN {fmt(got)}")

section("library upgrade")
group = library.layer_blend_group('MULTIPLY')
ids_before = [s.identifier for s in group.interface.items_tree]
nodes_before = sorted(n.get("ps_identifier", "") for n in group.nodes)
library._build_layer_blend(group, 'MULTIPLY')
check([s.identifier for s in group.interface.items_tree] == ids_before,
      "interface identifiers stable on rebuild")
check(sorted(n.get("ps_identifier", "") for n in group.nodes) == nodes_before, "nodes stable on rebuild")

group["ps_lib_version"] = library.LIBRARY_VERSION - 1
group.interface.remove(next(s for s in group.interface.items_tree if s.name == "Clip"))
stale = group.nodes.new('ShaderNodeMath')
stale["ps_identifier"] = "a_mask"
library.layer_blend_group('MULTIPLY')
check(group.get("ps_lib_version") == library.LIBRARY_VERSION, "an older group is rebuilt at the current version")
check("Clip" in [s.name for s in group.interface.items_tree], "rebuilt group gains the Clip input")
check("a_mask" not in [n.get("ps_identifier") for n in group.nodes], "rebuilt group drops nodes it no longer uses")

finish("BLEND TEST")
