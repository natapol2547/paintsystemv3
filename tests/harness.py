"""Shared helpers for the Blender-side tests. Not a test itself.

Every test file does::

    from harness import *          # noqa: F401,F403
    register_addon()
    ...
    finish("name of test")

The addon package is imported from the repository checkout, whatever the
folder is called, so tests do not depend on the extension id.
"""
import importlib
import os
import sys
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.basename(REPO)
VERSION = tuple(bpy.app.version)
BACKGROUND = bpy.app.background

_failures = []
_checks = 0
_addon = None


def since(major, minor=0, patch=0):
    """True when the running Blender is at least the given version."""
    return VERSION >= (major, minor, patch)


def before(major, minor=0, patch=0):
    return VERSION < (major, minor, patch)


def register_addon():
    """Import the repository as a package and register it once."""
    global _addon
    if _addon is not None:
        return _addon
    parent = os.path.dirname(REPO)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    _addon = importlib.import_module(PACKAGE)
    _addon.register()
    return _addon


def import_from(relpath):
    """Import a submodule of the addon, e.g. ``import_from("compiler.core")``."""
    return importlib.import_module(f"{PACKAGE}.{relpath}")


def check(cond, msg):
    global _checks
    _checks += 1
    status = "ok  " if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        _failures.append(msg)
    return bool(cond)


def fail(msg):
    return check(False, msg)


def section(title):
    print(f"\n== {title}")


def guarded(fn):
    """Run ``fn`` and record an exception as a failure instead of aborting."""
    try:
        fn()
    except Exception:
        traceback.print_exc()
        _failures.append(f"exception in {getattr(fn, '__name__', fn)}")


def summary(name):
    print()
    print(f"Blender {bpy.app.version_string}, {_checks} checks")
    if _failures:
        print(f"{name} FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"{name} PASSED")
    return 0


def finish(name):
    """Print the summary and exit the process with a status code."""
    code = summary(name)
    sys.stdout.flush()
    sys.stderr.flush()
    if BACKGROUND:
        sys.exit(code)
    # In a windowed session sys.exit only unwinds the script; the window
    # loop keeps running. Exit hard so the shell sees the status.
    os._exit(code)
