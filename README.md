# high_res_sat

Sentinel-2 L2A images from a GitHub Actions workflow, plus a small website to look at them.

- **Workflow** `.github/workflows/s2-truecolour.yml` ("Sentinel-2 true colour"): searches the free
  [Earth Search](https://earth-search.aws.element84.com/v1) STAC catalogue for a bounding box and date range, scores every
  acquisition date for coverage and cloud, picks one, and builds a true-colour and/or SWIR image of it.
- **Viewer** `site/`, published to GitHub Pages from the `s2-output` branch: a gallery of runs, a zoom/pan viewer,
  a compare slider and a map overlay.

No API keys, no paid services.

## Run it

GitHub web or mobile app: **Actions** → **Sentinel-2 true colour** → **Run workflow**.

| Input | Default | Meaning |
|---|---|---|
| `start_date`, `end_date` | required | `YYYY-MM-DD`, end date inclusive |
| `bbox` | `114,-2,115,-1` | `min_lon,min_lat,max_lon,max_lat` |
| `max_cloud` | 60 | Scene-level `eo:cloud_cover` pre-filter (whole tile, %). Use ~100 to switch it off. |
| `accept_cloud` | 20 | Max SCL cloud (%) **inside the bbox** for a date to count as acceptable |
| `max_px` | 2500 | Longest side of the output image |
| `composite` | `truecolour` | `truecolour`, `swir` (B12/B8A/B4: burn scars, sees through thin smoke) or `both` |
| `label` | blank | Optional area name shown on the site (blank = the bbox) |
| `publish` | on | Add the run to the viewer site |

**Date choice:** the most recent date with at least 90 % bbox coverage and SCL cloud at or below `accept_cloud`.
If none qualifies it falls back to the clearest well-covered date (then the best-covered one) and says so in the
job summary, on the PNG and on the site. One date only: tiles from different days are never mixed.

**Outputs** (workflow artifact `s2-truecolour`, 30 days): `<composite>_<date>.png` (axes, title, cloud figures),
`<composite>_<date>.tif` (georeferenced GeoTIFF), light `*.webp` copies and `thumb.jpg` for the site,
`scenes.csv` (every date considered), `run.json`.

## The viewer

Each run with `publish` on adds `runs/<id>/` to the `s2-output` branch and rebuilds the site
(`scripts/publish_site.sh`, `scripts/build_site.py`). The branch is force-pushed as one commit holding every run,
so history never grows, but **nothing is deleted**: each run adds roughly 10-20 MB (annotated PNGs are the bulk;
GeoTIFFs are not published, they stay in the artifact). GitHub Pages sites have a 1 GB limit, so prune old
`runs/` folders on the branch every so often.

**Enable Pages once:** Settings → Pages → Build and deployment → Source: *Deploy from a branch* →
Branch `s2-output`, folder `/ (root)` → Save. The site is then at `https://<owner>.github.io/<repo>/`.
Pages sites are public, and on a private repository Pages needs a paid GitHub plan.

Views: **View** (zoom with wheel, pinch or +/-, drag to pan, coordinates under the cursor), **Compare** (slider between any
two images of the same area: true colour vs SWIR, or two dates), **Map** (the image over OpenStreetMap or Esri imagery,
with an opacity slider; accurate to a few pixels for boxes of a few degrees near the equator). The page state is in the URL
hash, so links to a run or view can be shared.

## Run locally

```bash
pip install pystac-client rasterio numpy matplotlib pillow
python scripts/s2_truecolour.py --start-date 2026-08-01 --end-date 2026-08-31 \
    --bbox 114,-2,115,-1 --max-cloud 100 --accept-cloud 40 --composite both --outdir output
python scripts/build_site.py some_dir      # needs some_dir/runs/<id>/ copied from an output dir
```

## Notes

- SCL cloud can label thick smoke or bright bare ground (sand, mining) as cloud, and misses thin haze. Always look at the image.
- Over the humid tropics many passes are almost fully cloudy; widen the dates or raise `accept_cloud` if nothing qualifies.
- Contains modified Copernicus Sentinel data.
