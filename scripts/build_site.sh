#!/usr/bin/env bash
# Assemble the Pages site:  bash scripts/build_site.sh <out dir> <commit sha>
# The web app, the JavaScript client it imports, the claim workflow it prefills, and the program ids. The site hands
# out Knos's workflows pinned to the commit it was built from.
set -euo pipefail
cd "$(dirname "$0")/.."
out="$1"; sha="$2"
case "$sha" in *[!0-9a-f]*|"") echo "the commit must be a full sha"; exit 1;; esac
[ "${#sha}" = 40 ] || { echo "the commit must be a full sha"; exit 1; }
mkdir -p "$out"
cp -r web/. "$out/"
cp src/knos/settle/program_ids.json "$out/program_ids.json"
cp sdk/settle/index.js "$out/settle.js"
cp examples/knos-claim.yml "$out/knos-claim.yml"
sed -i.bak "s/KNOS_COMMIT_SHA/$sha/g" "$out/front.js" && rm -f "$out/front.js.bak"
! grep -q KNOS_COMMIT_SHA "$out/front.js"
