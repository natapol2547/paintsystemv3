"""Tabs that mark linked layers in the node editor (PS-016).

A linked layer changes when another node changes, so the node editor
marks it. A small tab in the node's header colour stands on its top
edge, a little in from the left, with a chain icon inside.

Entry point: ``draw_tabs(tree, scale)``. A node editor draw handler
calls it for the tree the editor shows, after the nodes are drawn.

The icon is ``icons/link_tab.png``. The ``gpu`` module cannot draw
Blender's own icons, so the add-on ships its own.
"""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .header_draw import node_top_left
from .layers.links import linked_names
from ..common import icon_preview, rounded_rect
from ..context import node_editor_tree

ICON_NAME = 'link_tab'
# Sizes in UI units. The tab reaches OVERLAP units down into the header,
# so the two read as one shape.
TAB_WIDTH = 24
TAB_HEIGHT = 15
TAB_INSET = 10
TAB_RADIUS = 5
OVERLAP = 1
ICON_SIZE = 12

# The draw handler, while the add-on is registered.
_handle = None
# The icon's texture. It is made at the first draw, which has a GPU
# context, and dropped by ``release``.
_texture = None


def _icon_texture() -> gpu.types.GPUTexture:
    global _texture
    if _texture is None:
        preview = icon_preview(ICON_NAME)
        width, height = preview.image_size
        # A preview's pixels run bottom row first, as a texture's do, and
        # their colour is premultiplied by their alpha.
        pixels = gpu.types.Buffer('FLOAT', width * height * 4, preview.image_pixels_float[:])
        _texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=pixels)
    return _texture


def draw_tabs(tree, scale: float) -> None:
    """Draw the tab of every linked layer in *tree*, in view space at interface scale *scale*."""
    names = linked_names(tree)
    if not names:
        return
    # ``from_builtin`` returns Blender's own cached shaders, so there is
    # nothing to store or free here.
    fill = gpu.shader.from_builtin('UNIFORM_COLOR')
    image = gpu.shader.from_builtin('IMAGE')
    texture = _icon_texture()
    blend = gpu.state.blend_get()
    try:
        for name in names:
            node = tree.nodes[name]
            left, top = node_top_left(node, scale)
            x0 = left + TAB_INSET * scale
            x1 = x0 + TAB_WIDTH * scale
            y1 = top + TAB_HEIGHT * scale
            shape = rounded_rect(x0, top - OVERLAP * scale, x1, y1, TAB_RADIUS * scale, square_bottom=True)
            gpu.state.blend_set('ALPHA')
            fill.bind()
            fill.uniform_float('color', (*node.header_color, 1.0))
            batch_for_shader(fill, 'TRI_FAN', {'pos': shape}).draw(fill)

            cx, cy, half = (x0 + x1) / 2, (top + y1) / 2, ICON_SIZE * scale / 2
            quad = ((cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half))
            # Straight 'ALPHA' would multiply the icon's colour by its alpha
            # a second time, and darken its soft edges.
            gpu.state.blend_set('ALPHA_PREMULT')
            image.bind()
            image.uniform_sampler('image', texture)
            batch_for_shader(image, 'TRI_FAN', {'pos': quad, 'texCoord': ((0, 0), (1, 0), (1, 1), (0, 1))}).draw(image)
    finally:
        gpu.state.blend_set(blend)


def _on_node_editor_draw():
    context = bpy.context
    tree = node_editor_tree(context)
    if tree is not None:
        draw_tabs(tree, context.preferences.system.ui_scale)


def register():
    global _handle
    _handle = bpy.types.SpaceNodeEditor.draw_handler_add(_on_node_editor_draw, (), 'WINDOW', 'POST_VIEW')


def release() -> None:
    """Drop the icon's texture while the GPU context still exists. The next draw makes a new one."""
    global _texture
    _texture = None


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    release()
