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
- ``<gizmo>.icon`` is ``blender_icon(...)``; at least one name must be a
  Blender icon. A gizmo takes an icon name rather than an icon id before
  4.5, so it cannot take an add-on icon and does not go through
  ``icon_kwargs``.
- ``blender_icon(...)`` as the fourth entry of a tuple whose first three
  entries are string literals is an ``EnumProperty`` item's icon; at least
  one name must be a Blender icon. ``register_class`` accepts an unknown
  name there and the item draws without an icon, so a rename would go
  unnoticed.
- ``bl_icon`` on a ``WorkSpaceTool`` names a ``.dat`` file in Blender's
  datafiles icons folder. Blender only prints a warning for a missing one
  when the toolbar draws, so it stays a literal and this test catches it.
- ``bl_icon`` and ``ps_icon`` are plain assignments directly in the class
  body. Setting them anywhere else, under an ``if`` in the class body, on
  the class object or through ``setattr``, fails.
- ``icon=`` with a literal name fails, as it bypasses the fallback. The
  exceptions are ``icon='NONE'`` and the sites in ``LITERAL_ICON_SITES``,
  which the layout API gives no ``icon_value`` alternative; their names
  must still be in the ``UILayout`` icon enum.
- ``**mapping`` in a ``UILayout`` call that takes an icon is
  ``icon_kwargs(...)`` or a dict literal without an ``icon`` or
  ``icon_value`` key.
- ``icon_value=`` fails with a string literal, and with ``get_icon(name)``
  when ``icons/`` has no file of that name. Other ``icon_value``
  expressions, such as an icon id handed to ``UIList.draw_item``, are not
  checked.
- ``icon_kwargs`` and ``blender_icon`` are found by name, so importing
  them under another name or using them other than by calling them fails.

A value that is not a string literal is resolved to the literals it can
take, by these rules only:

- ``a if condition else b`` takes the values of both branches.
- A name takes the value of its one binding in the innermost function that
  binds it, or failing that at module level. That binding must be a plain
  assignment and the only one in that scope, counting loop, ``with``,
  unpacking, augmented and walrus targets, imports, ``global`` and
  ``nonlocal`` rebinding from nested functions. A function parameter
  fails. The object must not be changed in place in that scope, by
  ``name[key] = value`` or ``name.update(...)``.
- A module-level name bound by a relative ``from ... import`` of another
  add-on module takes the value of that module's binding of the name, by
  the same rule. Neither module may change the object in place.
- A dict literal takes the values of its entries, and ``d[key]`` or
  ``d.get(key, default)`` takes the values of ``d`` plus ``default``.
- ``icon_kwargs(*x.ps_icon)`` is covered by the ``ps_icon`` check.

Any other expression fails the test with its file and line: extend the
rules here or write the call site so that they apply.

Not covered: icon names written as literals in ``EnumProperty`` items,
which the add-on does not use, and changes made through another reference
to the same object.

The last section scans planted faults, so a gap in these rules fails the
test on every Blender rather than going unnoticed.

Run:  blender -b --factory-startup --python tests/test_icons.py
"""
import ast
import itertools
import os
import sys
import tempfile

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REPO, check, fail, finish, section  # noqa: E402

# Top-level folders that hold no add-on code. Hidden folders and
# __pycache__ are skipped at any depth.
SKIP_DIRS = {"tests", "docs", "icons", "dist", "build"}

# (file, icon name) -> why the call cannot take ``**icon_kwargs(...)``.
# Empty while every icon the add-on draws goes through a layout call that
# accepts ``icon_value``.
LITERAL_ICON_SITES = {}

# Helpers the scan finds by the name they are called with.
ICON_HELPERS = ("icon_kwargs", "blender_icon")

# Class attributes that name icons.
ICON_ATTRIBUTES = ("bl_icon", "ps_icon")

# Attribute set on an object, not in a class body, that names an icon.
GIZMO_ICON_ATTRIBUTE = "icon"

# Methods that add entries to a dict in place.
DICT_MUTATORS = {"update", "setdefault", "__setitem__", "__ior__"}

SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


class Unresolved(Exception):
    """An icon expression the rules in the module docstring cannot resolve.

    *source* is the file *node* is in. The `Source` whose resolution fails
    fills it in, since resolution can follow an import into another file.
    """

    def __init__(self, node, reason, source=None):
        super().__init__(reason)
        self.node = node
        self.source = source


def scope_nodes(scope):
    """The nodes in *scope*, without descending into nested functions or classes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPE_NODES):
            stack.extend(ast.iter_child_nodes(node))


class Source:
    """One parsed add-on file and the static resolution of its icon expressions."""

    def __init__(self, rel, text):
        self.rel = rel
        self.tree = ast.parse(text, filename=rel)
        self.parents = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        # The scanned files by their relative path, which imports resolve
        # against. ``scan`` fills it in.
        self.modules = {}

    def _functions_around(self, node):
        parent = self.parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                yield parent
            parent = self.parents.get(parent)

    @staticmethod
    def _bindings(scope, name):
        """How many times *scope* binds *name*, counting rebinding through ``global`` or ``nonlocal``."""
        count = 0
        for node in scope_nodes(scope):
            if isinstance(node, ast.Name):
                count += node.id == name and isinstance(node.ctx, ast.Store)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                count += sum(alias.name == "*" or (alias.asname or alias.name.split(".")[0]) == name
                             for alias in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                   ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
                count += node.name == name
            elif isinstance(node, ast.MatchMapping):
                count += node.rest == name
        for node in ast.walk(scope):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                count += name in node.names
        return count

    @staticmethod
    def _assignments(scope, name):
        values = []
        for node in scope_nodes(scope):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name for t in node.targets):
                values.append(node.value)
            elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                  and node.target.id == name and node.value is not None):
                values.append(node.value)
        return values

    @staticmethod
    def _check_unchanged(scope, name, at, where="line "):
        """Raise at the node *at* if *scope* changes the object bound to *name* in place.

        *where* comes before the line number of the change in the message.
        """
        for node in ast.walk(scope):
            if isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Load):
                target = node.value
            elif isinstance(node, ast.Attribute) and node.attr in DICT_MUTATORS:
                target = node.value
            else:
                continue
            if isinstance(target, ast.Name) and target.id == name:
                raise Unresolved(at, f"{name!r} is changed in place at {where}{node.lineno}")

    def _module_path(self, statement):
        """The relative path of the add-on module a relative ``from`` import names, or None."""
        if statement.level == 0 or statement.module is None:
            return None
        base = os.path.dirname(self.rel)
        for _ in range(statement.level - 1):
            base = os.path.dirname(base)
        path = os.path.join(base, *statement.module.split("."))
        for candidate in (path + ".py", os.path.join(path, "__init__.py")):
            if os.path.normpath(candidate) in self.modules:
                return os.path.normpath(candidate)
        return None

    def _imported(self, node):
        """The source and value of the module-level name *node* when a ``from`` import binds it, else None."""
        statement = next((stmt for stmt in scope_nodes(self.tree) if isinstance(stmt, ast.ImportFrom)
                          and any((alias.asname or alias.name) == node.id for alias in stmt.names)), None)
        if statement is None:
            return None
        name = next(alias.name for alias in statement.names if (alias.asname or alias.name) == node.id)
        path = self._module_path(statement)
        if path is None:
            raise Unresolved(node, f"{node.id!r} is imported from a module the scan does not have")
        other = self.modules[path]
        values = other._assignments(other.tree, name)
        if other._bindings(other.tree, name) != 1 or len(values) != 1:
            raise Unresolved(node, f"{name!r} must be bound exactly once in {path}, by a plain assignment")
        self._check_unchanged(self.tree, node.id, node)
        other._check_unchanged(other.tree, name, node, where=f"{path}:")
        return other, values[0]

    def definition(self, node):
        """The source that binds a name and the expression it is assigned, following names and imports.

        Other nodes are returned as they are, with this source.
        """
        try:
            seen = set()
            while isinstance(node, ast.Name):
                if node.id in seen:
                    raise Unresolved(node, f"name {node.id!r} refers to itself")
                seen.add(node.id)
                scope = self.tree
                for function in self._functions_around(node):
                    arguments = function.args
                    parameters = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
                    parameters += [a for a in (arguments.vararg, arguments.kwarg) if a is not None]
                    if any(a.arg == node.id for a in parameters):
                        raise Unresolved(node, f"{node.id!r} is a function parameter")
                    if self._bindings(function, node.id):
                        scope = function
                        break
                imported = self._imported(node) if scope is self.tree else None
                if imported is not None and self._bindings(scope, node.id) == 1:
                    other, value = imported
                    return other.definition(value)
                values = self._assignments(scope, node.id)
                if self._bindings(scope, node.id) != 1 or len(values) != 1:
                    raise Unresolved(node, f"{node.id!r} must be bound exactly once, by a plain assignment")
                self._check_unchanged(scope, node.id, node)
                node = values[0]
            return self, node
        except Unresolved as error:
            error.source = error.source or self
            raise

    def resolve(self, node):
        """The set of string literals *node* can evaluate to."""
        source, node = self.definition(node)
        if source is not self:
            return source.resolve(node)
        try:
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
        except Unresolved as error:
            error.source = error.source or self
            raise

    def _dict_values(self, node):
        source, target = self.definition(node)
        if not isinstance(target, ast.Dict):
            raise Unresolved(node, f"{ast.unparse(node)!r} is not a dict literal")
        return source.resolve(target)

    def fallback_lists(self, nodes):
        """Every name list a sequence of icon expressions can produce, one per combination."""
        return [list(names) for names in itertools.product(*(sorted(self.resolve(n)) for n in nodes))]


def call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def addon_sources(root=REPO):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__"
                         and not (folder == root and d in SKIP_DIRS))
        for f in sorted(files):
            if f.endswith(".py"):
                path = os.path.join(folder, f)
                with open(path, encoding="utf-8") as fh:
                    yield Source(os.path.relpath(path, root), fh.read())


# --- what this Blender has ------------------------------------------------

def enum_names(prop):
    return set(prop.enum_items.keys())


BLENDER_ICONS = enum_names(bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon'])
LAYOUT_ICON_FUNCTIONS = {f.identifier for f in bpy.types.UILayout.bl_rna.functions
                         if "icon" in f.parameters.keys()}
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


def check_icon_value(report, source, keyword):
    value = keyword.value
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        report.problem(source, keyword,
                       f"icon_value={value.value!r} is a name, not an icon id: use **icon_kwargs({value.value!r})")
    elif isinstance(value, ast.Call) and call_name(value) == "get_icon":
        if len(value.args) != 1 or value.keywords or isinstance(value.args[0], ast.Starred):
            raise Unresolved(value, "get_icon takes one icon name")
        for name in sorted(source.resolve(value.args[0])):
            report.count("get_icon")
            if name not in ADDON_ICONS:
                report.problem(source, keyword, f"get_icon({name!r}) has no file in icons/")


def check_unpacked_keywords(source, keyword):
    _owner, value = source.definition(keyword.value)
    if isinstance(value, ast.Call) and call_name(value) == "icon_kwargs":
        return
    if not isinstance(value, ast.Dict):
        raise Unresolved(keyword.value, f"**{ast.unparse(keyword.value)} in a layout call is neither "
                                        "icon_kwargs(...) nor a dict literal")
    for key in value.keys:
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise Unresolved(value, "a dict unpacked into a layout call has a key that is not a string literal")
        if key.value in ("icon", "icon_value"):
            raise Unresolved(key, f"{key.value!r} unpacked from a dict: use **icon_kwargs(...)")


def is_bl_icon_value(source, node):
    parent = source.parents.get(node)
    return (isinstance(parent, ast.Assign) and parent.value is node
            and any(isinstance(t, ast.Name) and t.id == "bl_icon" for t in parent.targets)
            and isinstance(source.parents.get(parent), ast.ClassDef))


def is_gizmo_icon_value(source, node):
    """Whether *node* is the value of ``<gizmo>.icon = ...``."""
    parent = source.parents.get(node)
    return (isinstance(parent, ast.Assign) and parent.value is node
            and any(isinstance(t, ast.Attribute) and t.attr == GIZMO_ICON_ATTRIBUTE
                    for t in parent.targets))


def is_enum_item_icon(source, node):
    """Whether *node* is the icon of an ``EnumProperty`` item: the fourth entry of a tuple that starts with three strings."""
    parent = source.parents.get(node)
    return (isinstance(parent, ast.Tuple) and len(parent.elts) in (4, 5) and parent.elts[3] is node
            and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in parent.elts[:3]))


def scan_call(report, source, call):
    name = call_name(call)
    for keyword in call.keywords:
        if keyword.arg == "icon":
            check_literal_icon(report, source, keyword)
        elif keyword.arg == "icon_value":
            check_icon_value(report, source, keyword)
        elif keyword.arg is None and name in LAYOUT_ICON_FUNCTIONS:
            check_unpacked_keywords(source, keyword)
    if (name == "setattr" and len(call.args) >= 2 and isinstance(call.args[1], ast.Constant)
            and call.args[1].value in ICON_ATTRIBUTES):
        report.problem(source, call, f"{call.args[1].value} set with setattr: assign it in the class body")
    if name == "blender_icon":
        kind = ("gizmo icon" if is_gizmo_icon_value(source, call)
                else "enum item icon" if is_enum_item_icon(source, call) else None)
        if kind is not None:
            if call.keywords:
                raise Unresolved(call, "blender_icon takes icon names as plain positional arguments")
            check_fallbacks(report, source, call, source.fallback_lists(call.args),
                            kind, BLENDER_ICONS, "a Blender icon")
        elif not is_bl_icon_value(source, call):
            raise Unresolved(call, "blender_icon is only for bl_icon class attributes, gizmo icons and "
                                   "enum item icons; layout calls use icon_kwargs")
    if name != "icon_kwargs":
        return
    starred = [arg for arg in call.args if isinstance(arg, ast.Starred)]
    if starred:
        if len(call.args) == 1 and isinstance(starred[0].value, ast.Attribute) and starred[0].value.attr == "ps_icon":
            return
        raise Unresolved(call, "icon_kwargs with an unpacked argument other than a ps_icon tuple")
    check_fallbacks(report, source, call, source.fallback_lists(call.args), "icon_kwargs",
                    BLENDER_ICONS | ADDON_ICONS, "a Blender icon or a file in icons/")


def scan_reference(report, source, node):
    """Report icon helpers the scan would not find by name, and icon attributes set outside a class body."""
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name in ICON_HELPERS and alias.asname not in (None, alias.name):
                report.problem(source, node, f"{alias.name} imported as {alias.asname!r}: import it by its own name")
        return
    name = node.id if isinstance(node, ast.Name) else node.attr
    if name in ICON_HELPERS and isinstance(node.ctx, ast.Load):
        parent = source.parents.get(node)
        if not (isinstance(parent, ast.Call) and parent.func is node):
            report.problem(source, node, f"{name} used other than by calling it")
    elif name in ICON_ATTRIBUTES and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
        report.problem(source, node, f"{name} set on an object: assign it in the class body")
    elif (name == GIZMO_ICON_ATTRIBUTE and isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)):
        parent = source.parents.get(node)
        value = parent.value if isinstance(parent, ast.Assign) else None
        if not (isinstance(value, ast.Call) and call_name(value) == "blender_icon"):
            report.problem(source, node, "a gizmo icon bypasses the fallback: use blender_icon(...)")


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


def is_class_attribute(cls, parent, node):
    """Whether the name *node*, a child of *parent*, is a target of an assignment directly in the body of *cls*."""
    if not any(stmt is parent for stmt in cls.body):
        return False
    if isinstance(parent, ast.Assign):
        return any(target is node for target in parent.targets)
    return isinstance(parent, ast.AnnAssign) and parent.target is node


def scan_class(report, source, cls, addon_classes):
    for node in scope_nodes(cls):
        if (isinstance(node, ast.Name) and node.id in ICON_ATTRIBUTES and isinstance(node.ctx, ast.Store)
                and not is_class_attribute(cls, source.parents.get(node), node)):
            report.problem(source, node, f"{node.id} of {cls.name} is not a plain assignment in the class body")
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            targets = [stmt.target.id]
        else:
            continue
        if "ps_icon" in targets:
            owner, value = source.definition(stmt.value)
            if not isinstance(value, ast.Tuple):
                raise Unresolved(stmt, f"ps_icon of {cls.name} is not a tuple literal")
            check_fallbacks(report, source, stmt, owner.fallback_lists(value.elts), "ps_icon",
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
    modules = {os.path.normpath(source.rel): source for source in sources}
    for source in sources:
        source.modules = modules
    for source in sources:
        for node in ast.walk(source.tree):
            try:
                if isinstance(node, ast.Call):
                    scan_call(report, source, node)
                elif isinstance(node, ast.ClassDef):
                    scan_class(report, source, node, addon_classes)
                elif isinstance(node, (ast.ImportFrom, ast.Name, ast.Attribute)):
                    scan_reference(report, source, node)
            except Unresolved as error:
                owner = error.source or source
                used = f"line {node.lineno}" if owner is source else f"{source.rel}:{node.lineno}"
                report.problem(owner, error.node, f"unresolved icon reference (used at {used}): {error}")
    return report


# --- planted faults ---------------------------------------------------------

def plant(*lines):
    return "\n".join(lines) + "\n"


MISSING = "PS_MISSING_ICON"
ADDON_ICON = sorted(ADDON_ICONS)[0]

# (fault, source, line the scan must report). Each source is scanned on its
# own. Icon names that exist are used where the fault is the form, not the
# name, so only the rule under test can report it.
PLANTED_FAULTS = [
    ("icon in a dict literal unpacked into a layout call", plant(
        "def draw(layout):",
        "    layout.label(text='', **{'icon': 'LOCKED'})",
    ), 2),
    ("icon in a dict variable unpacked into a layout call", plant(
        "def draw(layout):",
        "    options = {'icon': 'LOCKED'}",
        "    layout.label(text='', **options)",
    ), 2),
    ("parameter unpacked into a layout call", plant(
        "def draw(layout, options):",
        "    layout.label(text='', **options)",
    ), 2),
    ("node bl_icon under an if in the class body", plant(
        "class Layer(bpy.types.Node):",
        "    if bpy.app.version >= (5, 0, 0):",
        "        bl_icon = 'LOCKED'",
    ), 3),
    ("tool bl_icon under an if in the class body", plant(
        "class Tool(WorkSpaceTool):",
        "    if bpy.app.version >= (5, 0, 0):",
        f"        bl_icon = '{MISSING}'",
    ), 3),
    ("gizmo icon given a literal name", plant(
        "def setup(gizmo):",
        "    gizmo.icon = 'LOCKED'",
    ), 2),
    ("gizmo icon given a missing name", plant(
        "def setup(gizmo):",
        f"    gizmo.icon = blender_icon('{MISSING}')",
    ), 2),
    ("blender_icon outside a bl_icon, a gizmo icon or an enum item", plant(
        "def draw(layout):",
        "    layout.label(text='', **icon_kwargs(blender_icon('LOCKED')))",
    ), 2),
    ("enum item icon given a missing name", plant(
        f"ITEMS = [('A', 'A', 'The first', blender_icon('{MISSING}'), 0)]",
    ), 1),
    ("ps_icon set on the class object", plant(
        "Layer.ps_icon = ('LOCKED',)",
    ), 1),
    ("bl_icon set with setattr", plant(
        "setattr(Layer, 'bl_icon', 'LOCKED')",
    ), 1),
    ("name rebound by a for loop", plant(
        "def draw(layout):",
        "    name = 'LOCKED'",
        f"    for name in ('{MISSING}',):",
        "        layout.label(**icon_kwargs(name))",
    ), 4),
    ("name rebound by unpacking", plant(
        "def draw(layout, flag):",
        "    name = 'LOCKED'",
        "    if flag:",
        f"        name, other = '{MISSING}', 1",
        "    layout.label(**icon_kwargs(name))",
    ), 5),
    ("name rebound by an augmented assignment", plant(
        "def draw(layout):",
        "    name = 'LOCKED'",
        "    name += '_OFF'",
        "    layout.label(**icon_kwargs(name))",
    ), 4),
    ("module name rebound through global", plant(
        "LOCK = 'LOCKED'",
        "def relock():",
        "    global LOCK",
        f"    LOCK = '{MISSING}'",
        "def draw(layout):",
        "    layout.label(**icon_kwargs(LOCK))",
    ), 6),
    ("name assigned only in a nested function", plant(
        "def draw(layout):",
        "    def pick():",
        "        name = 'LOCKED'",
        "    layout.label(**icon_kwargs(name))",
    ), 4),
    ("dict given an item after its literal", plant(
        "ICONS = {'a': 'LOCKED'}",
        f"ICONS['b'] = '{MISSING}'",
        "def draw(layout, key):",
        "    layout.label(**icon_kwargs(ICONS[key]))",
    ), 4),
    ("dict updated after its literal", plant(
        "ICONS = {'a': 'LOCKED'}",
        f"ICONS.update(b='{MISSING}')",
        "def draw(layout, key):",
        "    layout.label(**icon_kwargs(ICONS.get(key, 'NONE')))",
    ), 4),
    ("icon_kwargs imported under another name", plant(
        "from ..common import icon_kwargs as icons",
    ), 1),
    ("icon_kwargs used other than by calling it", plant(
        "from ..common import icon_kwargs",
        "lookup = icon_kwargs",
    ), 2),
    ("icon_value given a name", plant(
        "def draw(layout):",
        "    layout.label(text='', icon_value='LOCKED')",
    ), 2),
    ("icon_value given get_icon of a missing file", plant(
        "def draw(layout):",
        f"    layout.label(text='', icon_value=get_icon('{MISSING.lower()}'))",
    ), 2),
]

# Forms the rules accept, which must report nothing.
ACCEPTED_FORMS = plant(
    "from ..common import blender_icon, icon_kwargs",
    "ICONS = {'a': 'LOCKED'}",
    "LOCK = 'LOCKED'",
    "class Layer(bpy.types.Node):",
    f"    bl_icon = blender_icon('{MISSING}', 'LOCKED')",
    f"    ps_icon = ('{MISSING}', 'LOCKED')",
    "def draw(layout, key, flag, icon):",
    "    name = LOCK if flag else 'NONE'",
    "    options = icon_kwargs(name)",
    "    layout.label(text='', **options)",
    "    layout.label(**icon_kwargs(ICONS.get(key, 'NONE')))",
    "    layout.label(text='', **{'text_ctxt': 'Layer'})",
    "    layout.label(text='', icon='NONE')",
    "    layout.label(text='', icon_value=icon)",
    f"    layout.label(text='', icon_value=get_icon('{ADDON_ICON}'))",
    f"ITEMS = [('A', 'A', 'The first', blender_icon('{MISSING}', 'LOCKED'), 0)]",
)

# A module that another planted module imports from.
ICON_MODULE = "props/icons.py"
IMPORTER = plant(
    "from ..props.icons import ICONS",
    "def draw(layout, key):",
    "    layout.label(**icon_kwargs(ICONS[key]))",
)

# (fault, {path: source}, (path, line) the scan must report). The files
# are scanned together, so imports between them resolve.
PLANTED_IMPORT_FAULTS = [
    ("imported dict holds a missing name", {
        ICON_MODULE: plant(f"ICONS = {{'a': '{MISSING}'}}"),
        "panels/plant.py": IMPORTER,
    }, ("panels/plant.py", 3)),
    ("imported name bound twice in its module", {
        ICON_MODULE: plant("ICONS = {'a': 'LOCKED'}", "ICONS = {'a': 'LOCKED'}"),
        "panels/plant.py": IMPORTER,
    }, ("panels/plant.py", 3)),
    ("imported dict changed in place in its module", {
        ICON_MODULE: plant("ICONS = {'a': 'LOCKED'}", f"ICONS['b'] = '{MISSING}'"),
        "panels/plant.py": IMPORTER,
    }, ("panels/plant.py", 3)),
    ("imported dict changed in place by the importer", {
        ICON_MODULE: plant("ICONS = {'a': 'LOCKED'}"),
        "panels/plant.py": plant(
            "from ..props.icons import ICONS",
            f"ICONS['b'] = '{MISSING}'",
            "def draw(layout, key):",
            "    layout.label(**icon_kwargs(ICONS[key]))",
        ),
    }, ("panels/plant.py", 4)),
    ("imported from a module the scan does not have", {
        "panels/plant.py": IMPORTER,
    }, ("panels/plant.py", 3)),
    ("imported dict holds a value the rules cannot resolve", {
        ICON_MODULE: plant("ICONS = {'a': pick_icon()}"),
        "panels/plant.py": IMPORTER,
    }, (ICON_MODULE, 1)),
]

# Imports the rules accept, which must report nothing.
ACCEPTED_IMPORTS = {
    ICON_MODULE: plant("LOCK = 'LOCKED'", "ICONS = {'a': LOCK}"),
    "panels/plant.py": plant(
        "from ..props.icons import ICONS as TABLE",
        "def draw(layout, key):",
        "    layout.label(**icon_kwargs(TABLE.get(key, 'NONE')))",
    ),
}


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
for kind in ("icon_kwargs", "ps_icon", "blender_icon", "gizmo icon", "enum item icon", "WorkSpaceTool icon"):
    check(report.counts.get(kind, 0) > 0, f"the scan found {kind} references")
for rel, line, message in sorted(report.fallbacks):
    print(f"  [fallback] {rel}:{line} {message}")
for problem in report.sorted_problems():
    fail(problem)
check(not report.problems, f"{len(report.problems)} missing or unresolved icon references")

section("the scan reports planted faults")
for fault, text, line in PLANTED_FAULTS:
    planted = scan([Source("plant.py", text)])
    check(any(problem_line == line for _, problem_line, _ in planted.problems),
          f"{fault}: reported at line {line} {planted.sorted_problems()}")
accepted = scan([Source("plant.py", ACCEPTED_FORMS)])
check(not accepted.problems, f"accepted forms report nothing {accepted.sorted_problems()}")
for fault, files, (path, line) in PLANTED_IMPORT_FAULTS:
    planted = scan([Source(rel, text) for rel, text in files.items()])
    check(any((rel, problem_line) == (path, line) for rel, problem_line, _ in planted.problems),
          f"{fault}: reported at {path}:{line} {planted.sorted_problems()}")
accepted = scan([Source(rel, text) for rel, text in ACCEPTED_IMPORTS.items()])
check(not accepted.problems, f"accepted imports report nothing {accepted.sorted_problems()}")

with tempfile.TemporaryDirectory() as root:
    for rel in ("build/plant.py", "tests/plant.py", ".venv/plant.py",
                "panels/__pycache__/plant.py", "panels/build/plant.py", "panels/icons/plant.py"):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("")
    found = sorted(source.rel for source in addon_sources(root))
    expected = sorted(os.path.join("panels", d, "plant.py") for d in ("build", "icons"))
    check(found == expected, f"only top-level folders are skipped by name: scanned {found}")

finish("icons")
