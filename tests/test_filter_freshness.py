"""A filter layer notices when its pixels stop describing the stack (PS-057).

`filters.freshness` is compared against the stamp on every compile, and
`PaintSystemFilterLayerNode.emit_source` parks the answer on the node for
the panel to read. What matters is not only that a difference is noticed
but that the right one is named: "Out of date" with no reason is a button
the user has to press on faith.

Part of the claim is what must *not* set it off. Amount, `enabled` and
Mask are all outside the hash by construction, because fading a built
filter has to be free -- that is the whole reason the filter is a layer
rather than an action. Clip looks like one of those and is not: a
clipped layer's ``Color`` input carries its base's own content rather
than the stack, so the flag decides what the layer filters.

The last sections cover the pixel half, which exists because the
structural one is blind to a stroke: `compiler.ir` reduces an image to
its name, so painting into a layer below moves no hash at all.

No GPU here: the image is stamped by hand with what a build would have
stamped it with, which is what the comparison reads.
"""
import os
import sys
import traceback
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (check, finish, import_from, register_addon,  # noqa: E402
                     section)

register_addon()
core = import_from("compiler.core")
derived = import_from("filters.derived")
freshness = import_from("filters.freshness")
pixels = import_from("undo.pixels")
stack_ops = import_from("nodetree.stack_ops")
create_managed_image = import_from("compiler.bake").create_managed_image

SOLID = 'PaintSystemSolidColorLayerNode'
IMAGE = 'PaintSystemImageLayerNode'
FILTER = 'PaintSystemFilterLayerNode'


def below_of(node):
    link = stack_ops.feeding_link(node.inputs['Color'])
    return link.from_node if link is not None else None


def current_stamp(tree, node):
    """What a build of *node* right now would stamp its image with."""
    ctx = core.build_ir(tree).ctx
    return freshness.stamp(freshness.fingerprint_parts(ctx, node, below_of(node)))


def restamp(tree, node):
    """Pretend the layer was just rebuilt, without running a build."""
    node.derived_image[derived.FINGERPRINT_KEY] = current_stamp(tree, node)
    reason(tree, node)


def reason(tree, node):
    """The layer's own answer, after a compile.

    Marked dirty by hand: writing an ID property on the image tags
    nothing, which is why `layer_build.commit` calls `mark_dirty` for
    itself once the stamps are on.
    """
    core.mark_dirty(tree)
    core.flush_now()
    return node.derived_stale_reason


try:
    section("a layer that is up to date")
    tree = bpy.data.node_groups.new("Fresh", 'PaintSystemNodeTree')
    tree.initialize()
    with core.suspend_compile(tree):
        bottom = tree.insert_layer_node(SOLID)
        bottom.fill_color = (0.2, 0.6, 0.4, 1.0)
        node = tree.insert_layer_node(FILTER)
    core.flush_now()
    check(reason(tree, node) == "", "an unbuilt layer has nothing to be out of date about")

    result = create_managed_image("Fresh Result", 64, 64)
    result.pixels.foreach_set([0.5, 0.5, 0.5, 1.0] * 64 * 64)
    result.update()
    result[derived.BUILD_KEY] = "hand-stamped"
    result[derived.UV_MAP_KEY] = ""
    node.derived_image = result
    restamp(tree, node)
    check(reason(tree, node) == "", "and a freshly stamped one reads as up to date")

    section("what makes it out of date")
    bottom.fill_color = (0.9, 0.1, 0.1, 1.0)
    check(reason(tree, node) == "the layers below changed",
          f"a fill colour under it: {reason(tree, node)!r}")
    restamp(tree, node)

    with core.suspend_compile(tree):
        extra = tree.insert_layer_node(SOLID, target=bottom)
    check(reason(tree, node) == "the layers below changed",
          f"a layer added under it: {reason(tree, node)!r}")
    stack_ops.detach(tree, extra)
    restamp(tree, node)

    node.invert_alpha = True
    check(reason(tree, node) == "the filter settings changed",
          f"a filter parameter: {reason(tree, node)!r}")
    node.invert_alpha = False
    restamp(tree, node)

    # Switching the kind changes its settings as well; the reason names
    # the switch.
    node.filter_type = 'PAINTERLY'
    check(reason(tree, node) == "the filter changed",
          f"another kind of filter: {reason(tree, node)!r}")
    restamp(tree, node)
    # The painter has no passes to read its settings from, so it says
    # what its pixels depend on for itself.
    for name, value in (("painter_seed", 7), ("painter_brush", 'CIRCLE'),
                        ("painter_coverage", 30.0), ("painter_hue", 0.2)):
        previous = getattr(node, name)
        setattr(node, name, value)
        check(reason(tree, node) == "the filter settings changed",
              f"a Painterly setting, {name}: {reason(tree, node)!r}")
        setattr(node, name, previous)
        check(reason(tree, node) == "", f"and putting {name} back settles it")
    # Its Smoothing scales with the resolution, so its settings change
    # with it; the reason is still the one the user changed.
    node.resolution = '4096'
    check(reason(tree, node) == "the resolution changed",
          f"Painterly's resolution: {reason(tree, node)!r}")
    node.resolution = '2048'
    node.filter_type = 'INVERT'
    restamp(tree, node)

    node.resolution = '4096'
    check(reason(tree, node) == "the resolution changed",
          f"the resolution: {reason(tree, node)!r}")
    node.resolution = '2048'
    restamp(tree, node)

    node.uv_map = "SomeMap"
    check(reason(tree, node) == "the UV map changed",
          f"the UV map: {reason(tree, node)!r}")
    node.uv_map = ""
    restamp(tree, node)

    section("clipping, which decides what it filters")
    # A clipped layer's Color input is its base's own content instead of
    # the stack below, because the base holds its blend for the top of
    # the run to make. The node feeding the socket is the same node
    # either way, so `subtree_hash` cannot tell the two apart.
    node.is_clip = True
    check(reason(tree, node) == "clipping changed what it filters",
          f"clipping it: {reason(tree, node)!r}")
    restamp(tree, node)
    check(reason(tree, node) == "", "and a rebuild settles it there")
    node.is_clip = False
    check(reason(tree, node) == "clipping changed what it filters",
          f"unclipping it again: {reason(tree, node)!r}")
    restamp(tree, node)

    # The stamp records the base, not the flag: a clipped layer with no
    # unclipped layer under it composites as if it were not clipped, and
    # filters the same stack it did before.
    bottom.is_clip = True
    restamp(tree, node)
    node.is_clip = True
    check(reason(tree, node) == "",
          f"clipping to nothing filters the same stack: {reason(tree, node)!r}")
    node.is_clip = False
    bottom.is_clip = False
    restamp(tree, node)

    # The part is left out rather than stamped False, so a stamp written
    # before it existed still matches for the unclipped layer it was
    # already right about, and only a clipped one asks to be rebuilt.
    parts = freshness.fingerprint_parts(core.build_ir(tree).ctx, node, below_of(node))
    check("clip" not in parts, "an unclipped layer stamps what it always did")

    section("what does not")
    # These are outside the hash by construction: they change how the
    # built pixels are composited, never what they should contain.
    for name, value in (("opacity", 0.35), ("enabled", False)):
        setattr(node, name, value)
        check(reason(tree, node) == "", f"{name} leaves it alone")
    node.opacity, node.enabled = 1.0, True
    node.inputs['Mask'].default_value = 0.5
    check(reason(tree, node) == "", "and so does the Mask input")
    node.inputs['Mask'].default_value = 1.0
    core.flush_now()

    section("a stamp it cannot use")
    result[derived.FINGERPRINT_KEY] = "not json {"
    check(reason(tree, node) == "its build could not be read",
          f"unreadable: {reason(tree, node)!r}")

    del result[derived.FINGERPRINT_KEY]
    check(reason(tree, node) == "it was not built from this stack",
          f"missing altogether: {reason(tree, node)!r}")

    restamp(tree, node)
    parts = freshness.fingerprint_parts(core.build_ir(tree).ctx, node, below_of(node))
    parts["version"] = derived.FILTER_VERSION + 1
    result[derived.FINGERPRINT_KEY] = freshness.stamp(parts)
    check(reason(tree, node) == "Paint System was updated",
          f"stamped by another version of the addon: {reason(tree, node)!r}")

    section("losing the image")
    node.derived_image = None
    check(reason(tree, node) == "", "clears the reason rather than leaving the last one up")

    section("painting below it")
    # The other half. `subtree_hash` reduces an image to its name, so a
    # stroke under a filter layer moves nothing the structural check can
    # see; `note_image_changed` is what closes it.
    node.derived_image = result
    restamp(tree, node)
    with core.suspend_compile(tree):
        picture = tree.insert_layer_node(IMAGE, target=bottom)
        picture.image = create_managed_image("Fresh Source", 8, 8)
    restamp(tree, node)
    check(reason(tree, node) == "", "an image layer added below, then rebuilt, is up to date")

    elsewhere = create_managed_image("Fresh Elsewhere", 8, 8)
    freshness.note_image_changed([elsewhere.session_uid])
    check(node.stale_reason == "", "an image the stack does not read leaves it alone")

    pixels.write_pixels(picture.image, [1.0, 0.0, 0.0, 1.0] * 64)
    check(node.derived_stale_pixels, "writing pixels below it marks the layer")
    check(node.stale_reason == "the pixels below changed",
          f"and names that as the reason: {node.stale_reason!r}")

    # Structural first: it says which setting to look at, where the pixel
    # half can only say that something was painted.
    node.resolution = '4096'
    check(node.stale_reason == "the resolution changed",
          f"a structural change is the more useful answer: {node.stale_reason!r}")
    node.resolution = '2048'
    restamp(tree, node)
    check(node.stale_reason == "the pixels below changed", "and the pixel half is still there")

    section("what the panel says while a refresh is held back")
    # A layer that is switched off or locked is not a candidate for the
    # automatic path, so with Auto Refresh on and nothing happening the
    # badge alone reads as broken. Checked here rather than in
    # `test_ui_draw.py`, which needs a window: a recorded layout is
    # enough to read the branch, and the branch is the part worth
    # pinning.
    # Out of date through the pixel half, so that the compile any of
    # these toggles schedules cannot quietly clear it again.
    node.derived_image = result
    restamp(tree, node)
    node.auto_refresh = True
    node.derived_stale_pixels = True
    check(node.stale_reason == freshness.PIXEL_REASON,
          f"the layer is out of date to begin with: {node.stale_reason!r}")

    def held_back_message():
        """The labels `draw_result_settings` puts in its box, as one list."""
        labels = []
        pane = SimpleNamespace(enabled=True)
        pane.box = pane.row = lambda *args, **kwargs: pane
        pane.label = lambda **kwargs: labels.append(kwargs.get('text', ""))
        pane.operator = lambda *args, **kwargs: SimpleNamespace()
        pane.prop = lambda *args, **kwargs: None
        node.draw_result_settings(bpy.context, pane)
        return [text for text in labels if text.startswith("Waiting until")]

    node.enabled, node.lock_layer = True, False
    check(held_back_message() == [], "a layer that can refresh is told nothing")
    node.enabled = False
    check(held_back_message() == ["Waiting until the layer is switched on"],
          f"switched off: {held_back_message()}")
    node.lock_layer = True
    check(held_back_message() == ["Waiting until the layer is switched on"],
          "off and locked names the switch first, because it is the outer one")
    node.enabled = True
    check(held_back_message() == ["Waiting until the layer is unlocked"],
          f"locked alone names the lock, rather than sending the user to a "
          f"switch that is already on: {held_back_message()}")
    node.lock_layer = False

    section("an unbuilt layer")
    node.derived_stale_pixels = False
    node.derived_image = None
    pixels.write_pixels(picture.image, [0.0, 1.0, 0.0, 1.0] * 64)
    check(not node.derived_stale_pixels,
          "has no claim about pixels to lose, so nothing marks it")

except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("FILTER FRESHNESS TEST")
