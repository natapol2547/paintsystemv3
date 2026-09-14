#!/usr/bin/env bash
# Run the headless smoke test. Set BLENDER to override the executable.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${BLENDER:-/home/tawan/Desktop/Blender Launcher/stable/blender-5.2.1-lts.9e2066aef7ef/blender}"
exec "$BLENDER" -b --factory-startup --python "$HERE/smoke_compile.py"
