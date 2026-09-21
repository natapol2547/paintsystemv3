"""Shaders that draw the selection as a tint and marching ants (PS-091).

The image editor draws the mask over the image in one pass
(`create_image_shader`). It finds the outline from mask samples one
screen pixel to each side. The image is already in texture space, so
bilinear reads give smooth outlines directly.

The 3D view uses two passes. `create_coverage_shader` writes the mask
value of the front surface, read at the nearest texel, into an offscreen
buffer the size of the region. `create_screen_shader` then draws the mesh
again and finds the outline from neighbouring screen pixels of that
buffer. Neighbours in texture space would draw ants along every UV seam,
because the texels next to an island belong to the gutter or to another
part of the surface. Reading the nearest texel also keeps a gutter texel
from bleeding into a selected island.

- Both outline shaders share one push constant layout, `PUSH_CONSTANTS`,
  of 128 bytes, the most Vulkan guarantees.
- Their output is linear, so colours are converted from sRGB before
  upload (`overlay.srgb_to_linear`).
- Linear filtering of float textures is an optional device feature on
  Vulkan and Metal, so every read is a `texelFetch`.
"""
import gpu

PUSH_CONSTANTS = (
    ('MAT4', "view_projection"),
    ('VEC4', "wash"),       # rgb, opacity
    ('VEC4', "ant_a"),      # rgb, dash phase in pixels
    ('VEC4', "ant_b"),      # rgb, dash length in pixels
    ('VEC4', "params"),     # tile offset xy, clip z offset (3D only), half line width in pixels
)
"""The push constants of the image and screen shaders, in declaration order."""

ANT_GLSL = """
vec3 ant_color(vec2 tangent, vec4 dash_a, vec4 dash_b)
{
  /* Dash along the screen axis closest to the outline's tangent, so a
     dash is 1 to 1.41 dash lengths long in any direction. The dashes
     march along the tangent. dash_a.w is the phase and dash_b.w is the
     dash length, both in pixels. */
  float along = abs(tangent.x) >= abs(tangent.y) ?
                    (tangent.x < 0.0 ? -gl_FragCoord.x : gl_FragCoord.x) :
                    (tangent.y < 0.0 ? -gl_FragCoord.y : gl_FragCoord.y);
  float stripe = step(dash_b.w, mod(along - dash_a.w, 2.0 * dash_b.w));
  return mix(dash_a.rgb, dash_b.rgb, stripe);
}
"""
"""`vec3 ant_color(vec2 tangent, vec4 dash_a, vec4 dash_b)`: the dash colour at this fragment.

Pass the `ant_a` and `ant_b` push constants. Any shader whose outline
tangent is known can use it, so every set of ants crawls in step.
"""

MASK_GLSL = """
float mask_at(vec2 t, ivec2 last)
{
  /* Bilinear filtering by hand. `t` is in texels, with texel centres at
     whole numbers. Reads clamp to the edge. */
  vec2 base = floor(t);
  vec2 w = t - base;
  ivec2 i = ivec2(base);
  float a = texelFetch(mask, clamp(i, ivec2(0), last), 0).r;
  float b = texelFetch(mask, clamp(i + ivec2(1, 0), ivec2(0), last), 0).r;
  float c = texelFetch(mask, clamp(i + ivec2(0, 1), ivec2(0), last), 0).r;
  float d = texelFetch(mask, clamp(i + ivec2(1, 1), ivec2(0), last), 0).r;
  return mix(mix(a, b, w.x), mix(c, d, w.x), w.y);
}
"""
"""`float mask_at(vec2 t, ivec2 last)`: the mask bilinearly filtered at texel coordinate *t*."""

_SHADE_GLSL = ANT_GLSL + """
vec4 shade(float m, float right, float left, float up, float down)
{
  /* How fast the mask changes per screen pixel, from the values one pixel
     to each side. fwidth alone misses an edge that falls between two 2x2
     derivative quads. That happens at exactly half size and whenever a
     texel covers several pixels. */
  vec2 slopes = vec2(max(abs(right - m), abs(m - left)), max(abs(up - m), abs(m - down)));
  /* Distance to the 0.5 iso-line in pixels. */
  float distance = abs(m - 0.5) / max(length(slopes), 1e-6);
  float line = 1.0 - smoothstep(params.w - 0.5, params.w + 0.5, distance);
  vec2 direction = vec2(right - left, up - down);
  vec3 ant = ant_color(vec2(-direction.y, direction.x), ant_a, ant_b);
  /* Put the ants over the wash, both with straight alpha. Mixing the
     colours by `line` instead would carry the wash colour into the ants'
     soft edge, even when the wash is transparent. */
  float wash_alpha = wash.a * clamp(m, 0.0, 1.0);
  float alpha = line + wash_alpha * (1.0 - line);
  vec3 color = ant * line + wash.rgb * (wash_alpha * (1.0 - line));
  return vec4(color / max(alpha, 1e-6), alpha);
}
"""

_IMAGE_VERTEX = """
void main()
{
  v_uv = uv;
  gl_Position = view_projection * vec4(position, 1.0);
}
"""

_IMAGE_FRAGMENT = MASK_GLSL + _SHADE_GLSL + """
void main()
{
  vec2 uv = v_uv - params.xy;
  ivec2 last = textureSize(mask, 0) - ivec2(1);
  vec2 t = uv * vec2(last + ivec2(1)) - 0.5;
  /* Take derivatives before any discard. After a discard they can be
     undefined. */
  vec2 dt_dx = dFdx(t);
  vec2 dt_dy = dFdy(t);
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x >= 1.0 || uv.y >= 1.0) {
    discard;
  }
  vec4 color = shade(mask_at(t, last),
                     mask_at(t + dt_dx, last), mask_at(t - dt_dx, last),
                     mask_at(t + dt_dy, last), mask_at(t - dt_dy, last));
  if (color.a <= 0.0) {
    discard;
  }
  out_color = color;
}
"""

_COVERAGE_VERTEX = _IMAGE_VERTEX

_COVERAGE_FRAGMENT = """
void main()
{
  vec2 uv = v_uv - tile.xy;
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x >= 1.0 || uv.y >= 1.0) {
    discard;
  }
  ivec2 size = textureSize(mask, 0);
  float m = texelFetch(mask, clamp(ivec2(floor(uv * vec2(size))), ivec2(0), size - ivec2(1)), 0).r;
  /* g marks the pixel as covered, so the screen pass can tell the
     surface from the background. */
  out_value = vec4(m, 1.0, 0.0, 1.0);
}
"""

_SCREEN_VERTEX = """
void main()
{
  gl_Position = view_projection * vec4(position, 1.0);
  /* Blender's polygon offset for overlays, applied in clip space. It
     moves the vertex toward the viewer by a constant clip z
     (`overlay.clip_offset`). */
  gl_Position.z -= params.z;
}
"""

_SCREEN_FRAGMENT = _SHADE_GLSL + """
float value_at(ivec2 p, ivec2 last, float fallback)
{
  vec4 v = texelFetch(screen, clamp(p, ivec2(0), last), 0);
  return v.g > 0.5 ? v.r : fallback;
}

void main()
{
  ivec2 last = textureSize(screen, 0) - ivec2(1);
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec4 centre = texelFetch(screen, clamp(p, ivec2(0), last), 0);
  if (centre.g < 0.5) {
    discard;
  }
  float m = centre.r;
  /* A background neighbour takes this pixel's value, so a fully selected
     surface draws no outline along its silhouette. */
  vec4 color = shade(m,
                     value_at(p + ivec2(1, 0), last, m), value_at(p - ivec2(1, 0), last, m),
                     value_at(p + ivec2(0, 1), last, m), value_at(p - ivec2(0, 1), last, m));
  if (color.a <= 0.0) {
    discard;
  }
  out_color = color;
}
"""


def _outline_info(name: str) -> gpu.types.GPUShaderCreateInfo:
    info = gpu.types.GPUShaderCreateInfo()
    for kind, uniform in PUSH_CONSTANTS:
        info.push_constant(kind, uniform)
    info.sampler(0, 'FLOAT_2D', name)
    info.vertex_in(0, 'VEC3', "position")
    info.fragment_out(0, 'VEC4', "out_color")
    return info


def create_image_shader() -> gpu.types.GPUShader:
    """Build the image editor's one-pass shader (a quad with `position` and `uv`, sampler `mask`)."""
    interface = gpu.types.GPUStageInterfaceInfo("ps_selection_image_interface")
    interface.smooth('VEC2', "v_uv")
    info = _outline_info("mask")
    info.vertex_in(1, 'VEC2', "uv")
    info.vertex_out(interface)
    info.vertex_source(_IMAGE_VERTEX)
    info.fragment_source(_IMAGE_FRAGMENT)
    return gpu.shader.create_from_info(info)


def create_coverage_shader() -> gpu.types.GPUShader:
    """Build the 3D view's first pass, which writes (nearest mask value, 1, 0, 1) for the mesh.

    Push constants are `view_projection` and `tile` (the tile's UV offset
    in xy). The sampler is `mask`. Vertices have `position` and `uv`.
    """
    interface = gpu.types.GPUStageInterfaceInfo("ps_selection_coverage_interface")
    interface.smooth('VEC2', "v_uv")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', "view_projection")
    info.push_constant('VEC4', "tile")
    info.sampler(0, 'FLOAT_2D', "mask")
    info.vertex_in(0, 'VEC3', "position")
    info.vertex_in(1, 'VEC2', "uv")
    info.vertex_out(interface)
    info.fragment_out(0, 'VEC4', "out_value")
    info.vertex_source(_COVERAGE_VERTEX)
    info.fragment_source(_COVERAGE_FRAGMENT)
    return gpu.shader.create_from_info(info)


def create_screen_shader() -> gpu.types.GPUShader:
    """Build the 3D view's second pass (mesh `position` only, sampler `screen` is the coverage buffer)."""
    info = _outline_info("screen")
    info.vertex_source(_SCREEN_VERTEX)
    info.fragment_source(_SCREEN_FRAGMENT)
    return gpu.shader.create_from_info(info)
