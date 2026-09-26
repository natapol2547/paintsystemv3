"""GLSL for the selection mask passes in `raster.py`.

`raster._mask_shader` declares the push constants, samplers and outputs
these sources read and write, and `raster._resources` compiles each
fragment source twice: once for coverage and once, with
`SIGNED_DISTANCE` set, for the signed distance a `VIEW` op's outline
needs. The raster module docstring describes the maths.
"""
from . import outline

# GLSL shared with view_raster's texel pass. `edge_profile` turns a signed
# distance s from an edge into coverage, `combine` merges an op's coverage
# with the previous mask, and `previous_at` reads the previous mask. Both
# shaders declare the `mode` and `use_previous` push constants and the
# `previous` sampler these functions read.
COMMON_SOURCE = """
float edge_profile(float s, float edge_half_width)
{
  if (edge_half_width <= 0.0) {
    return s > 0.0 ? 1.0 : 0.0;
  }
  float t = clamp((s + edge_half_width) / (2.0 * edge_half_width), 0.0, 1.0);
  return t * t * (3.0 - 2.0 * t);
}

float combine(float previous_value, float coverage)
{
  if (mode == 0) {
    return coverage;
  }
  if (mode == 1) {
    return max(previous_value, coverage);
  }
  if (mode == 2) {
    return min(previous_value, 1.0 - coverage);
  }
  return min(previous_value, coverage);
}

float previous_at(ivec2 texel)
{
  return use_previous != 0 ? texelFetch(previous, texel, 0).r : 0.0;
}
"""

# `shape` holds one of raster's `_SHAPE_*` codes: 0 everything, 1 box,
# 2 ellipse, 3 invert, 4 nothing.
SHAPE_FRAGMENT_SOURCE = COMMON_SOURCE + """
/* Eberly, "Distance from a Point to an Ellipse, an Ellipsoid, or a
   Hyperellipsoid". The root is bracketed in u = s + 1 instead of s, so
   the bracket never needs a value of s near -1, which float32 cannot
   hold precisely. */
float ellipse_root(float r0_minus_1, float n0, float z1, float g)
{
  float u0 = z1;
  float u1 = g < 0.0 ? 1.0 : length(vec2(n0, z1));
  float u = u0;
  for (int i = 0; i < 160; i++) {
    u = 0.5 * (u0 + u1);
    if (u == u0 || u == u1) {
      break;
    }
    float ratio0 = n0 / (u + r0_minus_1);
    float ratio1 = z1 / u;
    float value = ratio0 * ratio0 + ratio1 * ratio1 - 1.0;
    if (value > 0.0) {
      u0 = u;
    }
    else if (value < 0.0) {
      u1 = u;
    }
    else {
      break;
    }
  }
  return u;
}

/* Unsigned distance from (y0, y1) >= 0 to the ellipse with radii
   e0 >= e1 > 0. */
float ellipse_distance(float e0, float e1, float y0, float y1)
{
  float r0_minus_1 = (e0 - e1) * (e0 + e1) / (e1 * e1);
  if (y1 > 0.0) {
    if (y0 > 0.0) {
      float z0 = y0 / e0;
      float z1 = y1 / e1;
      float g = z0 * z0 + z1 * z1 - 1.0;
      if (g == 0.0) {
        return 0.0;
      }
      float u = ellipse_root(r0_minus_1, (r0_minus_1 + 1.0) * z0, z1, g);
      return abs(1.0 - u) * length(vec2(y0 / (u + r0_minus_1), y1 / u));
    }
    return abs(y1 - e1);
  }
  float numer0 = e0 * y0;
  float denom0 = (e0 - e1) * (e0 + e1);
  if (numer0 < denom0) {
    float xde0 = numer0 / denom0;
    return length(vec2(e0 * xde0 - y0, e1 * sqrt(1.0 - xde0 * xde0)));
  }
  return abs(y0 - e0);
}

void main()
{
  ivec2 texel = ivec2(floor(v_texel));
  if (shape == 3) {
    out_mask = 1.0 - previous_at(texel);
    return;
  }
  float previous_value = mode == 0 ? 0.0 : previous_at(texel);
  float coverage = 1.0;
  if (shape == 4) {
    coverage = 0.0;
  }
  else if (shape == 1) {
    /* Box from shape_whole.xy + shape_fraction.xy to
       shape_whole.zw + shape_fraction.zw. */
    vec2 below = (shape_whole.xy - vec2(texel)) + (shape_fraction.xy - 0.5);
    vec2 above = (vec2(texel) - shape_whole.zw) + (0.5 - shape_fraction.zw);
    vec2 q = max(below, above);
    float outside = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0);
    if (SIGNED_DISTANCE) {
      out_mask = clamp(-outside, -half_width, half_width);
      return;
    }
    coverage = edge_profile(-outside, half_width);
  }
  else if (shape == 2) {
    /* Ellipse with its centre in xy and its radii in zw. */
    vec2 y = abs((shape_whole.xy - vec2(texel)) + (shape_fraction.xy - 0.5));
    vec2 e = shape_whole.zw + shape_fraction.zw;
    if (e.x < e.y) {
      y = y.yx;
      e = e.yx;
    }
    float z0 = y.x / e.x;
    float z1 = y.y / e.y;
    /* The ellipse scaled about its centre to pass through the texel stays
       at least |rho - 1| * e1 from the outline. So a texel whose bound
       reaches the half width needs no exact distance. */
    float bound = abs(length(vec2(z0, z1)) - 1.0) * e.y;
    if (bound >= half_width) {
      coverage = z0 * z0 + z1 * z1 < 1.0 ? 1.0 : 0.0;
    }
    else {
      float d = ellipse_distance(e.x, e.y, y.x, y.y);
      if (SIGNED_DISTANCE) {
        out_mask = clamp(z0 * z0 + z1 * z1 < 1.0 ? d : -d, -half_width, half_width);
        return;
      }
      coverage = edge_profile(z0 * z0 + z1 * z1 < 1.0 ? d : -d, half_width);
    }
  }
  if (SIGNED_DISTANCE) {
    /* At least half_width from the outline, or no shape at all. */
    out_mask = coverage > 0.5 ? half_width : -half_width;
    return;
  }
  out_mask = combine(previous_value, coverage);
}
"""

LASSO_FRAGMENT_SOURCE = COMMON_SOURCE + """
ivec2 data_texel(int index)
{
  return ivec2(index % DATA_WIDTH, index / DATA_WIDTH);
}

float segment_distance(vec2 p, vec2 a, vec2 b)
{
  vec2 ab = b - a;
  vec2 ap = p - a;
  float length2 = dot(ab, ab);
  float t = length2 > 0.0 ? clamp(dot(ap, ab) / length2, 0.0, 1.0) : 0.0;
  return length(ap - ab * t);
}

void main()
{
  ivec2 texel = ivec2(floor(v_texel));
  float previous_value = mode == 0 ? 0.0 : previous_at(texel);

  vec4 span = texelFetch(row_spans, ivec2(texel.x / span_width, texel.y), 0);
  int start = int(span.r);
  int packed_count = int(span.g);
  int count = packed_count / 2;
  int crossings = packed_count % 2;
  float x = float(texel.x);
  for (int i = 0; i < count; i++) {
    if (texelFetch(keys, data_texel(start + i), 0).r > x) {
      break;
    }
    crossings++;
  }
  bool inside = (crossings % 2) == 1;

  float coverage;
  if (half_width <= 0.0 && !SIGNED_DISTANCE) {
    coverage = inside ? 1.0 : 0.0;
  }
  else {
    ivec2 cell = texel / cell_size;
    vec4 record = texelFetch(cells, cell, 0);
    int offset = int(record.r);
    int list_length = int(record.g);
    vec2 local = vec2(texel - cell * cell_size) + 0.5;
    float from_centre = length(local - 0.5 * float(cell_size));
    float d = half_width;
    for (int i = 0; i < list_length; i++) {
      ivec2 at = data_texel(offset + i);
      /* Entries are sorted by distance from the cell centre, so no later
         entry can be closer than d. */
      if (texelFetch(bounds, at, 0).r - from_centre >= d) {
        break;
      }
      vec4 segment = texelFetch(entries, at, 0);
      d = min(d, segment_distance(local, segment.xy, segment.zw));
    }
    if (SIGNED_DISTANCE) {
      out_mask = inside ? d : -d;
      return;
    }
    coverage = edge_profile(inside ? d : -d, half_width);
  }
  out_mask = combine(previous_value, coverage);
}
""".replace("DATA_WIDTH", str(outline.DATA_WIDTH))

QUANTISE_VERTEX_SOURCE = """
void main()
{
  gl_Position = vec4(position * 2.0 - 1.0, 0.0, 1.0);
}
"""

QUANTISE_FRAGMENT_SOURCE = """
void main()
{
  out_value = floor(texelFetch(mask, ivec2(gl_FragCoord.xy), 0).r * 255.0 + 0.5) / 255.0;
}
"""
