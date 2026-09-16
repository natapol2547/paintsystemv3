"""Where every texel of a layer image sits on the mesh (PS-092).

A `TexelMap` is two GPU textures the size of a layer image. `position`
holds the world position of the surface under each texel with coverage in
alpha, and `normal` holds the world normal. With it, anything done in the
3D view - a lasso, a flood in screen space, a moved decal - reaches the
image as a flat pass over texels: no tool needs mesh adjacency or seam
handling, and the mesh is rasterised once per change instead of once per
operation.

The mesh is drawn in UV space: the vertex shader uses the UV coordinate
as the clip position, so a fragment lands on the texel that corner of the
surface is painted on. World transforms are applied with numpy while the
vertex arrays are built, which keeps the push constants to 24 bytes; two
`MAT4` push constants come to 144 and Vulkan only guarantees 128.

The margin is drawn, not flooded. Each triangle is rasterised twice: once
pushed outward from its own UV centroid by the margin, writing coverage
`MARGIN_COVERAGE`, and once at its real size writing coverage 1.0 over
the top. This is what Blender's bake margin calls Extend. The jump flood
this ticket first specified yields the nearest island texel rather than a
continuation of the surface, but wants two ping-pong seed textures and a
gather pass into fresh position and normal targets: about 1 GB of video
memory at 4K against the 400 MB the map itself occupies.
"""
import logging

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import core

log = logging.getLogger(__name__)

MARGIN = 4
"""Texels of margin drawn around every UV island, as Blender's bake default."""

MARGIN_COVERAGE = 0.5
"""Alpha written in the margin. An island texel has 1.0 and empty space 0.0,
so a tool that wants real surface tests ``> 0.75`` and one that wants every
paintable texel tests ``> 0.0``."""

CACHE_BUDGET = 1 << 30
"""Video memory the cache may hold, in bytes. A 4K map takes about 400 MB,
so without a ceiling a few image sizes would fill a card on their own."""

_MAP_BYTES_PER_TEXEL = 16 + 8  # RGBA32F position, RGBA16F normal

_VERTEX_SOURCE = """
void main()
{
  v_position = position;
  v_normal = normal;
  /* Grow the triangle away from its own centre by `margin` texels. The
     direction is measured in texels so a non-square image still gets an
     even margin. */
  vec2 away = (uv - uv_centroid) / texel_size;
  float distance = length(away);
  vec2 grown = distance > 1e-8
      ? uv + (away / distance) * texel_size * margin
      : uv;
  gl_Position = vec4((grown - tile_offset) * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAGMENT_SOURCE = """
void main()
{
  out_position = vec4(v_position, coverage);
  out_normal = vec4(normalize(v_normal), coverage);
}
"""

_shader = None


def _texel_shader() -> gpu.types.GPUShader:
    """The UV-space rasteriser, built once per session."""
    global _shader
    if _shader is not None:
        return _shader
    interface = gpu.types.GPUStageInterfaceInfo("ps_texel_map_interface")
    interface.smooth('VEC3', "v_position")
    interface.smooth('VEC3', "v_normal")

    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('VEC2', "tile_offset")
    info.push_constant('VEC2', "texel_size")
    info.push_constant('FLOAT', "margin")
    info.push_constant('FLOAT', "coverage")
    info.vertex_in(0, 'VEC2', "uv")
    info.vertex_in(1, 'VEC2', "uv_centroid")
    info.vertex_in(2, 'VEC3', "position")
    info.vertex_in(3, 'VEC3', "normal")
    info.vertex_out(interface)
    info.fragment_out(0, 'VEC4', "out_position")
    info.fragment_out(1, 'VEC4', "out_normal")
    info.vertex_source(_VERTEX_SOURCE)
    info.fragment_source(_FRAGMENT_SOURCE)
    _shader = gpu.shader.create_from_info(info)
    return _shader


def _triangle_arrays(obj: bpy.types.Object, uv_map: str,
                     depsgraph: bpy.types.Depsgraph) -> dict | None:
    """The evaluated mesh as a triangle soup of vertex attributes.

    Corners are not shared: UVs and split normals are per corner, so every
    triangle contributes three of its own vertices. Returns None when the
    object has no geometry or no UV map to draw into.
    """
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh()
    except RuntimeError as error:
        log.warning("Could not evaluate %r for a texel map: %s", obj.name, error)
        return None
    if mesh is None:
        return None
    try:
        mesh.calc_loop_triangles()
        if not len(mesh.loop_triangles):
            return None
        layer = mesh.uv_layers.get(uv_map) if uv_map else None
        if layer is None:
            layer = mesh.uv_layers.active
        if layer is None:
            return None

        corners = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get('loops', corners)

        uvs = np.empty(len(layer.uv) * 2, dtype=np.float32)
        layer.uv.foreach_get('vector', uvs)
        uvs = uvs.reshape(-1, 2)[corners]

        vertex_of_corner = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get('vertex_index', vertex_of_corner)
        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', positions)
        positions = positions.reshape(-1, 3)[vertex_of_corner[corners]]

        normals = np.empty(len(mesh.corner_normals) * 3, dtype=np.float32)
        mesh.corner_normals.foreach_get('vector', normals)
        normals = normals.reshape(-1, 3)[corners]
    finally:
        evaluated.to_mesh_clear()

    matrix = np.array(obj.matrix_world, dtype=np.float32)
    basis = matrix[:3, :3]
    positions = positions @ basis.T + matrix[:3, 3]
    try:
        normals = normals @ np.linalg.inv(basis)
    except np.linalg.LinAlgError:
        # A zero scale on an axis flattens the object; the normals are
        # meaningless either way, so rotate them and carry on.
        normals = normals @ basis.T

    # One centroid per triangle, repeated for each of its three corners.
    centroids = np.repeat(uvs.reshape(-1, 3, 2).mean(axis=1), 3, axis=0)

    return {
        "uv": uvs,
        "uv_centroid": centroids,
        "position": positions,
        "normal": normals,
    }


class TexelMap:
    """The position and normal textures for one object, image size and tile.

    Free is not exposed: dropping the last reference releases both
    textures and the framebuffer. The cache below is what holds them.
    """

    __slots__ = ('position', 'normal', 'framebuffer', 'width', 'height',
                 'tile', 'margin')

    def __init__(self, position, normal, framebuffer, width, height, tile, margin):
        self.position = position
        self.normal = normal
        self.framebuffer = framebuffer
        self.width = width
        self.height = height
        self.tile = tile
        self.margin = margin

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def video_memory(self) -> int:
        return self.width * self.height * _MAP_BYTES_PER_TEXEL

    def positions(self) -> np.ndarray:
        """World position and coverage per texel, `(height, width, 4)`.

        A read back, not the GPU copy. Tools that stay on the GPU sample
        `self.position` instead.
        """
        return core.read_color(self.framebuffer, self.width, self.height, 0)

    def normals(self) -> np.ndarray:
        """World normal and coverage per texel, `(height, width, 4)`."""
        return core.read_color(self.framebuffer, self.width, self.height, 1)


def build_texel_map(obj: bpy.types.Object, uv_map: str, width: int, height: int,
                    tile: int = 1001, margin: int = MARGIN,
                    depsgraph: bpy.types.Depsgraph | None = None) -> TexelMap | None:
    """Rasterise *obj* into UV space at *width* x *height*.

    Returns None when this session cannot draw, or when the object has no
    geometry or no UV map. Callers get the cached version through
    `get_texel_map`; this is the uncached build.
    """
    if not core.gpu_available():
        return None
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    arrays = _triangle_arrays(obj, uv_map, depsgraph)
    if arrays is None:
        return None

    shader = _texel_shader()
    batch = batch_for_shader(shader, 'TRIS', arrays)
    position = gpu.types.GPUTexture((width, height), format='RGBA32F')
    normal = gpu.types.GPUTexture((width, height), format='RGBA16F')
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(position, normal))

    previous_blend = gpu.state.blend_get()
    previous_depth = gpu.state.depth_test_get()
    try:
        # Writes must land as written: blending would mix the margin pass
        # into the real one, and a depth test would drop coplanar
        # fragments, since every triangle is drawn at depth 0.
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('NONE')
        # UV islands may be mirrored, which reverses their winding.
        gpu.state.face_culling_set('NONE')
        with framebuffer.bind():
            framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0))
            shader.uniform_float("tile_offset", core.tile_offset(tile))
            shader.uniform_float("texel_size", (1.0 / width, 1.0 / height))
            # The margin first, so the real triangles cover it.
            shader.uniform_float("margin", float(margin))
            shader.uniform_float("coverage", MARGIN_COVERAGE)
            batch.draw(shader)
            shader.uniform_float("margin", 0.0)
            shader.uniform_float("coverage", 1.0)
            batch.draw(shader)
    finally:
        gpu.state.blend_set(previous_blend)
        gpu.state.depth_test_set(previous_depth)

    return TexelMap(position, normal, framebuffer, width, height, tile, margin)


# ── Cache ────────────────────────────────────────────────────────────

_maps: dict[tuple, TexelMap] = {}
_recent: list[tuple] = []


def _matrix_key(obj: bpy.types.Object) -> tuple:
    """The world matrix as a hashable key.

    The map stores world positions, so moving the object invalidates it.
    `on_depsgraph_update_post` normally drops the entry first; this keeps
    the cache correct even when it does not run.
    """
    return tuple(round(value, 6) for row in obj.matrix_world for value in row)


def get_texel_map(obj: bpy.types.Object, uv_map: str, size: tuple[int, int],
                  tile: int = 1001, margin: int = MARGIN) -> TexelMap | None:
    """The cached map for these arguments, building it on first use."""
    width, height = size
    key = (obj.session_uid, uv_map or '', width, height, tile, margin,
           _matrix_key(obj))
    cached = _maps.get(key)
    if cached is not None:
        _recent.remove(key)
        _recent.append(key)
        return cached
    built = build_texel_map(obj, uv_map, width, height, tile, margin)
    if built is None:
        return None
    _maps[key] = built
    _recent.append(key)
    _evict()
    return built


def _evict() -> None:
    """Drop the least recently used maps until the cache fits the budget.

    The newest map is never dropped, so a single map larger than the whole
    budget is still usable.
    """
    total = sum(item.video_memory for item in _maps.values())
    while total > CACHE_BUDGET and len(_recent) > 1:
        key = _recent.pop(0)
        total -= _maps.pop(key).video_memory


def invalidate(session_uid: int | None = None) -> None:
    """Drop cached maps: all of them, or only one object's."""
    if session_uid is None:
        _maps.clear()
        _recent.clear()
        return
    for key in [key for key in _maps if key[0] == session_uid]:
        del _maps[key]
        _recent.remove(key)


def cached_count() -> int:
    """How many maps the cache holds. For the tests."""
    return len(_maps)


def release() -> None:
    """Free every GPU object this module holds.

    Called when the addon is unregistered. Leaving them to Python's own
    teardown is not enough: in a background session the GPU context is
    already gone by then and freeing a texture there segfaults Blender.
    """
    global _shader
    invalidate()
    _shader = None
