"""Selection tools in the 3D view: Rectangle, Ellipse and Lasso Selection (PS-093).

`shapes` holds the outline geometry, `preview` draws a drag's outline,
`select_ops` holds the modal operators that append `VIEW` ops, and
`workspace_tools` puts them in the Texture Paint toolbar.

A workspace keeps the id of its active tool per mode after the tool is
unregistered, and Blender does not reset it: the dead tool stays active,
with no keymap, so drags stop painting until the user picks another
tool. `unregister` therefore sets the brush back first, in live windows
through `wm.tool_set_by_id` and in every workspace by writing the
Texture Paint tool id.

A written id takes effect when Texture Paint is next entered in that
workspace. A workspace no window shows that is still in Texture Paint
is not entered again when it is shown, so it shows the brush without
its keymap until the user picks a tool or re-enters Texture Paint:
setting up a tool in a workspace no window shows crashes Blender.
"""
import logging

import bpy

from . import preview, select_ops, workspace_tools

log = logging.getLogger(__name__)

TOOL_PREFIX = "paint_system."


def default_brush_tool() -> str:
    """Id of the Texture Paint brush tool, the one the toolbar starts with."""
    return 'builtin.brush' if bpy.app.version >= (4, 3, 0) else 'builtin_brush.Draw'


def _ours(ref) -> bool:
    return ref is not None and ref.idname.startswith(TOOL_PREFIX)


def _reset_windows(context, brush: str) -> None:
    """Set the brush in each window whose object is in Texture Paint with one of these tools."""
    window_manager = getattr(context, 'window_manager', None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        try:
            workspace = window.workspace
            obj = window.view_layer.objects.active
            if workspace is None or obj is None or obj.mode != 'TEXTURE_PAINT':
                continue
            if not _ours(workspace.tools.from_space_view3d_mode('PAINT_TEXTURE', create=False)):
                continue
            # Every window through its own 3D view, also while the context
            # window is the Preferences window the add-on is disabled from.
            # Only passing a temporary screen itself raises, and a window
            # with a 3D view never shows one.
            area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
            if area is None:
                continue
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.tool_set_by_id(name=brush)
        except Exception:
            log.debug("Could not reset the selection tool in a window", exc_info=True)


def reset_active_tools(context=None) -> None:
    """Make the brush the Texture Paint tool wherever one of these tools is; before unregistering them."""
    context = context or bpy.context
    brush = default_brush_tool()
    _reset_windows(context, brush)
    # Windows in another mode, and workspaces no window shows, keep the id
    # until Texture Paint is entered there, so write it directly.
    for workspace in getattr(bpy.data, 'workspaces', ()):
        try:
            ref = workspace.tools.from_space_view3d_mode('PAINT_TEXTURE', create=False)
            if _ours(ref):
                ref.idname = brush
        except Exception:
            log.debug("Could not reset the selection tool of workspace %s", workspace.name, exc_info=True)


def register() -> None:
    select_ops.register()
    workspace_tools.register()


def unregister() -> None:
    reset_active_tools()
    workspace_tools.unregister()
    preview.release()
    select_ops.unregister()
