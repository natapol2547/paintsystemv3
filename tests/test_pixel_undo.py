"""Scripted pixel writes undo and redo with Blender's own steps (PS-090).

`undo.pixels.write_pixels` puts a `foreach_set` write into an image undo
step, the kind a paint stroke pushes, so Blender orders it against strokes
and memfile steps itself.

Two things make this runnable in background mode. Blender builds the undo
stack only when something pushes to it, and `image.invert` dereferences the
stack without a null check, so `ensure_undo_stack` seeds it first. And a
native stroke needs a paint context no background session has, so the
stand-in for "a step Blender pushed" is a real `image.invert`: the same
operator, the same undo code path, actually changing pixels.

Every datablock is fetched by name after an undo; Python references to an
ID may dangle once the undo system has restored over it.
"""
import os
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, close, finish, fmt, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
pixels = import_from("undo.pixels")

SIZE = 64
BIG = 4096
WHITE = (1.0, 1.0, 1.0, 1.0)
RED = (1.0, 0.0, 0.0, 1.0)
GREEN = (0.0, 1.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)


def flat(color, size=SIZE):
    return np.tile(np.asarray(color, dtype=np.float32), size * size)


def new_image(name, color=WHITE, size=SIZE):
    image = bpy.data.images.new(name, size, size, alpha=True)
    image.pixels.foreach_set(flat(color, size))
    image.update()
    return image


def first_pixel(name, size=SIZE):
    """The bottom-left pixel of the image called *name*, or None if it is gone."""
    image = bpy.data.images.get(name)
    if image is None:
        return None
    buf = np.empty(size * size * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    return tuple(float(v) for v in buf[:4])


def native_invert(name):
    """A real pixel edit by a Blender operator: one image undo step."""
    with bpy.context.temp_override(edit_image=bpy.data.images[name]):
        bpy.ops.image.invert(invert_r=True, invert_g=True, invert_b=True)


def undo():
    bpy.ops.ed.undo()


def redo():
    bpy.ops.ed.redo()


def test_one_write_one_step():
    section("one write, one Ctrl+Z")
    name = "PS Undo Write"
    new_image(name)
    bpy.ops.ed.undo_push(message="image exists")

    check(pixels.write_pixels(bpy.data.images[name], flat(RED)), "write_pixels reports the write registered")
    check(close(first_pixel(name), RED), f"the write landed {fmt(first_pixel(name))}")
    check(bpy.data.images[name].is_dirty, "the write marks the image dirty for PS-056")

    undo()
    got = first_pixel(name)
    check(close(got, WHITE), f"one undo restores the pixels from before the write {fmt(got)}")
    redo()
    got = first_pixel(name)
    check(close(got, RED), f"one redo brings the write back {fmt(got)}")


def test_second_write_costs_one_step():
    section("a second write on the same image")
    name = "PS Undo Second"
    new_image(name)
    bpy.ops.ed.undo_push(message="image exists")

    pixels.write_pixels(bpy.data.images[name], flat(RED))
    pixels.write_pixels(bpy.data.images[name], flat(GREEN))
    check(close(first_pixel(name), GREEN), f"the second write landed {fmt(first_pixel(name))}")

    undo()
    got = first_pixel(name)
    check(close(got, RED), f"one undo goes back to the first write, not past it {fmt(got)}")
    undo()
    got = first_pixel(name)
    check(close(got, WHITE), f"the next undo goes back past the first write {fmt(got)}")
    redo()
    redo()
    got = first_pixel(name)
    check(close(got, GREEN), f"two redos return to the second write {fmt(got)}")


def test_interleaved_with_native_steps():
    section("a scripted write, a step Blender pushed, and a document change")
    scripted, native = "PS Undo Scripted", "PS Undo Native"
    new_image(scripted)
    new_image(native)
    tree = bpy.data.node_groups.new("PS Undo Doc", 'ShaderNodeTree')
    tree["revision"] = 0
    bpy.ops.ed.undo_push(message="images exist")

    pixels.write_pixels(bpy.data.images[scripted], flat(RED))
    native_invert(native)
    bpy.data.node_groups["PS Undo Doc"]["revision"] = 1
    bpy.ops.ed.undo_push(message="document change")

    def revision():
        doc = bpy.data.node_groups.get("PS Undo Doc")
        return None if doc is None else doc.get("revision")

    check(revision() == 1 and close(first_pixel(scripted), RED) and close(first_pixel(native), (0, 0, 0, 1)),
          "all three edits are in place")

    undo()
    check(revision() == 0, f"undo 1 takes back the document change, revision {revision()}")
    check(close(first_pixel(native), (0, 0, 0, 1)) and close(first_pixel(scripted), RED),
          "undo 1 leaves both images alone")

    undo()
    got = first_pixel(native)
    check(close(got, WHITE), f"undo 2 takes back the step Blender pushed {fmt(got)}")
    check(close(first_pixel(scripted), RED), "undo 2 leaves the scripted write alone")

    undo()
    got = first_pixel(scripted)
    check(close(got, WHITE), f"undo 3 takes back the scripted write {fmt(got)}")

    for _ in range(3):
        redo()
    check(revision() == 1 and close(first_pixel(scripted), RED) and close(first_pixel(native), (0, 0, 0, 1)),
          "three redos put all three edits back")


def test_native_step_after_a_scripted_write():
    section("undoing a native step made after a scripted write")
    name = "PS Undo Keep"
    new_image(name)
    bpy.ops.ed.undo_push(message="image exists")

    pixels.write_pixels(bpy.data.images[name], flat(RED))
    native_invert(name)
    check(close(first_pixel(name), (0, 1, 1, 1)), f"the native step inverted the written pixels {fmt(first_pixel(name))}")

    undo()
    got = first_pixel(name)
    check(close(got, RED), f"undoing it leaves the scripted write in place {fmt(got)}")


def test_write_once_image_survives_undo_past_creation():
    section("a write-once image packed after its write")
    bpy.ops.ed.undo_push(message="before the write-once images")
    packed = new_image("PS Undo Packed", BLUE)
    new_image("PS Undo Loose", BLUE)
    check(pixels.pack_write_once(packed), "pack_write_once packed the image")
    bpy.ops.ed.undo_push(message="write-once images exist")

    undo()
    check(first_pixel("PS Undo Packed") is None, "undo past their creation removes them")
    redo()
    got = first_pixel("PS Undo Packed")
    check(got is not None and close(got, BLUE), f"redo brings the packed image back with its pixels {fmt(got or ())}")
    got = first_pixel("PS Undo Loose")
    check(got is not None and not close(got, BLUE),
          f"an unpacked image comes back blank, which is why write-once images are packed {fmt(got or ())}")


def test_write_pixels_checks_its_input():
    section("input checks")
    image = new_image("PS Undo Input")
    try:
        pixels.write_pixels(image, np.zeros(16, dtype=np.float32))
        check(False, "a wrong-sized buffer raises ValueError")
    except ValueError as error:
        check("takes" in str(error), f"a wrong-sized buffer raises ValueError: {error}")


def test_registration_cost():
    section("cost of a registration at 4K")
    image = bpy.data.images.new("PS Undo Cost", BIG, BIG, alpha=True)
    image.pixels.foreach_set(flat(RED, BIG))
    image.update()
    start = time.perf_counter()
    pixels.push_undo_step(image)
    elapsed = 1000 * (time.perf_counter() - start)
    print(f"  4K byte image registration: {elapsed:.0f} ms")
    check(elapsed < 500, f"a registration on a 4K byte image takes {elapsed:.0f} ms")
    bpy.data.images.remove(image)


for test in (test_one_write_one_step,
             test_second_write_costs_one_step,
             test_interleaved_with_native_steps,
             test_native_step_after_a_scripted_write,
             test_write_once_image_survives_undo_past_creation,
             test_write_pixels_checks_its_input,
             test_registration_cost):
    guarded(test)

finish("PIXEL UNDO TEST")
