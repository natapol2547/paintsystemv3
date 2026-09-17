"""Check that every icon the add-on names exists in the running Blender.

Blender renames icons between versions, and forks such as Bforartists ship
a different icon set. An unknown name in a layout call raises while the
panel draws, and an unknown ``bl_icon`` on a node or node tree class makes
``register_class`` raise. The add-on therefore resolves icon names at run
time through ``common.icon_kwargs`` and ``common.blender_icon``, which fall
back to later names and finally to no icon. This test parses the add-on
source with ``ast`` and checks every name it finds against this Blender, so
a rename shows up here rather than as a missing icon:

- ``icon_kwargs(...)`` and ``ps_icon`` tuples list names in fallback order;
  at least one must be a Blender icon or a file in ``icons/``.
- ``bl_icon`` on a node or node tree class is ``blender_icon(...)``; at
  least one name must be in the ``bl_icon`` enum of the ``bpy.types`` class
  it derives from.
- ``bl_icon`` on a ``WorkSpaceTool`` names a ``.dat`` file in Blender's
  datafiles icons folder. Blender only prints a warning for a missing one
  when the toolbar draws, so it stays a literal and this test catches it.
- ``icon=`` with a literal name fails, as it bypasses the fallback. The
  exceptions are ``icon='NONE'`` and the sites in ``LITERAL_ICON_SITES``,
  which the layout API gives no ``icon_value`` alternative; their names
  must still be in the ``UILayout`` icon enum.

A value that is not a string literal is resolved to the literals it can
take, by these rules only:

- ``a if condition else b`` takes the values of both branches.
- A name takes the values of its only assignment in the enclosing
  function, or failing that at module level.
- A dict literal takes the values of its entries, and ``d[key]`` or
  ``d.get(key, default)`` takes the values of ``d`` plus ``default``.
- ``icon_kwargs(*x.ps_icon)`` is covered by the ``ps_icon`` check.

Any other expression fails the test with its file and line: extend the
rules here or write the call site so that they apply.

Run:  blender -b --factory-startup --python tests/test_icons.py
"""
import ast
import itertools
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REPO, check, fail, finish, section  # noqa: E402

SKIP_DIRS = {"tests", "docs", "icons", "__pycache__", "dist", "build"}

# (file, icon name) -> why the call cannot take ``**icon_kwargs(...)``.
# Empty while every icon the add-on draws goes through a layout call that
# accepts ``icon_value``.
LITERAL_ICON_SITES = {}


class Unresolved(Exception):
    """An icon expression the rules in the module docstring cannot resolve."""

    def __init__(self, node, reason):
        super().__init__(reason)
        self.node = node


class Source:
    """One parsed add-on file and the static resolution of its icon expressions."""

    def __init__(self, path):
        self.rel = os.path.relpath(path, REPO)
        with open(path, encoding="utf-8") as fh:
            self.tree = ast.parse(fh.read(), filename=path)
        self.parents = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent

    def _functions_around(self, node):
        parent = self.parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                yield parent
            parent = self.parents.get(parent)

    def _assignments(self, statements, name):
        values = []
        for stmt in statements:
            if isinstance(stmt, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
                values.append(stmt.value)
            elif (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                  and stmt.target.id == name and stmt.value is not None):
                values.append(stmt.value)
        return values

    def definition(self, node):
        """The expression a name is assigned, following names; other nodes are returned as they are."""
        seen = set()
        while isinstance(node, ast.Name):
            if node.id in seen:
                raise Unresolved(node, f"name {node.id!r} refers to itself")
            seen.add(node.id)
            values = None
            for function in self._functions_around(node):
                arguments = function.args
                parameters = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
                parameters += [a for a in (arguments.vararg, arguments.kwarg) if a is not None]
                if any(a.arg == node.id for a in parameters):
                    raise Unresolved(node, f"{node.id!r} is a function parameter")
                values = self._assignments(ast.walk(function), node.id)
                if values:
                    break
            if not values:
                values = self._assignments(self.tree.body, node.id)
            if len(values) != 1:
                raise Unresolved(node, f"{node.id!r} needs exactly one assignment, found {len(values)}")
            node = values[0]
        return node

    def resolve(self, node):
        """The set of string literals *node* can evaluate to."""
        node = self.definition(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return self.resolve(node.body) | self.resolve(node.orelse)
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise Unresolved(node, "dict literal with ** unpacking")
            return set().union(*(self.resolve(value) for value in node.values))
        if isinstance(node, ast.Subscript):
            return self._dict_values(node.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and len(node.args) in (1, 2) and not node.keywords):
            values = self._dict_values(node.func.value)
            if len(node.args) == 2:
                values |= self.resolve(node.args[1])
            return values
        raise Unresolved(node, f"cannot resolve {ast.unparse(node)!r} to string literals")

    def _dict_values(self, node):
        target = self.definition(node)
        if not isinstance(target, ast.Dict):
            raise Unresolved(node, f"{ast.unparse(node)!r} is not a dict literal")
        return self.resolve(target)

    def fallback_lists(self, nodes):
        """Every name list a sequence of icon expressions can produce, one per combination."""
        return [list(names) for names in itertools.product(*(sorted(self.resolve(n)) for n in nodes))]


def call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def addon_sources():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(files):
            if f.endswith(".py"):
                yield Source(os.path.join(root, f))


# --- what this Blender has ------------------------------------------------

def enum_names(prop):
    return set(prop.enum_items.keys())


BLENDER_ICONS = enum_names(bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon'])
ADDON_ICONS = {os.path.splitext(f)[0] for f in os.listdir(os.path.join(REPO, "icons"))}
TOOL_ICON_DIR = bpy.utils.system_resource('DATAFILES', path="icons")
TOOL_ICONS = ({os.path.splitext(f)[0] for f in os.listdir(TOOL_ICON_DIR) if f.endswith(".dat")}
              if TOOL_ICON_DIR and os.path.isdir(TOOL_ICON_DIR) else set())


# --- collection -------------------------------------------------------------

class Report:
    def __init__(self):
        self.counts = {}
        self.problems = []
        # Names this Blender lacks where a later name in the list stands in.
        self.fallbacks = []

    def count(self, kind):
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def problem(self, source, node, message):
        self.problems.append((source.rel, node.lineno, message))

    def sorted_problems(self):
        return [f"{rel}:{line} {message}" for rel, line, message in sorted(self.problems)]


def check_enum_names(report, source, node, names, enum, enum_label):
    for name in sorted(names):
        report.count(enum_label)
        if name not in enum:
            report.problem(source, node, f"{name!r} is not in the {enum_label} enum")


def check_fallbacks(report, source, node, lists, label, available, available_label):
    for names in lists:
        report.count(label)
        if not any(name in available for name in names):
            report.problem(source, node, f"{label} {names}: none is {available_label}")
        elif names[0] not in available:
            fallback = next(name for name in names if name in available)
            report.fallbacks.append((source.rel, node.lineno, f"{names[0]!r} is missing, {fallback!r} is used"))


def check_literal_icon(report, source, keyword):
    names = source.resolve(keyword.value) - {'NONE'}
    for name in sorted(names):
        if (source.rel, name) not in LITERAL_ICON_SITES:
            report.problem(source, keyword, f"icon={name!r} bypasses the fallback: use **icon_kwargs({name!r})")
    check_enum_names(report, source, keyword, names, BLENDER_ICONS, "UILayout icon")


def is_bl_icon_value(source, node):
    parent = source.parents.get(node)
    return (isinstance(parent, ast.Assign) and parent.value is node
            and any(isinstance(t, ast.Name) and t.id == "bl_icon" for t in parent.targets)
            and isinstance(source.parents.get(parent), ast.ClassDef))


def scan_call(report, source, call):
    for keyword in call.keywords:
        if keyword.arg == "icon":
            check_literal_icon(report, source, keyword)
    if call_name(call) == "blender_icon" and not is_bl_icon_value(source, call):
        raise Unresolved(call, "blender_icon is only for bl_icon class attributes; layout calls use icon_kwargs")
    if call_name(call) != "icon_kwargs":
        return
    starred = [arg for arg in call.args if isinstance(arg, ast.Starred)]
    if starred:
        if len(call.args) == 1 and isinstance(starred[0].value, ast.Attribute) and starred[0].value.attr == "ps_icon":
            return
        raise Unresolved(call, "icon_kwargs with an unpacked argument other than a ps_icon tuple")
    check_fallbacks(report, source, call, source.fallback_lists(call.args), "icon_kwargs",
                    BLENDER_ICONS | ADDON_ICONS, "a Blender icon or a file in icons/")


def rna_base(source, cls, addon_classes):
    """The ``bpy.types`` class *cls* derives from that defines ``bl_icon``."""
    for base in cls.bases:
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if name is None or name in addon_classes:
            continue
        rna_type = getattr(bpy.types, name, None)
        if rna_type is not None and "bl_icon" in rna_type.bl_rna.properties:
            return name, rna_type
    raise Unresolved(cls, f"class {cls.name} sets bl_icon but no bpy.types base with bl_icon was found")


def scan_class(report, source, cls, addon_classes):
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            targets = [stmt.target.id]
        else:
            continue
        if "ps_icon" in targets:
            value = source.definition(stmt.value)
            if not isinstance(value, ast.Tuple):
                raise Unresolved(stmt, f"ps_icon of {cls.name} is not a tuple literal")
            check_fallbacks(report, source, stmt, source.fallback_lists(value.elts), "ps_icon",
                            BLENDER_ICONS | ADDON_ICONS, "a Blender icon or a file in icons/")
        if "bl_icon" in targets:
            if any(getattr(b, "id", getattr(b, "attr", None)) == "WorkSpaceTool" for b in cls.bases):
                for name in sorted(source.resolve(stmt.value)):
                    report.count("WorkSpaceTool icon")
                    if name not in TOOL_ICONS:
                        report.problem(source, stmt, f"{name!r} has no .dat file in {TOOL_ICON_DIR}")
                continue
            base_name, rna_type = rna_base(source, cls, addon_classes)
            value = stmt.value
            if not (isinstance(value, ast.Call) and call_name(value) == "blender_icon"):
                report.problem(source, stmt, f"bl_icon of {cls.name} bypasses the fallback: use blender_icon(...)")
                continue
            if value.keywords or any(isinstance(arg, ast.Starred) for arg in value.args):
                raise Unresolved(value, "blender_icon takes icon names as plain positional arguments")
            check_fallbacks(report, source, stmt, source.fallback_lists(value.args), "blender_icon",
                            enum_names(rna_type.bl_rna.properties["bl_icon"]), f"in the {base_name}.bl_icon enum")


def scan(sources):
    report = Report()
    addon_classes = {node.name for s in sources for node in ast.walk(s.tree) if isinstance(node, ast.ClassDef)}
    for source in sources:
        for node in ast.walk(source.tree):
            try:
                if isinstance(node, ast.Call):
                    scan_call(report, source, node)
                elif isinstance(node, ast.ClassDef):
                    scan_class(report, source, node, addon_classes)
            except Unresolved as error:
                report.problem(source, error.node,
                               f"unresolved icon reference (used at line {node.lineno}): {error}")
    return report


# --- run --------------------------------------------------------------------

section(f"icons named by the add-on on Blender {bpy.app.version_string}")
sources = list(addon_sources())
report = scan(sources)
total = sum(report.counts.values())
print(f"  Blender {bpy.app.version_string}: {total} icon references checked in {len(sources)} files")
for kind, number in sorted(report.counts.items()):
    print(f"    {kind}: {number}")
check(len(BLENDER_ICONS) > 0, f"this Blender has {len(BLENDER_ICONS)} UILayout icons")
check(len(TOOL_ICONS) > 0, f"this Blender has {len(TOOL_ICONS)} tool icons in {TOOL_ICON_DIR}")
for kind in ("icon_kwargs", "ps_icon", "blender_icon", "WorkSpaceTool icon"):
    check(report.counts.get(kind, 0) > 0, f"the scan found {kind} references")
for rel, line, message in sorted(report.fallbacks):
    print(f"  [fallback] {rel}:{line} {message}")
for problem in report.sorted_problems():
    fail(problem)
check(not report.problems, f"{len(report.problems)} missing or unresolved icon references")

finish("icons")
