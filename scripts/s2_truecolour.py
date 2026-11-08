#!/usr/bin/env python3
"""Sentinel-2 true-colour (and optional SWIR) image of the most recent clear date.

Searches Earth Search (Element 84) for Sentinel-2 L2A scenes over a bbox and
date range, scores every acquisition date for bbox coverage and cloud, picks
a date, then mosaics that date's tiles into a GeoTIFF and a PNG.

Selection (--pick latest_acceptable, the default):
  1. keep dates whose bbox coverage >= --min-coverage and whose SCL cloud
     fraction inside the bbox is <= --accept-cloud;
  2. choose the most recent of those.
If no date is acceptable it falls back to the clearest well-covered date
(then the best-covered date) and says so loudly.
--pick clearest ranks by lowest cloud instead of recency.
--composite swir|both adds a B12/B8A/B4 image that shows burn scars and sees
through thin smoke.

No API key needed. Runs locally or in GitHub Actions.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import sys
import textwrap
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import numpy as np
import rasterio
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
SCL_CLOUD = (8, 9, 10)  # medium prob, high prob, thin cirrus
SCL_SHADOW = 3
NATIVE_RES_M = 10.0
M_PER_DEG = 111_320.0

# SWIR composite: R=B12 (swir22), G=B8A (nir08), B=B4 (red). Fixed reflectance
# stretch per channel so images from different dates are comparable.
SWIR_ASSETS = ("swir22", "nir08", "red")
SWIR_RANGE = ((0.0, 0.40), (0.0, 0.50), (0.0, 0.20))
SWIR_GAMMA = 0.6
# True colour from the raw bands. The ESA "visual" asset is the raw value / 10 (black level lifted by the
# +0.1 offset), which clips to white at only ~15 % reflectance, so every cloud saturates. "soft" keeps that
# exact curve up to the knee (land is unchanged, within ~3/255) and rolls highlights off smoothly above it.
TC_KNEE = 0.666   # output level where the roll-off starts (reflectance ~0.07)
TC_ROLL = 1.2     # roll-off length, in units of the ESA scale (bigger = brighter clouds)
SWIR_CAPTION = ("Burn scars: dark red/brown | active fire: bright orange | "
                "vegetation: green | cloud: white | thin smoke: mostly transparent")

# Keep GDAL from listing bucket directories and retry flaky range requests.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("VSI_CACHE", "TRUE")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class Fail(Exception):
    """Clear, user-facing failure."""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--bbox", default="114,-2,115,-1",
                   help="min_lon,min_lat,max_lon,max_lat")
    p.add_argument("--max-cloud", type=float, default=60,
                   help="scene-level eo:cloud_cover pre-filter, %% (default 60)")
    p.add_argument("--accept-cloud", type=float, default=20,
                   help="max SCL cloud %% inside the bbox for a date to count "
                        "as acceptable (default 20)")
    p.add_argument("--max-px", type=int, default=2500,
                   help="longest side of the output image (default 2500)")
    p.add_argument("--pick", choices=["latest_acceptable", "clearest"],
                   default="latest_acceptable")
    p.add_argument("--composite", choices=["truecolour", "swir", "both"],
                   default="truecolour",
                   help="truecolour = B4/B3/B2; swir = B12/B8A/B4 (burn scars, "
                        "sees through thin smoke); both = one of each")
    p.add_argument("--tc-style", choices=["soft", "esa"], default="soft",
                   help="true colour tone: soft = highlights rolled off so clouds keep "
                        "detail (default); esa = the Sentinel-2 'visual' asset as is")
    p.add_argument("--label", default="",
                   help="optional area name shown on the site (default: the bbox)")
    p.add_argument("--min-coverage", type=float, default=90,
                   help="min bbox coverage %% for a date to qualify (default 90)")
    p.add_argument("--score-px", type=int, default=600,
                   help="longest side of the grid used to score dates")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--outdir", default="output")
    return p.parse_args(argv)


def parse_bbox(text):
    try:
        w, s, e, n = (float(x) for x in text.split(","))
    except ValueError:
        raise Fail(f"bbox must be 'min_lon,min_lat,max_lon,max_lat', got {text!r}")
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise Fail(f"bbox {text!r} is not a valid lon/lat box (need min < max)")
    return w, s, e, n


def make_grid(bbox, px, native=False):
    """EPSG:4326 grid over bbox; longest side `px` (or native 10 m if native)."""
    w, s, e, n = bbox
    cos_lat = math.cos(math.radians((s + n) / 2))
    wm, hm = (e - w) * M_PER_DEG * cos_lat, (n - s) * M_PER_DEG
    if native:
        width, height = math.ceil(wm / NATIVE_RES_M), math.ceil(hm / NATIVE_RES_M)
    elif wm >= hm:
        width, height = px, max(1, round(px * hm / wm))
    else:
        width, height = max(1, round(px * wm / hm)), px
    return width, height, from_bounds(w, s, e, n, width, height)


def tile_id(item):
    return item.properties.get("grid:code", "").replace("MGRS-", "") or item.id


def item_date(item):
    return item.datetime.date().isoformat()  # item.datetime is UTC


def read_item(href, bbox, out_w, out_h, bands, resampling):
    """Read `bands` of a COG onto the bbox grid in EPSG:4326.

    A WarpedVRT on a native-resolution bbox grid is decimated with out_shape,
    so GDAL pulls from the COG overviews and only the bbox window.
    """
    nw, nh, ntransform = make_grid(bbox, 0, native=True)
    nw, nh = max(nw, out_w), max(nh, out_h)
    ntransform = from_bounds(*bbox, nw, nh)
    with rasterio.open(href) as src, WarpedVRT(
        src, crs="EPSG:4326", transform=ntransform, width=nw, height=nh,
        resampling=Resampling.nearest, src_nodata=0, nodata=0,
    ) as vrt:
        return vrt.read(indexes=bands, out_shape=(len(bands), out_h, out_w),
                        resampling=resampling)


def mosaic_rgb(arrays):
    """First valid pixel wins; zeros are nodata."""
    out = np.zeros_like(arrays[0])
    filled = np.zeros(out.shape[1:], bool)
    for a in arrays:
        take = a.any(axis=0) & ~filled
        out[:, take] = a[:, take]
        filled |= take
    return out


def write_summary(lines):
    text = "\n".join(lines) + "\n"
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    print("\n" + text)


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------
def search_items(args, bbox):
    client = Client.open(STAC_URL)
    dt = f"{args.start_date}T00:00:00Z/{args.end_date}T23:59:59Z"

    def run(query):
        return list(client.search(
            collections=[COLLECTION], bbox=bbox, datetime=dt, query=query,
            limit=100).items())

    found = run({"eo:cloud_cover": {"lte": args.max_cloud}})
    if found:
        return found
    # Zero hits: work out which filter removed everything.
    unfiltered = run(None)
    if not unfiltered:
        raise Fail(
            f"No Sentinel-2 L2A scenes intersect bbox {args.bbox} between "
            f"{args.start_date} and {args.end_date}. The date range or bbox "
            "removed them (no cloud filter involved). Widen the date range or "
            "check the bbox order (min_lon,min_lat,max_lon,max_lat).")
    clouds = [i.properties.get("eo:cloud_cover", 100) for i in unfiltered]
    raise Fail(
        f"{len(unfiltered)} scenes exist for this bbox/date range, but all were "
        f"removed by the max_cloud filter (eo:cloud_cover <= {args.max_cloud}%). "
        f"Their cloud cover ranges {min(clouds):.0f}-{max(clouds):.0f}%. "
        f"Raise max_cloud to at least {math.ceil(min(clouds))} or widen the dates.")


def dedupe(items):
    """One item per (date, MGRS tile): keep the newest processing baseline."""
    best = {}
    for it in items:
        key = (item_date(it), tile_id(it))
        rank = (it.properties.get("s2:processing_baseline", ""),
                it.properties.get("updated", ""))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, it)
    return [v[1] for v in best.values()]


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
def score_item(item, bbox, grid_w, grid_h):
    """Return (valid mask, SCL array or None) for one item on the score grid."""
    try:
        rgb = read_item(item.assets["visual"].href, bbox, grid_w, grid_h,
                        [1, 2, 3], Resampling.average)
    except Exception as exc:
        print(f"  ! {item.id}: could not read visual ({exc})", file=sys.stderr)
        return None, None
    valid = rgb.any(axis=0)
    try:
        scl = read_item(item.assets["scl"].href, bbox, grid_w, grid_h,
                        [1], Resampling.nearest)[0]
    except Exception as exc:
        print(f"  ! {item.id}: could not read SCL ({exc})", file=sys.stderr)
        scl = None
    return valid, scl


def score_dates(items, bbox, args):
    gw, gh, _ = make_grid(bbox, args.score_px)
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda it: score_item(it, bbox, gw, gh), items))

    by_date = defaultdict(list)
    for it, (valid, scl) in zip(items, results):
        by_date[item_date(it)].append((it, valid, scl))

    rows = []
    for d, group in sorted(by_date.items()):
        group.sort(key=lambda g: tile_id(g[0]))
        valid = np.zeros((gh, gw), bool)
        scl_mosaic = np.zeros((gh, gw), np.uint8)
        scl_ok = True
        for it, v, scl in group:
            if v is None:
                continue
            take = v & ~valid
            valid |= v
            if scl is None:
                scl_ok = False
            else:
                scl_mosaic[take] = scl[take]
        metas = [it.properties.get("eo:cloud_cover") for it, _, _ in group]
        metas = [m for m in metas if m is not None]
        meta = float(np.mean(metas)) if metas else float("nan")
        coverage = 100 * valid.mean()
        if scl_ok and valid.any():
            s = scl_mosaic[valid]
            cloud = 100 * np.isin(s, SCL_CLOUD).mean()
            shadow = 100 * (s == SCL_SHADOW).mean()
        else:
            cloud = shadow = float("nan")
        rows.append({
            "date": d,
            "items": [g[0] for g in group],
            "tiles": sorted({tile_id(g[0]) for g in group}),
            "coverage": coverage,
            "meta_cloud": meta,
            "scl_cloud": cloud,
            "scl_shadow": shadow,
            # Rank on SCL; fall back to scene metadata if SCL was unreadable.
            "cloud": cloud if not math.isnan(cloud) else meta,
        })
    return rows


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def select(rows, args):
    """Return (chosen row, reason, fallback flag, ordered runner-ups)."""
    full = [r for r in rows if r["coverage"] >= args.min_coverage]
    clear_key = lambda r: (r["cloud"], -r["coverage"])

    if args.pick == "latest_acceptable":
        ok = sorted((r for r in full if r["cloud"] <= args.accept_cloud),
                    key=lambda r: r["date"], reverse=True)
        if ok:
            return (ok[0],
                    f"most recent date with >= {args.min_coverage:g}% coverage "
                    f"and <= {args.accept_cloud:g}% SCL cloud", False, ok[1:])
    if full:
        ranked = sorted(full, key=clear_key)
        why = (f"NO date had <= {args.accept_cloud:g}% cloud, so this is the "
               "clearest date with full coverage (fallback)"
               if args.pick == "latest_acceptable"
               else "lowest cloud among dates with full coverage")
        return ranked[0], why, args.pick == "latest_acceptable", ranked[1:]
    ranked = sorted(rows, key=lambda r: (-r["coverage"], r["cloud"]))
    return (ranked[0],
            f"NO date reached {args.min_coverage:g}% coverage, so this is the "
            "best-covered date (fallback; image will have gaps)",
            True, ranked[1:])


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------
def swir_item(item, bbox, w, h):
    """B12/B8A/B4 -> 8-bit RGB; 0 stays nodata, valid pixels are >= 1."""
    out = np.zeros((3, h, w), np.uint8)
    valid = np.ones((h, w), bool)
    for i, (key, (lo, hi)) in enumerate(zip(SWIR_ASSETS, SWIR_RANGE)):
        asset = item.assets[key]
        dn = read_item(asset.href, bbox, w, h, [1], Resampling.average)[0]
        rb = asset.extra_fields.get("raster:bands", [{}])[0]
        refl = dn * rb.get("scale", 1e-4) + rb.get("offset", -0.1)
        valid &= dn > 0
        stretch = np.clip((refl - lo) / (hi - lo), 0, 1) ** SWIR_GAMMA
        out[i] = (1 + stretch * 254).astype(np.uint8)
    out[:, ~valid] = 0
    return out


def truecolour_item(item, bbox, w, h):
    """B4/B3/B2 -> 8-bit RGB with the ESA curve below TC_KNEE and a soft shoulder above."""
    out = np.zeros((3, h, w), np.uint8)
    valid = np.ones((h, w), bool)
    for i, key in enumerate(("red", "green", "blue")):
        asset = item.assets[key]
        dn = read_item(asset.href, bbox, w, h, [1], Resampling.average)[0]
        rb = asset.extra_fields.get("raster:bands", [{}])[0]
        refl = dn * rb.get("scale", 1e-4) + rb.get("offset", -0.1)
        valid &= dn > 0
        t = (refl + 0.1) / 0.255  # what the ESA product encodes (0..1 = 0..255)
        over = np.maximum(t, TC_KNEE)
        y = np.where(t <= TC_KNEE, t,
                     TC_KNEE + (1 - TC_KNEE) * (1 - np.exp(-(over - TC_KNEE) / TC_ROLL)))
        out[i] = (1 + np.clip(y, 0, 1) * 254).astype(np.uint8)
    out[:, ~valid] = 0
    return out


def render(row, bbox, args, composite):
    w, h, transform = make_grid(bbox, args.max_px)
    if composite == "swir":
        read = lambda it: swir_item(it, bbox, w, h)
    elif args.tc_style == "soft":
        read = lambda it: truecolour_item(it, bbox, w, h)
    else:
        read = lambda it: read_item(it.assets["visual"].href, bbox, w, h,
                                    [1, 2, 3], Resampling.average)
    with ThreadPoolExecutor(args.workers) as pool:
        arrays = list(pool.map(read, row["items"]))
    return mosaic_rgb(arrays), transform


def write_geotiff(path, rgb, transform):
    with rasterio.open(
        path, "w", driver="GTiff", dtype="uint8", count=3,
        width=rgb.shape[2], height=rgb.shape[1], crs="EPSG:4326",
        transform=transform, nodata=0, compress="deflate", tiled=True,
        photometric="RGB",
    ) as dst:
        dst.write(rgb)


def write_png(path, rgb, bbox, row, note, composite="truecolour"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    w, s, e, n = bbox
    cos_lat = math.cos(math.radians((s + n) / 2))
    name = ("SWIR composite (B12/B8A/B4)" if composite == "swir"
            else "true colour")
    title = (f"Sentinel-2 L2A {name} - {row['date']}\n"
             f"Tiles: {', '.join(row['tiles'])}\n"
             f"Cloud: {row['meta_cloud']:.1f}% (scene metadata), "
             f"{row['scl_cloud']:.1f}% (SCL, in bbox)")
    if note:
        title += f"\n{note}"
    fig, ax = plt.subplots(figsize=(10, 10 * (n - s) / ((e - w) * cos_lat) + 1.5))
    ax.imshow(np.moveaxis(rgb, 0, -1), extent=(w, e, s, n), aspect=1 / cos_lat,
              interpolation="nearest")
    ax.set_xlabel("Longitude (deg)")
    ax.set_ylabel("Latitude (deg)")
    ax.ticklabel_format(useOffset=False)
    ax.grid(ls=":", alpha=0.4, color="white")
    ax.set_title("\n".join(textwrap.fill(t, 95) for t in title.split("\n")),
                 fontsize=10)
    if composite == "swir":
        fig.text(0.5, 0.01, SWIR_CAPTION, ha="center", fontsize=8)
        fig.tight_layout(rect=(0, 0.03, 1, 1))
    else:
        fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_web(outdir, comp, rgb):
    """Light WebP for the site (nodata transparent) at the output size."""
    from PIL import Image
    alpha = np.where(rgb.any(axis=0), 255, 0).astype(np.uint8)
    rgba = np.dstack([np.moveaxis(rgb, 0, -1), alpha])
    Image.fromarray(rgba, "RGBA").save(
        os.path.join(outdir, f"{comp}.webp"), quality=85, method=4)


def write_thumb(outdir, rgb, width=480):
    from PIL import Image
    img = Image.fromarray(np.moveaxis(rgb, 0, -1), "RGB")
    img.thumbnail((width, width))
    img.save(os.path.join(outdir, "thumb.jpg"), quality=82)


def num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 1)


def write_run_json(outdir, args, bbox, chosen, reason, fallback, composites,
                   size, n_raw, n_dates):
    stamp = os.environ.get("GITHUB_RUN_ID") or datetime.now(timezone.utc).strftime("%H%M%S")
    area = hashlib.sha1(",".join(f"{v:g}" for v in bbox).encode()).hexdigest()[:6]
    run_url = None
    if os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_REPOSITORY"):
        run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                   f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
                   f"{os.environ['GITHUB_RUN_ID']}")
    info = {
        "id": f"{chosen['date'].replace('-', '')}-{area}-{stamp}",
        "label": args.label.strip(),
        "date": chosen["date"],
        "bbox": list(bbox),
        "tiles": chosen["tiles"],
        "coverage": num(chosen["coverage"]),
        "cloud_meta": num(chosen["meta_cloud"]),
        "cloud_scl": num(chosen["scl_cloud"]),
        "shadow_scl": num(chosen["scl_shadow"]),
        "fallback": bool(fallback),
        "reason": reason,
        "composites": composites,
        "size": list(size),
        "scenes_found": n_raw,
        "dates_considered": n_dates,
        "inputs": {"start_date": args.start_date, "end_date": args.end_date,
                   "max_cloud": args.max_cloud, "accept_cloud": args.accept_cloud,
                   "max_px": args.max_px, "pick": args.pick, "tc_style": args.tc_style,
                   "min_coverage": args.min_coverage},
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_url": run_url,
    }
    with open(os.path.join(outdir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)


def fmt(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1f}"


def write_csv(path, rows, chosen, args):
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["date", "tiles", "coverage_pct", "cloud_meta_pct",
                     "cloud_scl_pct", "shadow_scl_pct", "acceptable", "chosen"])
        for r in rows:
            acceptable = (r["coverage"] >= args.min_coverage
                          and r["cloud"] <= args.accept_cloud)
            wr.writerow([r["date"], " ".join(r["tiles"]), fmt(r["coverage"]),
                         fmt(r["meta_cloud"]), fmt(r["scl_cloud"]),
                         fmt(r["scl_shadow"]), "yes" if acceptable else "no",
                         "yes" if r is chosen else "no"])


def table(rows):
    out = ["| Date | Tiles | Coverage % | Cloud % (meta) | Cloud % (SCL) |",
           "|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['date']} | {' '.join(r['tiles'])} | {fmt(r['coverage'])} "
                   f"| {fmt(r['meta_cloud'])} | {fmt(r['scl_cloud'])} |")
    return out


# --------------------------------------------------------------------------
def run(args):
    try:
        d0, d1 = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    except ValueError:
        raise Fail("start_date and end_date must be YYYY-MM-DD")
    if d0 > d1:
        raise Fail(f"start_date {args.start_date} is after end_date {args.end_date}")
    bbox = parse_bbox(args.bbox)
    os.makedirs(args.outdir, exist_ok=True)

    print(f"Searching {COLLECTION}: bbox={bbox} {args.start_date}..{args.end_date} "
          f"eo:cloud_cover<={args.max_cloud}")
    raw = search_items(args, bbox)
    items = dedupe(raw)
    print(f"{len(raw)} scenes found, {len(items)} after de-duplication; scoring...")

    rows = score_dates(items, bbox, args)
    rows = [r for r in rows if r["coverage"] > 0]
    if not rows:
        raise Fail(f"{len(items)} scenes matched the search, but none had readable "
                   "pixels inside the bbox (reads failed or all nodata).")

    chosen, reason, fallback, others = select(rows, args)
    note = f"FALLBACK: {reason}" if fallback else ""
    print(f"Chosen {chosen['date']}: {reason}")

    composites = (["truecolour", "swir"] if args.composite == "both"
                  else [args.composite])
    stems, rgbs = [], {}
    for comp in composites:
        rgb, transform = render(chosen, bbox, args, comp)
        stem = os.path.join(args.outdir, f"{comp}_{chosen['date']}")
        write_geotiff(stem + ".tif", rgb, transform)
        write_png(stem + ".png", rgb, bbox, chosen, note, comp)
        write_web(args.outdir, comp, rgb)
        rgbs[comp] = rgb
        stems.append(stem)
    write_thumb(args.outdir, rgbs.get("truecolour", next(iter(rgbs.values()))))
    write_csv(os.path.join(args.outdir, "scenes.csv"), rows, chosen, args)
    h, w = next(iter(rgbs.values())).shape[1:]
    write_run_json(args.outdir, args, bbox, chosen, reason, fallback, composites,
                   (w, h), len(raw), len(rows))

    lines = [
        f"## Sentinel-2 true colour: {chosen['date']}",
        "",
        f"- **Chosen date:** {chosen['date']} ({reason})",
    ]
    if fallback:
        lines.append(f"- :warning: **Fallback used.** Nothing met "
                     f"accept_cloud={args.accept_cloud:g}%.")
    lines += [
        f"- **Tiles:** {', '.join(chosen['tiles'])}",
        f"- **Coverage of bbox:** {fmt(chosen['coverage'])}%",
        f"- **Cloud, scene metadata:** {fmt(chosen['meta_cloud'])}%",
        f"- **Cloud, SCL (classes 8/9/10) in bbox:** {fmt(chosen['scl_cloud'])}% "
        "(SCL can mislabel haze/smoke, so eyeball the PNG)",
        f"- **Scenes found:** {len(raw)} ({len(items)} after de-dup) "
        f"on {len(rows)} dates",
        f"- **Inputs:** bbox `{args.bbox}`, {args.start_date} to {args.end_date}, "
        f"max_cloud {args.max_cloud:g}, accept_cloud {args.accept_cloud:g}, "
        f"max_px {args.max_px}, pick `{args.pick}`, composite `{args.composite}`",
        "",
        "### Runner-up dates",
    ]
    lines += table(others[:5]) if others else ["None."]
    lines += ["", "### All dates considered"] + table(rows)
    write_summary(lines)
    print(f"Wrote {', '.join(os.path.basename(s) + '.png/.tif' for s in stems)}, "
          f"scenes.csv in {args.outdir}/")


def main(argv=None):
    args = parse_args(argv)
    try:
        run(args)
    except Fail as exc:
        print(f"::error::{exc}", file=sys.stderr)
        write_summary(["## Sentinel-2 true colour: no image produced", "", str(exc)])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
