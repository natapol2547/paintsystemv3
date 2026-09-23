"""Draw the outline of a selection drag as marching ants (PS-093).

The ants are drawn in the region the drag started in. They use the
overlay's own `overlay_shader.ANT_GLSL` and `overlay.ant_style`, so they
crawl in step with the ants of the selection the drag becomes. Each
segment is a quad as wide as the overlay's line. Segments are drawn
clockwise like the overlay's outline, because the dash direction
follows the tangent. The quads overlap at the joints, so they go into a
scratch buffer the size of the region first, which is then blended over
the region once (`draw_ants`).

The overlay's redraw timer runs only while a selection shows, so the
preview owns a window timer. The operator's `modal` calls `tick` on its
`TIMER` events to keep the dashes moving while the mouse stays still.
"""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d

from ..gpu_passes.core import region_offscreen, saved_state
from ..selection import overlay, overlay_shader
from . import shapes

_VERTEX_SOURCE = """
void main()
{
  v_tangent = tangent;
  gl_Position = vec4(pos / params.zw * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAGMENT_SOURCE = overlay_shader.ANT_GLSL + """
void main()
{
  out_color = ant_color(v_tangent, ant_a, ant_b, params.xy);
}
"""

_shader: gpu.types.GPUShader | None = None


def _get_shader() -> gpu.types.GPUShader:
    global _shader
    if _shader is None:
        interface = gpu.types.GPUStageInterfaceInfo("ps_tool_preview_interface")
        interface.flat('VEC2', "v_tangent")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('VEC4', "ant_a")
        info.push_constant('VEC4', "ant_b")
        # Dash phase and dash length, then the target's width and height, in pixels.
        info.push_constant('VEC4', "params")
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_in(1, 'VEC2', "tangent")
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', "out_color")
        info.vertex_source(_VERTEX_SOURCE)
        info.fragment_source(_FRAGMENT_SOURCE)
        _shader = gpu.shader.create_from_info(info)
    return _shader


def line_width(context) -> int:
    """Width of the preview line in pixels. It matches the overlay's ants at the current UI scale."""
    return round(2.0 * overlay.LINE_HALF_WIDTH * context.preferences.system.ui_scale)


def draw_ants(canvas: gpu.types.GPUOffScreen, outline, style, width: int) -> None:
    """Draw the closed *outline* as ants *width* pixels wide over the bound framebuffer.

    *canvas* is a scratch buffer the size of that framebuffer, and *style*
    is what `overlay.ant_style` returns. The outline is in pixels, and the
    current `gpu.matrix` must map pixels, as in a `POST_PIXEL` handler.

    The segment quads overlap at every joint. They are drawn without
    blending into the cleared canvas, and the canvas is blended over the
    framebuffer once. Blended straight onto the framebuffer, a joint would
    take a translucent colour two or three times.
    """
    positions, tangents = shapes.quad_strip(outline, width)
    if not positions:
        return
    shader = _get_shader()
    batch = batch_for_shader(shader, 'TRIS', {"pos": positions, "tangent": tangents})
    ant_a, ant_b, dash = style
    with saved_state():
        with canvas.bind():
            gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0))
            gpu.state.blend_set('NONE')
            shader.bind()
            shader.uniform_float("ant_a", ant_a)
            shader.uniform_float("ant_b", ant_b)
            shader.uniform_float("params", (*dash, float(canvas.width), float(canvas.height)))
            batch.draw(shader)
        gpu.state.blend_set('ALPHA')
        draw_texture_2d(canvas.texture_color, (0.0, 0.0), canvas.width, canvas.height)


class Preview:
    """A drag's outline drawn in the region of *context*, until `remove`."""

    def __init__(self, context):
        self._region = context.region
        self._outline: list[tuple[float, float]] = []
        self._canvas: gpu.types.GPUOffScreen | None = None
        self._window_manager = context.window_manager
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        self._timer = self._window_manager.event_timer_add(overlay.REDRAW_INTERVAL, window=context.window)

    def update(self, points) -> None:
        """Show the closed outline through *points*, in region pixels."""
        self._outline = shapes.clockwise(points)
        self._region.tag_redraw()

    def tick(self) -> None:
        """Redraw so the dashes crawl. Called on the timer's events."""
        self._region.tag_redraw()

    def remove(self) -> None:
        """Stop drawing, stop the timer and free the canvas. Safe to call more than once."""
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
            self._region.tag_redraw()
        if self._timer is not None:
            self._window_manager.event_timer_remove(self._timer)
            self._timer = None
        self._canvas = None

    def _draw(self) -> None:
        context = bpy.context
        region = context.region
        if region is None or region.as_pointer() != self._region.as_pointer() or len(self._outline) < 2:
            return
        self._canvas = region_offscreen(region, self._canvas)
        if self._canvas is not None:
            draw_ants(self._canvas, self._outline, overlay.ant_style(context), line_width(context))


def release() -> None:
    """Free the shader while the GPU context exists. Called on unregister."""
    global _shader
    _shader = None
