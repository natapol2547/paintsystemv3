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

bl_info = {
    "name": "Paint System",
    "author": "Tawan Sunflower, @blastframe",
    "description": "",
    "blender": (4, 2, 0),
    "version": (3, 0, 0),
    "location": "View3D > Sidebar > Paint System",
    "warning": "",
    "category": "Paint",
    "support": "COMMUNITY",
    "tracker_url": "https://github.com/natapol2547/paintsystem"
}

bl_info_copy = bl_info.copy()


submodules = (
    "props",
    "nodes",
    "ui",
    "context",
    "preferences",
)

_register, _unregister = register_submodule_factory(__name__, submodules)


def register():
    _register()
    print("Paint System: Registered")


def unregister():
    _unregister()
    print("Paint System: Unregistered")
