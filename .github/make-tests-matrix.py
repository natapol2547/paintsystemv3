import re
from urllib import request


jobs = [
    {
        "version": "4.1.0",
        "version_x_y": "4.1",
        "sha": "released",
        "download_url": "https://download.blender.org/release/Blender4.1/blender-4.1.0-linux-x64.tar.xz",
    },
    {  # LTS
        "version": "4.2.13",
        "version_x_y": "4.2",
        "sha": "released",
        "download_url": "https://download.blender.org/release/Blender4.2/blender-4.2.13-linux-x64.tar.xz",
    },
    {
        "version": "4.3.2",
        "version_x_y": "4.3",
        "sha": "released",
        "download_url": "https://download.blender.org/release/Blender4.3/blender-4.3.2-linux-x64.tar.xz",
    },
    {
        "version": "4.4.3",
        "version_x_y": "4.4",
        "sha": "released",
        "download_url": "https://download.blender.org/release/Blender4.4/blender-4.4.3-linux-x64.tar.xz",
    },
    {  # LTS
        "version": "4.5.2",
        "version_x_y": "4.5",
        "sha": "released",
        "download_url": "https://download.blender.org/release/Blender4.5/blender-4.5.2-linux-x64.tar.xz",
    },
    # 5.0
    {
        "version": "5.0.0",
        "version_x_y": "5.0",
        "sha": "released",
        "download_url": "https://download.blender.org/release/Blender5.0/blender-5.0.0-linux-x64.tar.xz",
    },
    # {'version': '', 'version_x_y': '', 'download_url': ''},
]

matrix = {"include": jobs}
print(f"matrix={matrix}")