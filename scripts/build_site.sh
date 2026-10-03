#!/usr/bin/env bash
# Assemble the Pages site:  bash scripts/build_site.sh <out dir> <commit sha>
# The web app, the JavaScript client it imports, the claim workflow and the second deployment's program ids (the
# first deployment's addresses are in the page, as history). A template that still names its workflows by
# KNOS_COMMIT_SHA is given the commit the site was built from; the page hands out a file only when every workflow it
# calls is named by a full commit, so a placeholder left in one is said on the page and never handed out.
set -euo pipefail
cd "$(dirname "$0")/.."
out="$1"; sha="$2"
case "$sha" in *[!0-9a-f]*|"") echo "the commit must be a full sha"; exit 1;; esac
[ "${#sha}" = 40 ] || { echo "the commit must be a full sha"; exit 1; }
mkdir -p "$out"
cp -r web/. "$out/"
cp src/knos/settle/v2/program_ids.json "$out/program_ids.json"
cp sdk/settle/index.js "$out/settle.js"
cp examples/knos-claim.yml "$out/knos-claim.yml"
sed -i.bak "s/KNOS_COMMIT_SHA/$sha/g" "$out/front.js" && rm -f "$out/front.js.bak"
! grep -q KNOS_COMMIT_SHA "$out/front.js"
