#!/usr/bin/env python3
"""Build the Sentinel-2 viewer site in a directory that holds ``runs/<id>/run.json``.

Copies the static page from ``site/`` next to the runs and writes ``data.js`` (the run
list the page reads). Used on the ``s2-output`` branch by scripts/publish_site.sh, but it
works on any such directory:

    python scripts/build_site.py path/to/dir
"""
from __future__ import annotations

import hashlib
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
    # The same date, box and satellite can be published more than once (a re-run, or a series covering a
    # date that already had a card). Show only the newest; the older folder stays on the branch, hidden.
    newest: dict[tuple, dict] = {}
    for r in runs:
        key = (r["date"], tuple(round(v, 4) for v in r["bbox"]), r.get("source", "sentinel-2"))
        if key not in newest or r.get("generated", "") > newest[key].get("generated", ""):
            newest[key] = r
    runs = list(newest.values())
    # newest image date first; within a date, newest run first
    runs.sort(key=lambda r: (r["date"], r.get("generated", "")), reverse=True)
    return runs


def stamp_versions(root: Path) -> None:
    """Add ?v=<content hash> to the page's own files so browsers fetch fresh copies after every rebuild.

    Without it a browser (or GitHub Pages' cache) can keep serving an old app.js against the new index.html.
    """
    page = root / "index.html"
    html = page.read_text(encoding="utf-8")
    for rel in ("assets/style.css", "assets/app.js", "data.js"):
        digest = hashlib.sha1((root / rel).read_bytes()).hexdigest()[:8]
        for attr in ("href", "src"):
            html = html.replace(f'{attr}="{rel}"', f'{attr}="{rel}?v={digest}"')
    page.write_text(html, encoding="utf-8")


def build(root: Path) -> int:
    shutil.copytree(SITE_SRC, root, dirs_exist_ok=True)
    runs = load_runs(root)
    (root / "data.js").write_text(
        "window.S2_RUNS = " + json.dumps(runs, indent=1) + ";\n", encoding="utf-8")
    stamp_versions(root)
    (root / ".nojekyll").write_text("", encoding="utf-8")
    print(f"site built in {root}: {len(runs)} run(s)")
    return len(runs)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]))
