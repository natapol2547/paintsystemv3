# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom node headers using ControlRig's draw_label drawing technique.

Adapted from ControlRig by Edward Urena (GeneralNode.draw_color and
blender_draw.Draw/Rect).

Blender draws the header before draw_label(), then the body and controls
afterwards. A rounded backdrop drawn here leaves its color in the header.
Collapsed nodes draw their whole body here, before Blender draws the
outline and controls. Explicit Node.label values bypass this callback.
"""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..common import rounded_rect


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
    # Older Blender versions expose only the location relative to a frame.
    location = getattr(node, 'location_absolute', None)
    if location is None:
        location = node.location.copy()
        parent = node.parent
        while parent is not None:
            location += parent.location
            parent = parent.parent
    padding = 1.5 * scale
    left, top = location.x * scale - padding, location.y * scale + padding
    corner_radius = 5 * scale
    if node.hide:
        # Blender centers the collapsed body on the expanded title's center
        # (NODE_DY / 2 = 10 UI units), rather than on the node's top edge.
        padding = 0.5
        left = round(location.x * scale) - padding
        top = round(location.y * scale) + height / 2 - 10 * scale + padding
        # 4.x uses a capsule; 5.x uses the standard node corner radius.
        corner_radius = height / \
            2 if bpy.app.version < (5, 0, 0) else 4 * scale
        corner_radius += padding
    right, bottom = left + width + 2 * padding, top - height - 2 * padding
    vertices = rounded_rect(left, bottom, right, top, corner_radius)

    # from_builtin returns Blender's own cached shader, so there is nothing to keep here.
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
