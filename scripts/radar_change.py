"""Radar change detection for candidate burned areas (numpy + rasterio.features only).

Idea in plain words: radar echoes from a patch of vegetation are fairly steady from one
pass to the next of the same orbit. Burning removes leaves and branches, so the cross-polar
(VH) echo usually drops. We build a "normal" picture from several earlier passes, compare one
later pass against it, and flag pixels that fell by more than normal pass-to-pass wobble.

This is a *candidate* detector. Clearing, harvest, flooding and wet-soil changes can also
lower VH, so every result needs checking against optical/SWIR images or the ground.
"""
from __future__ import annotations

import numpy as np
from rasterio import features

MIN_DROP_DB = 1.5       # never flag a smaller fall than this
STD_MULT = 1.5          # ...or smaller than this many times the normal wobble
MAX_THRESHOLD_DB = 4.0  # cap so one noisy pixel history cannot hide real change
SCENE_SIGMAS = 3.0      # ...and never below this many times the scene-wide noise (robust spread of all differences)
FALLBACK_STD_DB = 1.0   # wobble assumed when fewer than 3 earlier passes exist
WATER_VH_DB = -22.0     # before-picture darker than this = open water, not vegetation
MIN_PATCH_HA = 0.5
MAX_FEATURES = 1500


def to_db(power):
    return 10.0 * np.log10(np.maximum(power, 1e-6))


def detect(before_vh, after_vh, before_valid, after_valid, px_ha, transform, min_patch_ha=MIN_PATCH_HA):
    """Compare one later VH pass with several earlier ones.

    before_vh: list of linear-power arrays (same grid); before_valid: matching bool masks.
    Returns a dict with the dB difference, masks and statistics.
    """
    stack = np.stack([np.where(v, a, np.nan) for a, v in zip(before_vh, before_valid)])
    count = np.isfinite(stack).sum(axis=0)
    with np.errstate(all="ignore"):
        normal = np.nanmean(stack, axis=0)                # mean in linear power
        if len(before_vh) >= 3:
            std_db = np.nanstd(to_db(stack), axis=0, ddof=1)
        else:
            std_db = np.full(normal.shape, FALLBACK_STD_DB, np.float32)
    std_db = np.where(np.isfinite(std_db), std_db, FALLBACK_STD_DB)

    ok = after_valid & (count >= 2) & np.isfinite(normal)
    normal_db = to_db(np.where(ok, normal, 1.0))
    after_db = to_db(np.where(ok, after_vh, 1.0))
    diff = np.where(ok, normal_db - after_db, 0.0).astype(np.float32)   # + = got darker
    land = ok & (normal_db > WATER_VH_DB)

    # Whole-scene effects (rain-wet ground, calibration) shift every pixel alike: remove the median,
    # and measure how noisy the differences are overall, so the rule adapts to this scene's speckle.
    shift, sigma = 0.0, 0.0
    if land.any():
        shift = float(np.median(diff[land]))
        diff = np.where(ok, diff - shift, 0.0).astype(np.float32)
        sigma = 1.4826 * float(np.median(np.abs(diff[land])))
    floor = max(MIN_DROP_DB, SCENE_SIGMAS * sigma)
    thr = np.maximum(floor, np.minimum(STD_MULT * std_db, MAX_THRESHOLD_DB)).astype(np.float32)

    min_px = max(1, int(round(min_patch_ha / px_ha)))
    drop = sieve(land & (diff >= thr), min_px)
    rise = sieve(land & (-diff >= thr), min_px)
    n_valid = int(ok.sum())
    patches = polygons(drop, transform, px_ha)
    return {
        "diff_db": diff, "drop": drop, "rise": rise, "valid": ok, "threshold_db": thr,
        "patches": patches,
        "stats": {
            "flagged_px": int(drop.sum()),
            "flagged_ha": round(float(drop.sum()) * px_ha, 1),
            "flagged_pct": round(100.0 * drop.sum() / n_valid, 2) if n_valid else 0.0,
            "valid_ha": round(n_valid * px_ha, 1),
            "increase_ha": round(float(rise.sum()) * px_ha, 1),
            "patches": len(patches),
            "min_patch_ha": min_patch_ha,
            "median_threshold_db": round(float(np.median(thr[ok])), 2) if n_valid else None,
            "scene_shift_db": round(shift, 2), "scene_noise_db": round(sigma, 2),
        },
    }


def sieve(mask, min_px):
    """Drop connected patches smaller than min_px pixels (8-connected)."""
    if min_px <= 1 or not mask.any():
        return mask
    out = features.sieve(mask.astype(np.uint8), size=min_px, connectivity=8)
    return out.astype(bool)


def edge(mask, grow=1):
    """Pixels on the border of the flagged patches, thickened by `grow` pixels."""
    inner = mask.copy()
    inner[1:, :] &= mask[:-1, :]
    inner[:-1, :] &= mask[1:, :]
    inner[:, 1:] &= mask[:, :-1]
    inner[:, :-1] &= mask[:, 1:]
    inner[0, :] = inner[-1, :] = False
    inner[:, 0] = inner[:, -1] = False
    ring = mask & ~inner
    for _ in range(grow):
        g = ring.copy()
        g[1:, :] |= ring[:-1, :]
        g[:-1, :] |= ring[1:, :]
        g[:, 1:] |= ring[:, :-1]
        g[:, :-1] |= ring[:, 1:]
        ring = g
    return ring


def base_grey(after_vv_db, valid):
    """Plain grey backdrop from the later VV pass (dark = smooth/wet, bright = rough/built)."""
    g = np.clip((after_vv_db + 22.0) / 22.0, 0, 1)
    return (45 + g * 175).astype(np.uint8) * valid


def _blend(rgb, mask, colour, alpha):
    a = np.asarray(alpha, np.float32)
    if a.ndim == 0:
        a = np.full(mask.shape, float(a), np.float32)
    a = np.where(mask, a, 0.0)[None]
    col = np.asarray(colour, np.float32).reshape(3, 1, 1)
    return (rgb * (1 - a) + col * a)


def change_image(grey, res):
    """Grey base + red/orange where VH fell (flagged), blue where it rose. Returns 8-bit (3,h,w)."""
    rgb = np.repeat(grey[None].astype(np.float32), 3, axis=0)
    mag = np.clip((np.abs(res["diff_db"]) - 1.0) / 5.0, 0, 1)
    t = mag[None]
    drop_col = (np.array([255, 150, 0], np.float32).reshape(3, 1, 1) * (1 - t)
                + np.array([215, 0, 0], np.float32).reshape(3, 1, 1) * t)
    a = np.where(res["drop"], 0.55 + 0.4 * mag, 0.0)[None]
    rgb = rgb * (1 - a) + drop_col * a
    rgb = _blend(rgb, res["rise"], (40, 110, 255), 0.55)
    return np.clip(rgb, 1, 255).astype(np.uint8)


def outline_image(grey, res, grow=1):
    """Grey base + faint red fill and a yellow outline round each flagged patch."""
    rgb = np.repeat(grey[None].astype(np.float32), 3, axis=0)
    rgb = _blend(rgb, res["drop"], (220, 40, 30), 0.25)
    rgb = _blend(rgb, edge(res["drop"], grow), (255, 235, 0), 1.0)
    return np.clip(rgb, 1, 255).astype(np.uint8)


def _ring_area(ring):
    x = np.array([p[0] for p in ring])
    y = np.array([p[1] for p in ring])
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def polygons(mask, transform, px_ha):
    """Each flagged patch as a GeoJSON Feature with its area, largest first."""
    out = []
    if not mask.any():
        return out
    px_deg2 = abs(transform.a * transform.e)
    for geom, val in features.shapes(mask.astype(np.uint8), mask=mask, transform=transform,
                                     connectivity=8):
        if val != 1:
            continue
        rings = [[[round(x, 5), round(y, 5)] for x, y in r] for r in geom["coordinates"]]
        area_px = abs(_ring_area(geom["coordinates"][0])) - sum(
            abs(_ring_area(h)) for h in geom["coordinates"][1:])
        area_px /= px_deg2
        out.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": rings},
                    "properties": {"area_ha": round(area_px * px_ha, 2)}})
    out.sort(key=lambda f: -f["properties"]["area_ha"])
    return out


def candidates_geojson(patches, max_features=MAX_FEATURES):
    """GeoJSON FeatureCollection of the largest patches (capped to keep the file small)."""
    return {"type": "FeatureCollection", "features": patches[:max_features],
            "metadata": {"patches": len(patches), "shown": min(len(patches), max_features)}}
