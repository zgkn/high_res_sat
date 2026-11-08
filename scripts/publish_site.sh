#!/usr/bin/env bash
# Add one run's output to the viewer site branch and republish it.
#
#   scripts/publish_site.sh <run output dir>   add a run, rebuild and publish the site
#   scripts/publish_site.sh                    only rebuild the site (new page code, no new run)
#
# Reads the existing output branch (if any), adds runs/<id>/ from the run output dir, rebuilds the
# site with scripts/build_site.py and force-pushes the result as a single commit, so the branch
# keeps every run but never accumulates history. Needs GH_TOKEN and GITHUB_REPOSITORY, or
# REMOTE_URL to point at any git remote (handy for testing). GeoTIFFs are not published: they
# stay in the workflow artifact.
set -euo pipefail

src=${1:-}
branch=${OUTPUT_BRANCH:-s2-output}
remote=${REMOTE_URL:-https://x-access-token:${GH_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
pub="$work/pub"
trap 'rm -rf "$work"' EXIT

if git ls-remote --exit-code --heads "$remote" "$branch" >/dev/null 2>&1; then
  git clone --quiet --depth 1 --branch "$branch" "$remote" "$pub"
  rm -rf "$pub/.git"
else
  mkdir -p "$pub"
fi

if [ -n "$src" ]; then
  id=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$src/run.json")
  mkdir -p "$pub/runs/$id"
  cp "$src"/run.json "$src"/scenes.csv "$src"/thumb.jpg "$src"/*.webp "$src"/*.png "$pub/runs/$id/"
  msg="Publish $id"
else
  [ -d "$pub/runs" ] || { echo "Nothing to rebuild: branch $branch has no runs yet." >&2; exit 1; }
  msg="Rebuild site"
fi

python3 "$root/scripts/build_site.py" "$pub"

cd "$pub"
git init --quiet -b "$branch"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit --quiet -m "$msg"
git push --quiet --force "$remote" "$branch"
echo "$msg -> branch $branch"
