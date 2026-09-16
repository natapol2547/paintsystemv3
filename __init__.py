# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from bpy.utils import register_submodule_factory

from .custom_icons import load_icons, unload_icons

submodules = (
    "props",
    "context",
    "compiler",
    "gpu_passes",
    "nodes",
    "nodetree",
    "ops",
    "panels",
    "handlers",
    "preferences",
    "keymaps",
)

_register, _unregister = register_submodule_factory(__name__, submodules)


def register():
    load_icons()
    _register()


def unregister():
    unload_icons()
    _unregister()
