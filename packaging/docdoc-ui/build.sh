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
console="$package/src/docdoc_ui/console"

echo "==> building the operations console"
(cd "$root/ui" && npm ci && npm run build)

if [[ ! -f "$root/ui/dist/index.html" ]]; then
  echo "ui/dist/index.html is missing — the build produced nothing to package" >&2
  exit 1
fi

# `rm -rf` and not just an overwrite: a stale `assets/` from before ADR-0020
# holds a built *viewer*, and a wheel that shipped one would serve it. Removed
# here rather than left to a developer's `git clean`, because the directory is
# gitignored and `git clean` is exactly what nobody runs.
echo "==> copying assets into the distribution"
rm -rf "$console" "$package/src/docdoc_ui/assets"
mkdir -p "$console"
cp -R "$root/ui/dist/." "$console/"

# `uv build` rather than `python -m build`, which is what this said until T082
# and which is not a dependency this repository declares — the documented
# command simply failed. uv is already the tool every other workflow here uses,
# so the build needs nothing installed that a contributor does not already have.
echo "==> building the wheel"
(cd "$package" && uv build --wheel --out-dir dist)

echo "==> done: $package/dist"
ls -1 "$package/dist"
