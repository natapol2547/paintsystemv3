"""The display a channel preview uses (PS-061).

A preview shows a channel's values as they are stored. The scene's colour
management changes values on their way to the screen, so a preview sets a
neutral display and puts the scene's own back when the last preview ends.
The saved settings live on the scene, so they are saved with the file and
undo covers them.
"""
import logging

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.utils import register_classes_factory

log = logging.getLogger(__name__)

# No look, and exposure and gamma that change nothing.
NEUTRAL_LOOK = 'None'
NEUTRAL_EXPOSURE = 0.0
NEUTRAL_GAMMA = 1.0


def preview_view_transform(channel) -> str:
    """The view transform that shows *channel*'s values as they are stored.

    Standard shows a colour as it was painted. Raw shows data as its value,
    so a roughness of 0.5 is middle grey, as in an image editor. Standard
    would show it brighter.
    """
    return 'Raw' if channel is not None and channel.color_space == 'NONCOLOR' else 'Standard'


def set_enum(owner, prop: str, value: str) -> bool:
    """Set the enum *prop* to *value*, and return False when it is not one of its items.

    A custom OpenColorIO configuration may have no Standard or Raw view,
    and a file may store a view or look this configuration does not have.
    """
    try:
        setattr(owner, prop, value)
    except TypeError:
        log.warning("the colour management has no %s '%s'", prop, value)
        return False
    return True


class PaintSystemPreviewDisplay(bpy.types.PropertyGroup):
    """The scene's display from before a preview, and the view transform the preview set."""

    is_saved: BoolProperty(name="Is Saved", default=False)
    view_transform: StringProperty(name="View Transform")
    look: StringProperty(name="Look")
    exposure: FloatProperty(name="Exposure")
    gamma: FloatProperty(name="Gamma", default=1.0)
    # When the scene has another view transform, the user picked it while
    # previewing, and the preview leaves it alone.
    applied: StringProperty(name="Applied View Transform")

    def show(self, view_settings, channel) -> None:
        """Save the scene's display, unless a preview already did, and show *channel* neutrally."""
        if not self.is_saved:
            self.view_transform = view_settings.view_transform
            self.look = view_settings.look
            self.exposure = view_settings.exposure
            self.gamma = view_settings.gamma
            self.is_saved = True
        self._apply_view_transform(view_settings, channel)
        view_settings.look = NEUTRAL_LOOK
        view_settings.exposure = NEUTRAL_EXPOSURE
        view_settings.gamma = NEUTRAL_GAMMA

    def follow(self, view_settings, channel) -> None:
        """Switch to the view transform for *channel*, unless the user picked another one."""
        if self.is_saved and view_settings.view_transform == self.applied:
            self._apply_view_transform(view_settings, channel)

    def restore(self, view_settings) -> None:
        """Put the saved display back, except the settings the user changed while previewing."""
        if not self.is_saved:
            return
        # Setting the view transform resets the look, so the look comes after it.
        if (view_settings.view_transform == self.applied
                and set_enum(view_settings, 'view_transform', self.view_transform)):
            set_enum(view_settings, 'look', self.look)
        if view_settings.exposure == NEUTRAL_EXPOSURE:
            view_settings.exposure = self.exposure
        if view_settings.gamma == NEUTRAL_GAMMA:
            view_settings.gamma = self.gamma
        self.is_saved = False
        self.applied = ""

    def _apply_view_transform(self, view_settings, channel) -> None:
        set_enum(view_settings, 'view_transform', preview_view_transform(channel))
        # When the switch fails, the view transform on screen is still the
        # one the preview gives back at the end, so it is recorded either way.
        self.applied = view_settings.view_transform


def follow_preview_display(channel) -> None:
    """Let every scene that shows a preview switch to the view transform for *channel*."""
    for scene in bpy.data.scenes:
        scene.paint_system.preview_display.follow(scene.view_settings, channel)


def restore_preview_displays() -> None:
    """Put back the display of every scene that showed a preview."""
    for scene in bpy.data.scenes:
        scene.paint_system.preview_display.restore(scene.view_settings)


classes = (
    PaintSystemPreviewDisplay,
)


register, unregister = register_classes_factory(classes)
