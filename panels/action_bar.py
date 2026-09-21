# SPDX-License-Identifier: GPL-3.0-or-later
"""A floating row of action buttons inside the 3D view (PS-052).

One `GizmoGroup` in VIEW_3D / WINDOW holds a backdrop gizmo and one
`GIZMO_GT_button_2d` per action, each bound to an operator with
`target_set_operator`. Gizmos are what makes the bar work at all: they
are picked before the tool keymap, so a click on a button beats the
brush and the selection tools without the add-on binding a key, and
`gizmogroup.gizmo_tweak` has no `UNDO` option, so the action it runs owns
the one Ctrl+Z it costs.

`draw_prepare` runs before every draw of the region and lays the row out
in region pixels inside the part of the region no other region covers, so
opening the tool bar, the sidebar or the asset shelf, or resizing the
area, moves the bar on the next draw.

The poll has to check `SpaceView3D.show_gizmo`: gizmos that are not drawn
are still picked, so without it a hidden bar would go on swallowing
clicks over its own rectangle.
"""
import math

import bpy
import gpu
from bpy.types import Gizmo, GizmoGroup, Menu
from bpy.utils import register_class, unregister_class
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from ..common import ADDON_ID, blender_icon, icon_kwargs
from ..context import get_active_tree
from ..selection import session as selection_session

BUTTON_SIZE = 28.0
"""Diameter of one button's hit circle and hover disc, in pixels at a UI scale of 1."""

BUTTON_GAP = 2.0
"""Space between two buttons."""

PADDING = 4.0
"""Space between the buttons and the edge of the backdrop."""

MARGIN = 10.0
"""Space between the backdrop and the edge of the visible part of the view."""

CORNER = 8.0
"""Corner radius of the backdrop."""

COVERING_REGIONS = frozenset((
    'TOOLS', 'UI', 'HEADER', 'TOOL_HEADER', 'ASSET_SHELF', 'ASSET_SHELF_HEADER'))
"""Region types that can be drawn over the 3D view's WINDOW region.

HUD, the Adjust Last Operation panel, is left out: it floats over a
corner, can be collapsed, and draws above the bar where they overlap.
"""

def preferences(context):
    """The add-on's preferences, or None when it has no entry (a test, a reload)."""
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon is not None else None


def show_bar(context) -> bool:
    """Whether the bar belongs in this view now.

    Texture Paint, a live selection on the active tree, and the
    preference on. The bar is the selection's own toolbar, so without a
    selection there is nothing for it to act on that the sidebar does not
    already offer.
    """
    prefs = preferences(context)
    if prefs is not None and not prefs.show_action_bar:
        return False
    if context.mode != 'PAINT_TEXTURE':
        return False
    tree = get_active_tree(context)
    if tree is None:
        return False
    state = selection_session.current()
    return state.selected and state.tree_uid == tree.session_uid


def is_shown(region) -> bool:
    """Whether *region* takes up space; a hidden one reports a width or height of 1."""
    return region.width > 1 and region.height > 1


def _bounds(region) -> tuple[int, int, int, int]:
    """Window-space (xmin, ymin, xmax, ymax) of *region*, with xmax and ymax exclusive."""
    return region.x, region.y, region.x + region.width, region.y + region.height


def visible_rect(area, region) -> tuple[int, int, int, int]:
    """Region-local rect of the part of *region* no covering region hides.

    With Region Overlap on, the tool bar, the sidebar, the headers and
    the asset shelf are drawn over the WINDOW region; with it off they
    sit beside it and never intersect it. Each shown region that does
    intersect trims the side it is aligned to, so a tool bar flipped to
    the right trims the right side.
    """
    x0, y0, x1, y1 = _bounds(region)
    for other in area.regions:
        if other.type not in COVERING_REGIONS or not is_shown(other):
            continue
        ox0, oy0, ox1, oy1 = _bounds(other)
        if ox1 <= x0 or ox0 >= x1 or oy1 <= y0 or oy0 >= y1:
            continue
        alignment = other.alignment
        if alignment == 'LEFT':
            x0 = max(x0, ox1)
        elif alignment == 'RIGHT':
            x1 = min(x1, ox0)
        elif alignment == 'BOTTOM':
            y0 = max(y0, oy1)
        elif alignment == 'TOP':
            y1 = min(y1, oy0)
    return x0 - region.x, y0 - region.y, x1 - region.x, y1 - region.y


def bar_layout(visible, count: int, scale: float) -> dict:
    """The backdrop rect and the button centres, centred at the bottom of *visible*.

    A view narrower than the bar gets the bar from its left edge running
    past the right one, rather than a row of buttons too small to hit.
    """
    vx0, vy0, vx1, vy1 = visible
    size, gap = BUTTON_SIZE * scale, BUTTON_GAP * scale
    pad, margin = PADDING * scale, MARGIN * scale
    width = count * size + max(count - 1, 0) * gap + 2 * pad
    height = size + 2 * pad
    x0 = round((vx0 + vx1 - width) / 2)
    if vx1 - vx0 < width + 2 * margin:
        x0 = max(x0, round(vx0 + margin))
    y0 = round(vy0 + margin)
    centers = [(x0 + pad + size / 2 + index * (size + gap), y0 + pad + size / 2)
               for index in range(count)]
    return {"visible": visible, "rect": (x0, y0, x0 + width, y0 + height),
            "centers": centers, "radius": size / 2, "scale": scale}


def _rounded_rect(x0, y0, x1, y1, radius, segments=6):
    """Outline of a rounded rectangle, counter-clockwise from the right edge."""
    radius = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    corners = ((x1 - radius, y1 - radius, 0.0), (x0 + radius, y1 - radius, 0.5),
               (x0 + radius, y0 + radius, 1.0), (x1 - radius, y0 + radius, 1.5))
    points = []
    for cx, cy, start in corners:
        for step in range(segments + 1):
            angle = math.pi * (start + 0.5 * step / segments)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


# Label and (red, green, blue, alpha) for each entry of the Invert menu.
INVERT_CHOICES = (
    ("Colors", (True, True, True, False)),
    ("Colors and Alpha", (True, True, True, True)),
    None,
    ("Red", (True, False, False, False)),
    # Inverting green switches a normal map between OpenGL and DirectX.
    ("Green", (False, True, False, False)),
    ("Blue", (False, False, True, False)),
    ("Alpha", (False, False, False, True)),
)


class PAINTSYSTEM_MT_invert_channels(Menu):
    """Invert the colours, or only some channels, of the active layer."""

    bl_idname = "PAINTSYSTEM_MT_invert_channels"
    bl_label = "Invert Channels"

    def draw(self, context):
        layout = self.layout
        for choice in INVERT_CHOICES:
            if choice is None:
                layout.separator()
                continue
            label, (red, green, blue, alpha) = choice
            op = layout.operator("paint_system.invert_pixels", text=label)
            op.invert_r, op.invert_g, op.invert_b, op.invert_a = red, green, blue, alpha


class PAINTSYSTEM_MT_action_bar(Menu):
    """What does not fit on the bar, and the way to put the bar away."""

    bl_idname = "PAINTSYSTEM_MT_action_bar"
    bl_label = "Selection Actions"

    def draw(self, context):
        layout = self.layout
        layout.operator("paint_system.select_all", text="Select All",
                        **icon_kwargs('SELECT_SET')).action = 'SELECT'
        layout.menu(PAINTSYSTEM_MT_invert_channels.bl_idname, **icon_kwargs('MOD_MASK'))
        # Not buttons on the bar: both ask for a radius first, and a
        # gizmo that opens a dialog is not the one-click thing the bar
        # is for. Placeholder icons.
        layout.separator()
        layout.operator("paint_system.blur_pixels", **icon_kwargs('MOD_SMOOTH'))
        layout.operator("paint_system.sharpen_pixels", **icon_kwargs('MOD_EDGESPLIT'))
        prefs = preferences(context)
        if prefs is None:
            return
        layout.separator()
        layout.prop(prefs, "show_action_bar", text="Hide Action Bar",
                    invert_checkbox=True, **icon_kwargs('HIDE_ON'))


class PAINTSYSTEM_GT_action_backdrop(Gizmo):
    """A rounded rectangle under the buttons, which also swallows clicks between them."""

    bl_idname = "PAINTSYSTEM_GT_action_backdrop"
    __slots__ = ("rect", "corner")

    def setup(self):
        self.rect = (0, 0, 0, 0)
        self.corner = CORNER
        self.use_tooltip = False

    def draw(self, context):
        x0, y0, x1, y1 = self.rect
        if x1 <= x0 or y1 <= y0:
            return
        outline = _rounded_rect(x0, y0, x1, y1, self.corner)
        fan = [((x0 + x1) / 2, (y0 + y1) / 2)] + outline + [outline[0]]
        indices = [(0, index, index + 1) for index in range(1, len(fan) - 1)]
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        blend = gpu.state.blend_get()
        gpu.state.blend_set('ALPHA')
        try:
            shader.bind()
            shader.uniform_float("color", (*self.color, self.alpha))
            batch_for_shader(shader, 'TRIS', {"pos": fan}, indices=indices).draw(shader)
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.12))
            batch_for_shader(shader, 'LINE_STRIP',
                             {"pos": outline + [outline[0]]}).draw(shader)
        finally:
            gpu.state.blend_set(blend)

    def test_select(self, context, location):
        x0, y0, x1, y1 = self.rect
        return 0 if x0 <= location[0] < x1 and y0 <= location[1] < y1 else -1


class PAINTSYSTEM_GGT_action_bar(GizmoGroup):
    bl_idname = "PAINTSYSTEM_GGT_action_bar"
    bl_label = "Paint System Action Bar"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    # SCALE: the sizes below are pixels times the UI scale, not world
    # units. PERSISTENT: the group survives a tool change, so the bar
    # does not blink away when the user picks a selection tool.
    bl_options = {'PERSISTENT', 'SCALE'}

    @classmethod
    def poll(cls, context):
        space = context.space_data
        if space is None or space.type != 'VIEW_3D' or not space.show_gizmo:
            return False
        return show_bar(context)

    def button(self, operator: str, **properties):
        """A button gizmo running *operator*, added to the row. The caller sets its icon.

        The icons are literal `blender_icon` calls at the call sites
        below rather than a table here, because `GIZMO_GT_button_2d`
        takes an icon name and grew `icon_value` only in 4.5: a gizmo
        cannot show an add-on icon on every version the add-on supports,
        so these are Blender's own and `tests/test_icons.py` checks each
        one where it is written.
        """
        item = self._theme
        gizmo = self.gizmos.new("GIZMO_GT_button_2d")
        gizmo.draw_options = {'BACKDROP'}
        gizmo.color = item.inner_sel[:3]
        gizmo.alpha = 0.0
        gizmo.color_highlight = item.inner_sel[:3]
        gizmo.alpha_highlight = 0.6
        gizmo.scale_basis = BUTTON_SIZE / 2
        target = gizmo.target_set_operator(operator)
        for name, value in properties.items():
            setattr(target, name, value)
        self.buttons.append((operator, gizmo))
        return gizmo

    def setup(self, context):
        self._theme = context.preferences.themes[0].user_interface.wcol_toolbar_item
        self.buttons = []
        # Placeholder icons until the add-on has drawn its own.
        self.button("paint_system.clear_pixels").icon = blender_icon('IMAGE_ALPHA')
        self.button("paint_system.fill_pixels").icon = blender_icon('SNAP_FACE')
        self.button("paint_system.invert_pixels").icon = blender_icon('MOD_MASK')
        invert = self.button("paint_system.select_all", action='INVERT')
        invert.icon = blender_icon('SELECT_DIFFERENCE')
        self.button("paint_system.select_all", action='DESELECT').icon = blender_icon('X')
        more = self.button("wm.call_menu", name=PAINTSYSTEM_MT_action_bar.bl_idname)
        more.icon = blender_icon('THREE_DOTS', 'COLLAPSEMENU')
        # Created last: Blender draws a group's gizmos last to first and
        # picks them first to last, so the backdrop draws under the
        # buttons and is only picked where no button is.
        backdrop = self.gizmos.new(PAINTSYSTEM_GT_action_backdrop.bl_idname)
        backdrop.color = self._theme.inner[:3]
        backdrop.alpha = max(self._theme.inner[3], 0.85)
        backdrop.color_highlight = backdrop.color
        backdrop.alpha_highlight = backdrop.alpha
        self.backdrop = backdrop

    def shown_buttons(self, context):
        """The buttons whose operator can run now.

        A gizmo cannot be greyed out, and one that runs nothing looks
        broken, so a button whose operator polls False is hidden and the
        row closes up. The operator's `poll` answers from flags alone, so
        this costs no read (`ops.pixel_ops`).
        """
        shown = []
        for operator, gizmo in self.buttons:
            group, name = operator.split(".", 1)
            can_run = getattr(getattr(bpy.ops, group), name).poll()
            gizmo.hide = not can_run
            if can_run:
                shown.append(gizmo)
        return shown

    def draw_prepare(self, context):
        shown = self.shown_buttons(context)
        layout = bar_layout(visible_rect(context.area, context.region), len(shown),
                            context.preferences.system.ui_scale)
        for gizmo, (x, y) in zip(shown, layout["centers"]):
            gizmo.matrix_basis = Matrix.Translation((x, y, 0.0))
        self.backdrop.rect = layout["rect"]
        self.backdrop.corner = CORNER * layout["scale"]


def draw_gizmo_popover(self, context):
    """The bar's switch in the viewport's own Gizmos popover, where widgets live."""
    prefs = preferences(context)
    if prefs is None:
        return
    layout = self.layout
    layout.separator()
    layout.label(text="Paint System")
    layout.prop(prefs, "show_action_bar")


classes = (
    PAINTSYSTEM_MT_invert_channels,
    PAINTSYSTEM_MT_action_bar,
    PAINTSYSTEM_GT_action_backdrop,
    PAINTSYSTEM_GGT_action_bar,
)


def register():
    for cls in classes:
        register_class(cls)
    bpy.types.VIEW3D_PT_gizmo_display.append(draw_gizmo_popover)


def unregister():
    bpy.types.VIEW3D_PT_gizmo_display.remove(draw_gizmo_popover)
    for cls in reversed(classes):
        unregister_class(cls)
