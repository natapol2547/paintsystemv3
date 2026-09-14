from .core import (
    compile_tree,
    build_ir,
    mark_dirty,
    flush_now,
    suspend_compile,
    ps_trees,
    cleanup_orphan_artifacts,
    normalize_tree,
    CompileContext,
)
from .ir import IR, Ref
from . import core as _core


def register():
    _core.reset_state()


def unregister():
    _core.reset_state()
