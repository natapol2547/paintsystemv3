"""Resolve the Blender builds to test against.

For every release series from ``blender_version_min`` in
``blender_manifest.toml`` up to the newest stable series, pick the latest
patch release from download.blender.org. Add the daily alpha/beta builds
of unreleased series from builder.blender.org as experimental entries.

Usage:
    python .github/resolve-blender.py            # GitHub Actions outputs
    python .github/resolve-blender.py --pretty   # human readable
    python .github/resolve-blender.py --min      # only the minimum series

Outputs (GITHUB_OUTPUT format):
    matrix       {"include": [{series, version, url, experimental}, ...]}
    min_version  e.g. 4.2.23
    min_url      download URL of the minimum supported build
    max_version  newest stable patch release
    max_url      its download URL
"""
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

RELEASE_INDEX = "https://download.blender.org/release/Blender{series}/"
DAILY_JSON = "https://builder.blender.org/download/daily/?format=json&v=1"
RE_ARCHIVE = re.compile(r"blender-(\d+)\.(\d+)\.(\d+)-linux-x64\.tar\.xz")
TIMEOUT = 30


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "paint-system-ci"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def manifest_min():
    path = Path(__file__).resolve().parent.parent / "blender_manifest.toml"
    with open(path, "rb") as f:
        data = tomllib.load(f)
    major, minor, _ = (int(x) for x in data["blender_version_min"].split("."))
    return major, minor


def daily_builds():
    """Linux x86_64 tar.xz entries from the daily builder, newest first."""
    try:
        data = json.loads(fetch(DAILY_JSON))
    except Exception as exc:  # noqa: BLE001
        print(f"warning: daily builder unavailable: {exc}", file=sys.stderr)
        return []
    out = []
    for b in data:
        if (b.get("platform"), b.get("architecture"), b.get("file_extension")) != ("linux", "x86_64", "xz"):
            continue
        major, minor, patch = (int(x) for x in b["version"].split("."))
        out.append({
            "series": (major, minor),
            "version": (major, minor, patch),
            "cycle": b.get("release_cycle", ""),
            "url": b["url"],
        })
    return out


def latest_release(series):
    """Latest official patch archive for an ``(major, minor)`` series."""
    url = RELEASE_INDEX.format(series=f"{series[0]}.{series[1]}")
    try:
        html = fetch(url)
    except Exception:  # noqa: BLE001
        return None
    best = None
    for m in RE_ARCHIVE.finditer(html):
        v = tuple(int(x) for x in m.groups())
        if v[:2] == series and (best is None or v > best):
            best = v
    if best is None:
        return None
    return best, url + f"blender-{best[0]}.{best[1]}.{best[2]}-linux-x64.tar.xz"


def resolve():
    lo = manifest_min()
    daily = daily_builds()
    stable_series = {b["series"] for b in daily if b["cycle"] == "stable"}
    hi = max(stable_series) if stable_series else lo

    # Walk every series between lo and hi. Minor numbers restart at 0 on
    # a new major, so probe the release index and stop a major when a
    # series is missing and no later series of that major is known.
    entries = []
    known = set(stable_series) | {lo}
    major, minor = lo
    while (major, minor) <= hi:
        found = latest_release((major, minor))
        if found is not None:
            (v, url) = found
            entries.append({
                "series": f"{major}.{minor}",
                "version": f"{v[0]}.{v[1]}.{v[2]}",
                "url": url,
                "experimental": False,
            })
            minor += 1
        elif any(s[0] == major and s[1] > minor for s in known):
            minor += 1
        else:
            major, minor = major + 1, 0
        if major > hi[0] + 1:
            break

    for b in sorted(daily, key=lambda b: b["version"]):
        if b["cycle"] != "stable" and b["series"] > hi:
            if any(e["series"] == f"{b['series'][0]}.{b['series'][1]}" for e in entries):
                continue
            entries.append({
                "series": f"{b['series'][0]}.{b['series'][1]}",
                "version": f"{b['version'][0]}.{b['version'][1]}.{b['version'][2]}-{b['cycle']}",
                "url": b["url"],
                "experimental": True,
            })

    if not entries:
        sys.exit("no Blender builds resolved")
    stable = [e for e in entries if not e["experimental"]]
    if not stable:
        sys.exit(f"no stable release found for {lo[0]}.{lo[1]}+")
    return entries, stable[0], stable[-1]


def main(argv):
    entries, lo, hi = resolve()
    if "--min" in argv:
        entries = [lo]
    if "--pretty" in argv:
        for e in entries:
            flag = " (experimental)" if e["experimental"] else ""
            print(f"{e['series']:>5}  {e['version']:<14} {e['url']}{flag}")
        return
    print(f"matrix={json.dumps({'include': entries})}")
    print(f"min_version={lo['version']}")
    print(f"min_url={lo['url']}")
    print(f"max_version={hi['version']}")
    print(f"max_url={hi['url']}")


if __name__ == "__main__":
    main(sys.argv[1:])
