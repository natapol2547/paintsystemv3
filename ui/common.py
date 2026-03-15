import bpy

from ..common import get_icon


class PaintSystemPanel(bpy.types.Panel):
    def get_icon(self, custom_icon_name: str) -> int:
        return get_icon(custom_icon_name)
