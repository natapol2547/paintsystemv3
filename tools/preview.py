"""Draw the outline of a selection drag as marching ants (PS-093).

The ants are drawn in the region the drag started in. They use the
overlay's own `overlay_shader.ANT_GLSL` and `overlay.ant_style`, so they
crawl in step with the ants of the selection the drag becomes. Each
segment is a quad as wide as the overlay's line. Segments are drawn
clockwise like the overlay's outline, because the dash direction
follows the tangent.

The overlay's redraw timer runs only while a selection shows, so the
preview owns a window timer. The operator's `modal` calls `tick` on its
`TIMER` events to keep the dashes moving while the mouse stays still.
"""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..gpu_passes.core import saved_state
from ..selection import overlay, overlay_shader
from . import shapes

_VERTEX_SOURCE = """
void main()
{
  v_tangent = tangent;
  gl_Position = vec4(pos / region.xy * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAGMENT_SOURCE = overlay_shader.ANT_GLSL + """
void main()
{
  out_color = vec4(ant_color(v_tangent, ant_a, ant_b), 1.0);
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
        info.push_constant('VEC4', "region")
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


class Preview:
    """A drag's outline drawn in the region of *context*, until `remove`."""

    def __init__(self, context):
        self._region = context.region
        self._outline: list[tuple[float, float]] = []
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
        """Stop drawing and stop the timer. Safe to call more than once."""
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
            self._region.tag_redraw()
        if self._timer is not None:
            self._window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _draw(self) -> None:
        context = bpy.context
        region = context.region
        if region is None or region.as_pointer() != self._region.as_pointer() or len(self._outline) < 2:
            return
        positions, tangents = shapes.quad_strip(self._outline, line_width(context))
        if not positions:
            return
        shader = _get_shader()
        batch = batch_for_shader(shader, 'TRIS', {"pos": positions, "tangent": tangents})
        ant_a, ant_b = overlay.ant_style(context)
        shader.bind()
        shader.uniform_float("ant_a", ant_a)
        shader.uniform_float("ant_b", ant_b)
        shader.uniform_float("region", (float(region.width), float(region.height), 0.0, 0.0))
        with saved_state():
            gpu.state.blend_set('NONE')
            batch.draw(shader)


def release() -> None:
    """Free the shader while the GPU context exists. Called on unregister."""
    global _shader
    _shader = None
