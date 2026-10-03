#!/usr/bin/env bash
# Build the second deployment's programs (programs-v2) and refresh the binaries the tests load.
#
#   bash scripts/build_programs_v2.sh [oidc|pay|all]     # default: all. Needs cargo build-sbf on PATH
#
#   programs-v2/knos_oidc   test build (--features testkeys)                  -> tests/fixtures/knos_oidc_v2_test.so
#                           real build (no features)                          -> tests/fixtures/knos_oidc_v2_real.so
#                                                                                and $target/deploy/knos_oidc.so
#   programs-v2/knos_pay    test build (--features testkeys, devnet on)       -> tests/fixtures/knos_pay_v2_test.so
#                           test build, real-money rules (no devnet feature)  -> tests/fixtures/knos_pay_v2_nodevnet.so
#                           devnet build (default features)                   -> $target/deploy/knos_pay.so
#
# $target is CARGO_TARGET_DIR when it is set, and programs-v2/target otherwise.
#
# A testkeys build also trusts two seed-derived signing keys, a test rotate pin, the test harness's repository as
# an attester and a test guardian (programs-v2/knos_oidc/src/pins.rs); it is never deployed. The real build is
# built LAST for each program, so what is left in $target/deploy is the binary to try on a cluster. The binary that
# is deployed is the reproducible one (`solana-verify build programs-v2 --library-name <name>`), not this one.
#
# Each fixture is copied right after its own build: the next build overwrites $target/deploy/<name>.so. The hashes
# of the fixtures are pinned in tests/fixtures/SHA256SUMS; this script rewrites the lines of the ones it built, so a
# changed binary shows up in review as a changed pin. Run it again after changing a program id
# (programs-v2/program_ids.json, OIDC_ID in knos_pay), a pin in pins.rs, or any source file of a program.
set -euo pipefail
cd "$(dirname "$0")/.."
what="${1:-all}"
fix=tests/fixtures
target="${CARGO_TARGET_DIR:-$PWD/programs-v2/target}"
built=()

# build <crate> <fixture name, or - for none> [cargo build-sbf arguments]
build() {
  local crate="$1" out="$2"; shift 2
  # cargo build-sbf leaves a deployed file alone when it looks newer than what was just built: remove the last one, so
  # that what is copied below is this build and never the one before it
  rm -f "$target/deploy/$crate.so"
  (cd "programs-v2/$crate" && cargo build-sbf "$@")
  if [ "$out" != "-" ]; then
    cp "$target/deploy/$crate.so" "$fix/$out"
    built+=("$fix/$out")
  fi
}

# the pin of one fixture: replace its line in SHA256SUMS, or add it
pin() {
  local sum; sum="$(sha256sum "$1" | cut -d' ' -f1)"
  grep -v "  $1\$" "$fix/SHA256SUMS" > "$fix/SHA256SUMS.new" || true
  echo "$sum  $1" >> "$fix/SHA256SUMS.new"
  mv "$fix/SHA256SUMS.new" "$fix/SHA256SUMS"
}

case "$what" in oidc|pay|all) ;; *) echo "usage: bash scripts/build_programs_v2.sh [oidc|pay|all]" >&2; exit 2 ;; esac

if [ "$what" = oidc ] || [ "$what" = all ]; then
  build knos_oidc knos_oidc_v2_test.so --features testkeys
  build knos_oidc knos_oidc_v2_real.so
fi

if [ "$what" = pay ] || [ "$what" = all ]; then
  build knos_pay knos_pay_v2_nodevnet.so --no-default-features --features testkeys
  build knos_pay knos_pay_v2_test.so --features testkeys
  build knos_pay -
fi

for f in "${built[@]}"; do pin "$f"; done
sha256sum "${built[@]}"
ls "$target/deploy/"*.so | xargs sha256sum
