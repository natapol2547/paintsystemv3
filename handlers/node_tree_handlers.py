import bpy


@bpy.app.handlers.persistent
def initialize_ps_node_tree(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph = None):
    """Initialize Paint System node tree"""
    for node_tree in bpy.data.node_groups:
        if node_tree.bl_idname == 'PaintSystemNodeTree':
            if not hasattr(node_tree, 'is_new_status') or len(node_tree.nodes) != 0 or len(node_tree.channels) != 0:
                continue
            node_tree.init(bpy.context)
            node_tree.is_new_status = False


def on_addon_enable():
    print("on_addon_enable")


def register():
    bpy.app.timers.register(on_addon_enable)
    bpy.app.handlers.depsgraph_update_post.append(initialize_ps_node_tree)


def unregister():
    bpy.app.handlers.depsgraph_update_post.remove(initialize_ps_node_tree)
