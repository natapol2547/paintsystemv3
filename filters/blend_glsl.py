# SPDX-License-Identifier: GPL-3.0-or-later
"""The layer compositing rules as one GPU pass (PS-057).

`compiler.library._build_layer_blend` is what the render engines run. To
filter the stack below a layer, the same compositing has to happen on the
GPU, over textures rather than shader sockets, and agree with the render
to the texel. This module is that port: the same Porter-Duff split, the
same clip handling, and Blender's own blend functions at factor 1, which
is the only factor the library group ever asks them for.

`compiler.library._build_filter_mix` is ported alongside it, because a
filter layer can sit below another one and replaces the stack rather than
compositing over it. Which rule a pass runs is the `rule` argument.

Both sides work in straight alpha and scene-linear colour, which is what
the shader graph carries between layers. Converting a source image's
stored values into that is the caller's job.

`ALLOWED_BLEND_MODES` is an allow-list, not a claim. A mode joins it once
its per-texel parity test against a Cycles bake of the library group
passes (`tests/test_filter_blend.py`). A mode outside it is not an error:
the filter layer falls back to baking its input with Cycles instead.
"""
from __future__ import annotations

import gpu
from gpu_extras.batch import batch_for_shader


# The identifiers of ShaderNodeMix.blend_type, numbered for the shader.
# The values are part of no file and no artifact, so they may be
# renumbered freely; they only have to agree within this module.
BLEND_MODE_IDS = {
    'MIX': 0,
    'DARKEN': 1,
    'MULTIPLY': 2,
    'BURN': 3,
    'LIGHTEN': 4,
    'SCREEN': 5,
    'DODGE': 6,
    'ADD': 7,
    'OVERLAY': 8,
    'SOFT_LIGHT': 9,
    'LINEAR_LIGHT': 10,
    'DIFFERENCE': 11,
    'EXCLUSION': 12,
    'SUBTRACT': 13,
    'DIVIDE': 14,
    'HUE': 15,
    'SATURATION': 16,
    'COLOR': 17,
    'VALUE': 18,
}

# Modes whose parity test passes. See the module docstring.
ALLOWED_BLEND_MODES = frozenset(BLEND_MODE_IDS)

# Which compositing rule a pass runs: an ordinary layer blend, or a filter
# layer replacing the stack below it.
BLEND, FILTER_MIX = 'BLEND', 'FILTER_MIX'


_COLOR_UTILS = """
vec3 ps_rgb_to_hsv(vec3 rgb)
{
  float cmax = max(rgb.r, max(rgb.g, rgb.b));
  float cmin = min(rgb.r, min(rgb.g, rgb.b));
  float cdelta = cmax - cmin;
  float v = cmax;
  float s = cmax != 0.0 ? cdelta / cmax : 0.0;
  float h = 0.0;
  if (s != 0.0) {
    vec3 c = (vec3(cmax) - rgb) / cdelta;
    if (rgb.r == cmax) {
      h = c.b - c.g;
    }
    else if (rgb.g == cmax) {
      h = 2.0 + c.r - c.b;
    }
    else {
      h = 4.0 + c.g - c.r;
    }
    h /= 6.0;
    if (h < 0.0) {
      h += 1.0;
    }
  }
  return vec3(h, s, v);
}

vec3 ps_hsv_to_rgb(vec3 hsv)
{
  float h = hsv.x;
  float s = hsv.y;
  float v = hsv.z;
  if (s == 0.0) {
    return vec3(v);
  }
  if (h == 1.0) {
    h = 0.0;
  }
  h *= 6.0;
  float i = floor(h);
  float f = h - i;
  float p = v * (1.0 - s);
  float q = v * (1.0 - (s * f));
  float t = v * (1.0 - (s * (1.0 - f)));
  if (i == 0.0) {
    return vec3(v, t, p);
  }
  if (i == 1.0) {
    return vec3(q, v, p);
  }
  if (i == 2.0) {
    return vec3(p, v, t);
  }
  if (i == 3.0) {
    return vec3(p, q, v);
  }
  if (i == 4.0) {
    return vec3(t, p, v);
  }
  return vec3(v, p, q);
}
"""

# Blender's own mix functions with the factor at 1, which is all the
# library group asks for: it sets the blend node's Factor to a constant 1
# and does the fading in the compositing around it.
_BLEND = """
float ps_burn(float a, float b)
{
  if (b <= 0.0) {
    return 0.0;
  }
  return clamp(1.0 - (1.0 - a) / b, 0.0, 1.0);
}

float ps_dodge(float a, float b)
{
  if (a == 0.0) {
    return 0.0;
  }
  float t = 1.0 - b;
  if (t <= 0.0) {
    return 1.0;
  }
  return min(a / t, 1.0);
}

float ps_overlay(float a, float b)
{
  if (a < 0.5) {
    return a * 2.0 * b;
  }
  return 1.0 - 2.0 * (1.0 - b) * (1.0 - a);
}

float ps_soft_light(float a, float b)
{
  float scr = 1.0 - (1.0 - b) * (1.0 - a);
  return (1.0 - a) * b * a + a * scr;
}

float ps_divide(float a, float b)
{
  return b != 0.0 ? a / b : 0.0;
}

vec3 ps_blend(int mode, vec3 a, vec3 b)
{
  if (mode == 1) {  /* DARKEN */
    return min(a, b);
  }
  if (mode == 2) {  /* MULTIPLY */
    return a * b;
  }
  if (mode == 3) {  /* BURN */
    return vec3(ps_burn(a.r, b.r), ps_burn(a.g, b.g), ps_burn(a.b, b.b));
  }
  if (mode == 4) {  /* LIGHTEN */
    return max(a, b);
  }
  if (mode == 5) {  /* SCREEN */
    return vec3(1.0) - (vec3(1.0) - b) * (vec3(1.0) - a);
  }
  if (mode == 6) {  /* DODGE */
    return vec3(ps_dodge(a.r, b.r), ps_dodge(a.g, b.g), ps_dodge(a.b, b.b));
  }
  if (mode == 7) {  /* ADD */
    return a + b;
  }
  if (mode == 8) {  /* OVERLAY */
    return vec3(ps_overlay(a.r, b.r), ps_overlay(a.g, b.g), ps_overlay(a.b, b.b));
  }
  if (mode == 9) {  /* SOFT_LIGHT */
    return vec3(ps_soft_light(a.r, b.r), ps_soft_light(a.g, b.g), ps_soft_light(a.b, b.b));
  }
  if (mode == 10) {  /* LINEAR_LIGHT */
    return a + 2.0 * b - vec3(1.0);
  }
  if (mode == 11) {  /* DIFFERENCE */
    return abs(a - b);
  }
  if (mode == 12) {  /* EXCLUSION */
    return max(a + b - 2.0 * a * b, vec3(0.0));
  }
  if (mode == 13) {  /* SUBTRACT */
    return a - b;
  }
  if (mode == 14) {  /* DIVIDE */
    return vec3(ps_divide(a.r, b.r), ps_divide(a.g, b.g), ps_divide(a.b, b.b));
  }
  if (mode >= 15) {  /* HUE, SATURATION, COLOR, VALUE */
    vec3 hsv = ps_rgb_to_hsv(a);
    vec3 hsv2 = ps_rgb_to_hsv(b);
    if (mode == 15) {
      if (hsv2.y == 0.0) {
        return a;
      }
      return ps_hsv_to_rgb(vec3(hsv2.x, hsv.y, hsv.z));
    }
    if (mode == 16) {
      if (hsv.y == 0.0) {
        return a;
      }
      return ps_hsv_to_rgb(vec3(hsv.x, hsv2.y, hsv.z));
    }
    if (mode == 17) {
      if (hsv2.y == 0.0) {
        return a;
      }
      return ps_hsv_to_rgb(vec3(hsv2.x, hsv2.y, hsv.z));
    }
    return ps_hsv_to_rgb(vec3(hsv.x, hsv.y, hsv2.z));
  }
  return b;  /* MIX */
}
"""

# The compositing of _build_layer_blend, texel for texel. The comments
# there explain the split; this only has to agree with it.
_COMPOSITE = """
vec4 ps_layer_blend(vec4 prev, vec4 src, float opacity, float mask, float clip, int mode)
{
  float es = clamp(src.a * opacity * mask, 0.0, 1.0);
  float kept = mix(1.0, prev.a, clamp(clip, 0.0, 1.0));
  float source_weight = es * kept;
  float backdrop_weight = prev.a * (1.0 - es);
  float alpha = source_weight + backdrop_weight;
  /* Math DIVIDE returns 0 for a zero divisor, so a transparent result
     takes the backdrop colour instead of NaN. */
  float source_share = alpha != 0.0 ? source_weight / alpha : 0.0;
  vec3 source_color = src.rgb;
  if (mode != 0) {
    float blended_share = kept != 0.0 ? prev.a / kept : 0.0;
    source_color = mix(src.rgb, ps_blend(mode, prev.rgb, src.rgb),
                       clamp(blended_share, 0.0, 1.0));
  }
  return vec4(mix(prev.rgb, source_color, clamp(source_share, 0.0, 1.0)), alpha);
}

/* _build_filter_mix: the stack below is replaced by the filter's own
   pixels rather than composited under them, faded by Amount. `kept`
   weights only the source, so a clipped filter never adds coverage
   outside the layer it is clipped to. */
vec4 ps_filter_mix(vec4 prev, vec4 src, float amount, float mask, float clip)
{
  float f = clamp(amount * mask, 0.0, 1.0);
  float kept = mix(1.0, prev.a, clamp(clip, 0.0, 1.0));
  float source_weight = src.a * f * kept;
  float backdrop_weight = prev.a * (1.0 - f);
  float alpha = source_weight + backdrop_weight;
  float source_share = alpha != 0.0 ? source_weight / alpha : 0.0;
  return vec4(mix(prev.rgb, src.rgb, clamp(source_share, 0.0, 1.0)), alpha);
}
"""

BLEND_GLSL = _COLOR_UTILS + _BLEND + _COMPOSITE


_QUAD = {"position": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0),
                      (0.0, 0.0), (1.0, 1.0), (0.0, 1.0))}

_VERTEX = """
void main()
{
  v_texel = position * target_size;
  gl_Position = vec4(position * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAGMENT = """
void main()
{
  ivec2 texel = ivec2(v_texel);
  vec4 prev = texelFetch(backdrop, texel, 0);
  vec4 src = texelFetch(source, texel, 0);
  float m = opacity;
  if (use_mask != 0) {
    m *= clamp(texelFetch(mask, texel, 0).r, 0.0, 1.0);
  }
  out_color = filter_mix != 0 ? ps_filter_mix(prev, src, m, 1.0, clip)
                              : ps_layer_blend(prev, src, m, 1.0, clip, mode);
}
"""

_shader = None
_batch = None
_no_mask_texture = None


def blend_shader():
    """The compositing shader and its batch, built once per session."""
    global _shader, _batch
    if _shader is None:
        interface = gpu.types.GPUStageInterfaceInfo("ps_layer_blend_iface")
        interface.smooth('VEC2', "v_texel")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('VEC2', "target_size")
        info.push_constant('FLOAT', "opacity")
        info.push_constant('FLOAT', "clip")
        info.push_constant('INT', "mode")
        info.push_constant('INT', "filter_mix")
        info.push_constant('INT', "use_mask")
        info.sampler(0, 'FLOAT_2D', "backdrop")
        info.sampler(1, 'FLOAT_2D', "source")
        info.sampler(2, 'FLOAT_2D', "mask")
        info.vertex_in(0, 'VEC2', "position")
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', "out_color")
        info.vertex_source(_VERTEX)
        info.fragment_source(BLEND_GLSL + _FRAGMENT)
        _shader = gpu.shader.create_from_info(info)
        _batch = batch_for_shader(_shader, 'TRIS', _QUAD)
    return _shader, _batch


def _no_mask():
    """A 1x1 texture for the mask sampler when a pass uses none.

    A sampler the create-info declares has to be bound even where the
    shader never reads it.
    """
    global _no_mask_texture
    if _no_mask_texture is None:
        _no_mask_texture = gpu.types.GPUTexture(
            (1, 1), format='R32F', data=gpu.types.Buffer('FLOAT', 1, [1.0]))
    return _no_mask_texture


def blend_over(backdrop, source, target, size, *, rule=BLEND, mode='MIX',
               opacity=1.0, clip=False, mask=None):
    """Composite *source* over *backdrop* into *target*, and return the framebuffer.

    All three are textures of *size*, holding straight alpha and linear
    colour. *mask* is an `R32F` texture of the same size or None. *rule*
    picks the layer blend or a filter layer's replacement, where *opacity*
    is the filter's Amount and *mode* is unused.

    The framebuffer has to be held alongside its texture until the result
    is used: a `GPUFrameBuffer` does not keep its colour slot alive, and
    reading one whose texture Python has already freed gives zeroes
    rather than an error.
    """
    shader, batch = blend_shader()
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    blend = gpu.state.blend_get()
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')
    gpu.state.depth_mask_set(False)
    gpu.state.face_culling_set('NONE')
    try:
        with framebuffer.bind():
            shader.uniform_float("target_size", (float(size[0]), float(size[1])))
            shader.uniform_float("opacity", float(opacity))
            shader.uniform_float("clip", 1.0 if clip else 0.0)
            shader.uniform_int("mode", BLEND_MODE_IDS.get(mode, 0))
            shader.uniform_int("filter_mix", 1 if rule == FILTER_MIX else 0)
            shader.uniform_int("use_mask", 0 if mask is None else 1)
            shader.uniform_sampler("backdrop", backdrop)
            shader.uniform_sampler("source", source)
            shader.uniform_sampler("mask", _no_mask() if mask is None else mask)
            batch.draw(shader)
    finally:
        gpu.state.blend_set(blend)
    return framebuffer


def release() -> None:
    """Drop the shader and its placeholder before the GPU context goes."""
    global _shader, _batch, _no_mask_texture
    _shader = None
    _batch = None
    _no_mask_texture = None
