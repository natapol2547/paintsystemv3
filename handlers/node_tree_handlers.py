import bpy


seen_node_trees = set()
on_addon_enable_called = False


@bpy.app.handlers.persistent
def load_post(scene):
    for node_tree in bpy.data.node_groups:
        if node_tree.bl_idname == 'PaintSystemNodeTree':
            seen_node_trees.add((node_tree.uuid, node_tree.session_uid))


@bpy.app.handlers.persistent
def initialize_ps_node_tree(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph = None):
    """Initialize Paint System node tree"""
    global on_addon_enable_called
    if not on_addon_enable_called:
        return
    for node_tree in bpy.data.node_groups:
        if node_tree.bl_idname == 'PaintSystemNodeTree':
            if not hasattr(node_tree, 'is_new_status') or (node_tree.uuid, node_tree.session_uid) in seen_node_trees:
                continue
            if node_tree.session_uid not in seen_node_trees:
                node_tree.init(bpy.context)
                node_tree.is_new_status = False
            seen_node_trees.add((node_tree.uuid, node_tree.session_uid))


def on_addon_enable():
    global on_addon_enable_called
    if on_addon_enable_called:
        return
    on_addon_enable_called = True
    load_post(bpy.context.scene)


def register():
    bpy.app.handlers.load_post.append(load_post)
    bpy.app.timers.register(on_addon_enable)
    bpy.app.handlers.depsgraph_update_post.append(initialize_ps_node_tree)


def unregister():
    bpy.app.handlers.depsgraph_update_post.remove(initialize_ps_node_tree)
    bpy.app.handlers.load_post.remove(load_post)
