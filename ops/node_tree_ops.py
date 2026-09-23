import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, StringProperty
from bpy.utils import register_classes_factory

from .. import templates
from ..common import icon_kwargs, is_newer_than
from ..context import PREVIEW_TREE_KEY, find_material_group_node, get_active_tree, get_ps_object, update_active_image
from ..compiler.core import compile_tree, flush_now, suspend_compile
from ..nodes.layers.base_layer_node import RESOLUTION_ITEMS
from ..nodes.layers.registry import layer_type
from ..props.channel import PREVIEW_OUTPUT, SOCKET_ICONS


def link_tree_to_material(material, tree):
    """Run *tree* in *material* and return the group node that runs it.

    Adds the group node when there is none, left of the shader node the
    channels paint into (see ``templates.find_target``). Each channel that
    matches a channel template by name and type is connected to that node,
    as a template would connect it. Channels that are already connected
    stay as they are. A new group node also gets back the links a deleted
    one had to Paint Over's Shader to RGB and to a running channel preview.
    """
    if not is_newer_than(5, 0):
        material.use_nodes = True
    material.paint_system.tree = tree
    compile_tree(tree)
    is_new = find_material_group_node(material, tree) is None
    group = templates.ensure_group_node(material, tree)
    target = templates.find_target(material, group)
    if is_new:
        templates.place_group(material, group, target)
        templates.restore_paint_over(material, group, tree, target)
        _restore_preview(material, group, tree)
    if target is not None:
        for channel in tree.channels:
            templates.connect_channel(material, group, channel, target)
    return group


def _restore_preview(material, group, tree) -> None:
    """Show *tree*'s running channel preview through the new *group* in *material*.

    The preview's Material Output stays active when its group node is
    deleted, and would show nothing.
    """
    if not tree.preview_channel or PREVIEW_OUTPUT not in group.outputs:
        return
    output = next((node for node in material.node_tree.nodes if node.get(PREVIEW_TREE_KEY) == tree.uuid), None)
    if output is not None:
        material.node_tree.links.new(group.outputs[PREVIEW_OUTPUT], output.inputs['Surface'])


def _connected(group, tree) -> bool:
    """Whether *group* shows any of *tree*'s channels in its material."""
    return any(group.outputs[channel.name].is_linked for channel in tree.channels
               if channel.name in group.outputs)


def _edited_id(obj):
    """The data Add Paint System writes to: the active material, or what gets the new one."""
    if obj.active_material is not None:
        return obj.active_material
    if len(obj.material_slots) > 0 and obj.material_slots[obj.active_material_index].link == 'OBJECT':
        return obj
    return obj.data


def _on_template_changed(self, context):
    for prop, value in templates.template_defaults(self.template).items():
        setattr(self, prop, value)


START_ITEMS = [
    ('IMAGE', "Image Layer", "Start with an empty image layer to paint on"),
    ('NOTHING', "Nothing", "Start with no layers"),
]

# The dialog's width, and the width a summary line may take in it, in UI units.
DIALOG_WIDTH = 360
SUMMARY_WIDTH = 300


class PAINTSYSTEM_OT_setup_material(Operator):
    bl_idname = "paint_system.setup_material"
    bl_label = "Add Paint System"
    bl_description = ("Paint the active object's material with a new Paint System tree, set up from a template. "
                      "A material whose tree is no longer in it gets it back")
    # No REGISTER: the dialog is the options UI. An Adjust Last Operation
    # panel would draw the dialog again against the finished material, where
    # its recommendation and summary no longer hold.
    bl_options = {'UNDO'}

    # Declared first: keyword arguments are applied in declaration order, so
    # an option passed along with the template wins over the template's
    # default.
    template: EnumProperty(
        name="Template",
        items=templates.MATERIAL_TEMPLATE_ITEMS,
        default='UNLIT',
        update=_on_template_changed,
        options={'SKIP_SAVE'},
    )
    canvas: FloatVectorProperty(
        name="Canvas",
        description="The colour the canvas starts as. A clear canvas starts transparent",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        options={'SKIP_SAVE'},
    )
    add_color: BoolProperty(name="Color", default=True, options={'SKIP_SAVE'})
    add_metallic: BoolProperty(name="Metallic", default=False, options={'SKIP_SAVE'})
    add_roughness: BoolProperty(name="Roughness", default=False, options={'SKIP_SAVE'})
    add_normal: BoolProperty(name="Normal", default=False, options={'SKIP_SAVE'})
    start_with: EnumProperty(name="Start With", items=START_ITEMS, default='NOTHING', options={'SKIP_SAVE'})
    resolution: EnumProperty(name="Resolution", items=RESOLUTION_ITEMS, default='2048', options={'SKIP_SAVE'})
    uv_map: StringProperty(
        name="UV Map",
        description="The UV map the first layer and a Normal channel use. Empty uses the active render UV map",
        options={'SKIP_SAVE'},
    )
    use_smooth_transparency: BoolProperty(
        name="Smooth Transparency",
        description="Blend transparent parts smoothly. Overlapping transparent faces may show in the wrong order",
        default=False,
        options={'SKIP_SAVE'},
    )
    use_backface_culling: BoolProperty(
        name="Backface Culling",
        description="Hide the back of faces, and the far side of transparent surfaces",
        default=False,
        options={'SKIP_SAVE'},
    )
    use_standard_view: BoolProperty(
        name="Standard View Transform",
        description="Show colours as they are painted, without the scene's tone mapping",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        obj = get_ps_object(getattr(context, 'object', None))
        if obj is None:
            cls.poll_message_set("Select a mesh, or an empty parented to one")
            return False
        material = obj.active_material
        tree = material.paint_system.tree if material is not None else None
        if tree is not None and find_material_group_node(material, tree) is not None:
            cls.poll_message_set("The material already paints with the Paint System")
            return False
        # Linked data is read again from its library when the file opens,
        # so the setup would not last.
        if not _edited_id(obj).is_editable:
            cls.poll_message_set("Linked data cannot be set up. Make it local first")
            return False
        return True

    def invoke(self, context, event):
        material = get_ps_object(context.object).active_material
        if material is not None and material.paint_system.tree is not None:
            return self.execute(context)
        self.template = templates.recommend(material, context.scene)[0]
        self.start_with = 'IMAGE'
        return context.window_manager.invoke_props_dialog(
            self, width=DIALOG_WIDTH, title="Add Paint System", confirm_text="Add")

    def execute(self, context):
        obj = get_ps_object(context.object)
        material = obj.active_material
        scene = context.scene
        if material is not None and material.paint_system.tree is not None:
            tree = material.paint_system.tree
            group = link_tree_to_material(material, tree)
            self._finish(context, tree)
            if _connected(group, tree):
                self.report({'INFO'}, f'Connected "{tree.name}" to "{material.name}" again')
            else:
                self.report({'WARNING'}, f'Added "{tree.name}" back to "{material.name}", but found no shader '
                                         'node to connect it to. Connect its outputs to show the paint')
            return {'FINISHED'}

        # A script that names no template gets the recommended one, with its
        # defaults for the options the script did not set either.
        template = self.template
        if not self.properties.is_property_set('template'):
            template = templates.recommend(material, scene)[0]
            for prop, value in templates.template_defaults(template).items():
                if not self.properties.is_property_set(prop):
                    setattr(self, prop, value)
        problem = templates.refusal(template, self, material, scene)
        if problem is not None:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}

        fresh = material is None or material.node_tree is None
        if material is None:
            material = bpy.data.materials.new(templates.new_material_name(obj))
            if len(obj.material_slots) == 0:
                obj.data.materials.append(material)
            else:
                obj.material_slots[obj.active_material_index].material = material
        templates.prepare_material(material, template, fresh)

        tree = bpy.data.node_groups.new(material.name, 'PaintSystemNodeTree')
        tree.initialize(add_channel=False)
        with suspend_compile(tree):
            for key in templates.template_channels(template, self):
                templates.create_template_channel(tree, key, uv_map=self.uv_map)
            # The first layer goes into the first channel, and takes its
            # colour space.
            tree.active_channel_index = 0
            if self.start_with == 'IMAGE':
                layer = layer_type('IMAGE').create(tree, ps_object=obj, resolution=self.resolution)
                if self.uv_map:
                    layer.uv_map = self.uv_map
        material.paint_system.tree = tree
        # The group node only gets the channels' sockets once the tree compiles.
        compile_tree(tree)
        group = templates.ensure_group_node(material, tree)
        templates.build_material(template, material, group, tree, self)
        templates.apply_material_settings(material, self)
        if self.use_standard_view and templates.standard_view_applies(scene):
            templates.apply_standard_view(scene)
        self._finish(context, tree)
        return {'FINISHED'}

    def _finish(self, context, tree):
        context.scene.paint_system.active_node_tree = tree
        update_active_image(context)

    # -- the dialog -------------------------------------------------------

    def draw(self, context):
        layout = self.layout
        obj = get_ps_object(context.object)
        material = obj.active_material
        scene = context.scene
        recommended, reason = templates.recommend(material, scene)

        cards = layout.column(align=True)
        cards.scale_y = 1.4
        for keys in (('UNLIT', 'PBR', 'PAINT_OVER'), ('NORMAL', 'GROUP')):
            row = cards.row(align=True)
            for key in keys:
                card = row.row(align=True)
                card.enabled = key != 'PAINT_OVER' or templates.paint_over_possible(material, scene)
                card.prop_enum(self, "template", key)
        if self.template == recommended:
            layout.label(text=reason, **icon_kwargs('INFO'))

        self._draw_summary(layout.box().column(align=True), context, obj)
        self._draw_options(layout, obj)

        header, body = layout.panel("paint_system_add_material_viewport", default_closed=True)
        header.label(text="Material & Viewport")
        if body is not None:
            col = body.column()
            col.prop(self, "use_smooth_transparency")
            if self.use_smooth_transparency:
                col.label(text="Overlapping transparent faces may show in the wrong order.",
                          **icon_kwargs('ERROR'))
            col.prop(self, "use_backface_culling")
            if templates.standard_view_applies(scene):
                col.prop(self, "use_standard_view")

    def _draw_summary(self, col, context, obj):
        points = context.preferences.ui_styles[0].widget.points
        for line in templates.summary_lines(self.template, self, obj, context):
            # An indented line starts two icon widths in.
            width = SUMMARY_WIDTH - (40 if line.indent else 0)
            for index, text in enumerate(templates.wrap_text(line.text, width, points)):
                row = col.row()
                if line.indent:
                    row.separator(factor=2.0)
                # A wrapped warning keeps its text lined up under the first line.
                icon = ('ERROR' if index == 0 else 'BLANK1') if line.warning else 'NONE'
                row.label(text=text, **icon_kwargs(icon))

    def _draw_options(self, layout, obj):
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        if self.template == 'UNLIT':
            col.prop(self, "canvas")
        elif self.template == 'PBR':
            channels = col.column(heading="Channels", align=True)
            for key, prop in templates.PBR_CHANNEL_OPTIONS.items():
                channels.prop(self, prop, **icon_kwargs(SOCKET_ICONS[templates.CHANNEL_TEMPLATES[key].type]))
        col.prop(self, "start_with")
        if self.start_with == 'IMAGE':
            col.row().prop(self, "resolution", expand=True)
        uses_uv = self.start_with == 'IMAGE' or 'NORMAL' in templates.template_channels(self.template, self)
        if uses_uv and len(obj.data.uv_layers) > 1:
            col.prop_search(self, "uv_map", obj.data, "uv_layers")


class PAINTSYSTEM_OT_compile_tree(Operator):
    bl_idname = "paint_system.compile_tree"
    bl_label = "Recompile"
    bl_description = "Force a full rebuild of the compiled shader group"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return get_active_tree(context) is not None

    def execute(self, context):
        tree = get_active_tree(context)
        fingerprint = compile_tree(tree, force=True)
        flush_now()
        self.report({'INFO'}, f"Compiled {tree.compiled.name} ({fingerprint[:8]})")
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_setup_material,
    PAINTSYSTEM_OT_compile_tree,
)


register, unregister = register_classes_factory(classes)
