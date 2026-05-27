import uuid

from bpy.props import StringProperty

from ..common import transform_unique_name


def _set_name_transform(self, new_value, curr_value, is_set):
    return transform_unique_name(self, new_value, curr_value, is_set, 'nodes')


class PaintSystemBaseNode:
    uuid: StringProperty(name="UUID")
    name: StringProperty(name="Name", default="",
                         set_transform=_set_name_transform)

    def init(self, context):
        self.name = self.bl_label
        self.uuid = str(uuid.uuid4())

    def copy(self, node):
        self.name = node.name
        self.uuid = str(uuid.uuid4())

    def free(self):
        pass

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'PaintSystemNodeTree'
