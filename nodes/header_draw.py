# SPDX-License-Identifier: GPL-3.0-or-later
"""Coloured node headers, drawn from inside ``draw_label``.

Entry point: ``draw_header(node)``, called from the node's ``draw_label``.
``node_top_left`` places the header, and ``link_tabs`` uses it too.

The technique is adapted from ControlRig by Edward Urena
(``GeneralNode.draw_color`` and ``blender_draw.Draw``/``Rect``).

Blender draws the header before it calls ``draw_label()``, and draws the
body and controls after. So a rounded shape drawn here stays visible
only in the header. For a collapsed node, this draws the whole body,
before Blender draws the outline and controls. A node with an explicit
``Node.label`` skips ``draw_label``, so it gets no custom header.
"""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..common import is_newer_than, node_location, rounded_rect


def node_top_left(node, scale) -> tuple[float, float]:
    """The top-left corner of *node* as the node editor draws it, in view space.

    *scale* is the interface scale, ``preferences.system.ui_scale``.
    """
    location = node_location(node)
    if node.hide:
        # Blender centres a collapsed node on the centre of the expanded
        # title (NODE_DY / 2 = 10 UI units), not on the node's top edge.
        return (round(location.x * scale),
                round(location.y * scale) + node.dimensions[1] / 2 - 10 * scale)
    return location.x * scale, location.y * scale


def draw_header(node):
    context = bpy.context
    if (bpy.app.background or not node.use_custom_color
            or node.header_color is None):
        return
    if (context.area is None or context.area.type != 'NODE_EDITOR'
            or context.region is None or context.region.type != 'WINDOW'
            or context.space_data.edit_tree != node.id_data):
        return
    width, height = node.dimensions
    if width <= 0 or height <= 0:
        return

    scale = context.preferences.system.ui_scale
    padding = 1.5 * scale
    corner_radius = 5 * scale
    if node.hide:
        padding = 0.5
        # Blender 4.x draws a collapsed node as a capsule. 5.x uses the
        # standard node corner radius.
        corner_radius = 4 * scale if is_newer_than(5, 0) else height / 2
        corner_radius += padding
    left, top = node_top_left(node, scale)
    left, top = left - padding, top + padding
    right, bottom = left + width + 2 * padding, top - height - 2 * padding
    vertices = rounded_rect(left, bottom, right, top, corner_radius)

    # ``from_builtin`` returns Blender's own cached shader, so there is
    # nothing to store or free here.
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRI_FAN', {'pos': vertices})
    blend = gpu.state.blend_get()
    try:
        gpu.state.blend_set('ALPHA')
        shader.bind()
        shader.uniform_float('color', (*node.header_color, 1.0))
        batch.draw(shader)
    finally:
        gpu.state.blend_set(blend)
