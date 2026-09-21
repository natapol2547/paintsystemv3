"""The PNG a filter layer's build packs, checked without a GPU (PS-053).

`filters.png` writes it a band of rows at a time, so the checks are on
what a band boundary could get wrong: the Up filter carried across it,
the rows kept in order, and a file a reader takes. Blender is the reader
that matters, so it reads one too. `tests/test_filter_build.py` checks
the build that feeds it from the GPU.
"""
import os
import struct
import sys
import traceback
import zlib

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section  # noqa: E402

register_addon()
png = import_from("filters.png")

WIDTH, HEIGHT = 37, 23
# Uneven on purpose, one of them a single row.
BANDS = (5, 1, 10, 7)


def chunks(data):
    """``{kind: payload}`` of a PNG, IDAT payloads joined."""
    found = {}
    offset = len(png.SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])
        if crc != zlib.crc32(kind + payload) & 0xFFFFFFFF:
            raise ValueError(f"bad CRC on {kind!r}")
        found[kind] = found.get(kind, b"") + payload
        offset += 12 + length
    return found


def written(picture, bands=BANDS):
    """*picture*, uint8 ``(height, width, 4)`` top row first, as a PNG in *bands*."""
    stream = png.RGBAStream(picture.shape[1], picture.shape[0])
    first = 0
    for rows in bands:
        stream.add(picture[first:first + rows])
        first += rows
    return stream.finish()


def raises(call):
    try:
        call()
    except ValueError:
        return True
    return False


try:
    rng = np.random.default_rng(7)
    picture = rng.integers(0, 256, size=(HEIGHT, WIDTH, 4), dtype=np.uint8)
    data = written(picture)

    section("what the stream writes")
    check(data.startswith(png.SIGNATURE), "a PNG signature")
    parts = chunks(data)
    check(parts[b"IHDR"] == struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0),
          "an 8-bit RGBA header of the size it was given")
    rows = np.frombuffer(zlib.decompress(parts[b"IDAT"]), dtype=np.uint8)
    rows = rows.reshape(HEIGHT, 1 + WIDTH * 4)
    check(bool((rows[:, 0] == png.UP).all()), "every row Up-filtered")
    # Undoing Up is a running sum down the rows, modulo 256.
    decoded = np.cumsum(rows[:, 1:].astype(np.int64), axis=0) % 256
    check(np.array_equal(decoded.reshape(HEIGHT, WIDTH, 4), picture),
          "which decodes to the picture, across every band boundary")
    check(written(picture, (HEIGHT,)) == data,
          "and the file does not depend on where the bands fall")

    section("what Blender reads")
    image = bpy.data.images.new("PNG Stream", 1, 1)
    image.pack(data=data, data_len=len(data))
    image.source = 'FILE'
    check(tuple(image.size) == (WIDTH, HEIGHT), f"the size the file says: {tuple(image.size)}")
    values = np.empty(WIDTH * HEIGHT * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    got = np.rint(values * 255.0).astype(np.uint8).reshape(HEIGHT, WIDTH, 4)
    check(np.array_equal(got[::-1], picture),
          "every byte, the first row written at the top of the image")
    bpy.data.images.remove(image)

    section("what it refuses")
    stream = png.RGBAStream(WIDTH, HEIGHT)
    check(raises(lambda: stream.add(picture[:, :-1])), "a band of another width")
    stream.add(picture[:4])
    check(raises(stream.finish), "finishing before every row is in")
except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("FILTER PNG TEST")
