"""Layer types offered for adding, in menu order.

The Add Layer menu, the ``paint_system.add_layer`` type enum and the node
editor's Layers category all read this list, so they cannot drift apart.
"""
from .folder_layer_node import PaintSystemFolderLayerNode
from .image_layer_node import PaintSystemImageLayerNode
from .solid_color_layer_node import PaintSystemSolidColorLayerNode


def layer_types() -> list[type]:
    return [
        PaintSystemFolderLayerNode,
        PaintSystemSolidColorLayerNode,
        PaintSystemImageLayerNode,
    ]


def layer_type(ps_type: str) -> type | None:
    return next((cls for cls in layer_types() if cls.ps_type == ps_type), None)


def layer_type_items() -> list[tuple[str, str, str]]:
    """``EnumProperty`` items for choosing a layer type."""
    return [(cls.ps_type, cls.ps_label, cls.ps_description) for cls in layer_types()]
