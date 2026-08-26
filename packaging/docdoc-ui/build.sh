#!/usr/bin/env bash
# Build the `docdoc-ui` distribution from a clean checkout.
#
#   ./packaging/docdoc-ui/build.sh
#
# Two steps, in this order, and the order is the point: the assets are copied in
# immediately before the wheel is built and are never committed. A checkout
# therefore holds no build output at any moment (FR-038), which is what makes
# `git status` a meaningful check in CI rather than a formality.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
package="$root/packaging/docdoc-ui"
assets="$package/src/docdoc_ui/assets"

echo "==> building the browser client"
(cd "$root/ui" && npm ci && npm run build)

if [[ ! -f "$root/ui/dist/index.html" ]]; then
  echo "ui/dist/index.html is missing — the build produced nothing to package" >&2
  exit 1
fi

echo "==> copying assets into the distribution"
rm -rf "$assets"
mkdir -p "$assets"
cp -R "$root/ui/dist/." "$assets/"

# `uv build` rather than `python -m build`, which is what this said until T082
# and which is not a dependency this repository declares — the documented
# command simply failed. uv is already the tool every other workflow here uses,
# so the build needs nothing installed that a contributor does not already have.
echo "==> building the wheel"
(cd "$package" && uv build --wheel --out-dir dist)

echo "==> done: $package/dist"
ls -1 "$package/dist"
