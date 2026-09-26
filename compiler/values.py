"""Compare Python values with the values Blender stores.

A write to a node tree tags the tree and every material that uses it, even
when the value does not change. So code that writes node properties,
socket values or locations first checks with ``same_value`` whether RNA
already holds the value.
"""
from __future__ import annotations

import struct
from typing import Any

import bpy

_pack_float32 = struct.Struct('f').pack
_unpack_float32 = struct.Struct('f').unpack


def as_float32(value: float) -> float:
    """Round *value* to float32 precision, the way Blender stores floats."""
    try:
        return _unpack_float32(_pack_float32(value))[0]
    except (OverflowError, struct.error):
        return value


def _same_id(current: Any, desired: Any) -> bool:
    """True when both values point to the same datablock, or both are None.

    Two Python wrappers of one datablock are different objects, so they are
    compared by memory address.
    """
    if current is None or desired is None:
        return current is None and desired is None
    if not (isinstance(current, bpy.types.ID) and isinstance(desired, bpy.types.ID)):
        return False
    return current.as_pointer() == desired.as_pointer()


def same_value(current: Any, desired: Any) -> bool:
    """True when writing *desired* over *current* would store the same value.

    Blender stores floats as float32, so *desired* is rounded to float32
    before the comparison. Otherwise ``0.1``, which reads back as
    ``0.10000000149011612``, would look changed on every build. Any
    difference that float32 can hold still compares unequal. Datablocks
    compare by identity, and sequences compare element by element.

    A wrong False only costs an extra write. A wrong True would leave the
    artifact out of date for good. So any type not handled here gives
    False.
    """
    if isinstance(desired, str):
        return isinstance(current, str) and current == desired
    if isinstance(desired, (bool, int, float)):
        if not isinstance(current, (bool, int, float)):
            return False
        if isinstance(current, float) or isinstance(desired, float):
            return current == as_float32(desired)
        return current == desired
    if desired is None:
        return current is None
    if isinstance(desired, bpy.types.ID) or isinstance(current, bpy.types.ID):
        return _same_id(current, desired)
    if isinstance(desired, (set, frozenset)):
        try:
            return set(current) == set(desired)
        except TypeError:
            return False
    try:
        desired_items = list(desired)
        current_items = list(current)
    except TypeError:
        return False
    if len(current_items) != len(desired_items):
        return False
    return all(same_value(c, d) for c, d in zip(current_items, desired_items))
