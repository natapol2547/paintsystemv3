#!/usr/bin/env bash
# Run the Blender-side tests.
#
#   tests/run.sh                 headless tests (tests/test_*.py)
#   tests/run.sh --ui            headless tests plus the windowed UI draw test
#   tests/run.sh test_compile.py only the named headless test(s)
#
# BLENDER  path to the Blender executable (default: local 5.2 LTS install)
# XVFB=1   force the UI test through xvfb-run even when DISPLAY is set
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${BLENDER:-/home/tawan/Desktop/Blender Launcher/stable/blender-5.2.1-lts.9e2066aef7ef/blender}"

run_ui=0
files=()
for arg in "$@"; do
    case "$arg" in
        --ui) run_ui=1 ;;
        *) files+=("$HERE/$(basename "$arg")") ;;
    esac
done
if [ ${#files[@]} -eq 0 ]; then
    files=("$HERE"/test_*.py)
fi

failed=()
for f in "${files[@]}"; do
    name="$(basename "$f")"
    [ "$name" = "test_ui_draw.py" ] && continue
    echo "### $name"
    # --python-exit-code makes an uncaught exception in the script fail
    # the process; by default Blender only prints it and exits 0.
    if ! "$BLENDER" -b --factory-startup --python-exit-code 1 --python "$f"; then
        failed+=("$name")
    fi
done

if [ "$run_ui" = 1 ]; then
    echo "### test_ui_draw.py (windowed)"
    cmd=("$BLENDER" --factory-startup --python-exit-code 1 --python "$HERE/test_ui_draw.py")
    if [ "${XVFB:-0}" = 1 ] || [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        if command -v xvfb-run >/dev/null; then
            # Headless X server with Mesa's software renderer; force the
            # OpenGL backend since Vulkan has no software device there.
            cmd=(env LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a -s "-screen 0 1920x1080x24"
                 "$BLENDER" --factory-startup --gpu-backend opengl --python-exit-code 1
                 --python "$HERE/test_ui_draw.py")
        else
            echo "no display and no xvfb-run; skipping UI test"
            cmd=()
        fi
    fi
    if [ ${#cmd[@]} -gt 0 ]; then
        if ! timeout 300 "${cmd[@]}"; then
            failed+=("test_ui_draw.py")
        fi
    fi
fi

echo
if [ ${#failed[@]} -gt 0 ]; then
    echo "FAILED: ${failed[*]}"
    exit 1
fi
echo "ALL TESTS PASSED"
