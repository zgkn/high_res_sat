#!/usr/bin/env python3
"""Build the Sentinel-2 viewer site in a directory that holds ``runs/<id>/run.json``.

Copies the static page from ``site/`` next to the runs and writes ``data.js`` (the run
list the page reads). Used on the ``s2-output`` branch by scripts/publish_site.sh, but it
works on any such directory:

    python scripts/build_site.py path/to/dir
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SITE_SRC = Path(__file__).resolve().parent.parent / "site"


def load_runs(root: Path) -> list[dict]:
    runs = []
    for meta in sorted((root / "runs").glob("*/run.json")):
        info = json.loads(meta.read_text(encoding="utf-8"))
        folder = meta.parent
        rel = f"runs/{folder.name}"
        comps = [c for c in info.get("composites", []) if (folder / f"{c}.webp").exists()]
        if not comps or not (folder / "thumb.jpg").exists():
            print(f"skipping {rel}: missing images", file=sys.stderr)
            continue
        info["id"] = folder.name
        info["composites"] = comps
        info["images"] = {c: f"{rel}/{c}.webp" for c in comps}
        info["png"] = {c: f"{rel}/{c}_{info['date']}.png" for c in comps
                       if (folder / f"{c}_{info['date']}.png").exists()}
        info["thumb"] = f"{rel}/thumb.jpg"
        runs.append(info)
    # newest image date first; within a date, newest run first
    runs.sort(key=lambda r: (r["date"], r.get("generated", "")), reverse=True)
    return runs


def build(root: Path) -> int:
    shutil.copytree(SITE_SRC, root, dirs_exist_ok=True)
    runs = load_runs(root)
    (root / "data.js").write_text(
        "window.S2_RUNS = " + json.dumps(runs, indent=1) + ";\n", encoding="utf-8")
    (root / ".nojekyll").write_text("", encoding="utf-8")
    print(f"site built in {root}: {len(runs)} run(s)")
    return len(runs)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]))
