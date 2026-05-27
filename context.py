import bpy

from bpy.props import PointerProperty
from bpy.utils import register_classes_factory


def is_ps_node_tree_poll(self, node_tree: bpy.types.NodeTree):
    return node_tree.bl_idname == 'PaintSystemNodeTree'


class PaintSystem(bpy.types.PropertyGroup):
    image: PointerProperty(type=bpy.types.Image)
    active_node_tree: PointerProperty(
        type=bpy.types.NodeTree,
        name="Active Paint System Tree",
        description="Currently active Paint System node tree",
        poll=is_ps_node_tree_poll,
    )


classes = (
    PaintSystem,
)

_register, _unregister = register_classes_factory(classes)


def register():
    _register()
    bpy.types.Scene.paint_system = PointerProperty(type=PaintSystem)


def unregister():
    del bpy.types.Scene.paint_system
    _unregister()


# import bpy

# # 1. Define the function that fetches the name safely
# def get_active_material_name(self):
#     # 'self' here refers to the context object
#     mat = self.active_object.active_material if self.active_object else None
#     return mat.name if mat else ""


# # 2. Register it to Blender's Context type
# def register():
#     bpy.types.Context.active_material_name = bpy.props.StringProperty(
#         get=get_active_material_name
#     )


# def unregister():
#     del bpy.types.Context.active_material_name


# if __name__ == "__main__":
#     register()
    
#     # How you use it anywhere in your addon:
#     # print(bpy.context.active_material_name)