#!/usr/bin/env bash
# Run the Blender-side tests.
#
#   tests/run.sh                 headless tests (tests/test_*.py)
#   tests/run.sh --ui            headless tests plus the windowed ones
#   tests/run.sh test_compile.py only the named headless test(s)
#
# Files in window_only need a window and are skipped headless. --ui runs
# every file in ui_tests windowed after the headless loop. That list also
# holds GPU tests that run headless: Blender 5.2's gpu.init() gives a
# background session a GPU context, but 4.2 to 5.1 have none, so there
# they only have coverage under --ui. Files in event_simulate also get
# --enable-event-simulate, which makes Blender ignore real input.
#
# BLENDER  path to the Blender executable (default: local 5.2 LTS install)
# XVFB=1   force the windowed tests through xvfb-run even when DISPLAY is set
# GPU_BACKEND=<name>
#          pass --gpu-backend <name> (opengl or vulkan) to the headless tests;
#          when unset, Blender picks its default backend
# PS_PERF_SCALE=<factor>
#          multiply the test_perf.py budgets, for a machine slower or busier
#          than the one they were measured on (default: 5 under CI, else 1)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Need a window: skipped in the headless loop.
window_only=(
    test_ui_draw.py
    test_selection_session_ui.py
    test_selection_stencil_ui.py
    test_selection_overlay_ui.py
    test_selection_view_windowed.py
    test_selection_tools_ui.py
    test_keymaps_ui.py
    test_action_bar_ui.py
)
# Run again windowed under --ui: 4.2 to 5.1 have no background GPU context.
ui_tests=(
    test_ui_draw.py
    test_texel_map.py
    test_selection_raster.py
    test_selection_session_ui.py
    test_selection_stencil.py
    test_selection_stencil_ui.py
    test_selection_overlay.py
    test_selection_overlay_ui.py
    test_selection_view_raster.py
    test_selection_view_windowed.py
    test_selection_tools_ui.py
    test_keymaps_ui.py
    test_filters_gpu.py
    test_filter_blend.py
    test_selection_actions.py
    test_action_bar_ui.py
)
# Windowed with --enable-event-simulate: real input is ignored while they run.
event_simulate=(
    test_selection_tools_ui.py
    test_keymaps_ui.py
)

BLENDER="${BLENDER:-/home/tawan/Desktop/Blender Launcher/stable/blender-5.2.1-lts.9e2066aef7ef/blender}"
backend_args=()
[ -n "${GPU_BACKEND:-}" ] && backend_args=(--gpu-backend "$GPU_BACKEND")

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
    [[ " ${window_only[*]} " == *" $name "* ]] && continue
    echo "### $name"
    # --python-exit-code makes an uncaught exception in the script fail
    # the process; by default Blender only prints it and exits 0.
    if ! "$BLENDER" -b --factory-startup "${backend_args[@]}" --python-exit-code 1 --python "$f"; then
        failed+=("$name")
    fi
done

if [ "$run_ui" = 1 ]; then
    for name in "${ui_tests[@]}"; do
        echo "### $name (windowed)"
        extra_args=()
        [[ " ${event_simulate[*]} " == *" $name "* ]] && extra_args=(--enable-event-simulate)
        cmd=("$BLENDER" --factory-startup "${extra_args[@]}" --python-exit-code 1 --python "$HERE/$name")
        if [ "${XVFB:-0}" = 1 ] || [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
            if command -v xvfb-run >/dev/null; then
                # Headless X server with Mesa's software renderer; force the
                # OpenGL backend since Vulkan has no software device there.
                cmd=(env LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a -s "-screen 0 1920x1080x24"
                     "$BLENDER" --factory-startup --gpu-backend opengl "${extra_args[@]}"
                     --python-exit-code 1 --python "$HERE/$name")
            else
                echo "no display and no xvfb-run; skipping $name"
                cmd=()
            fi
        fi
        if [ ${#cmd[@]} -gt 0 ]; then
            if ! timeout 300 "${cmd[@]}"; then
                failed+=("$name (windowed)")
            fi
        fi
    done
fi

echo
if [ ${#failed[@]} -gt 0 ]; then
    echo "FAILED: ${failed[*]}"
    exit 1
fi
echo "ALL TESTS PASSED"
