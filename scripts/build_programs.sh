#!/usr/bin/env bash
# Build the two programs and the example, and refresh the binaries the tests load.
#
#   bash scripts/build_programs.sh          # needs cargo build-sbf (agave 2.3) on PATH
#
#   programs/knos_oidc    real build (no features)            -> programs/target/deploy/knos_oidc.so
#                         test build (--features testkeys)    -> tests/fixtures/knos_oidc_test.so
#   programs/knos_pay     devnet build (default features)     -> programs/target/deploy/knos_pay.so
#                                                                and tests/fixtures/knos_pay_test.so
#                         real-money build (no faucet)        -> tests/fixtures/knos_pay_nodevnet.so
#   examples/oidc_gate    the example consumer                -> tests/fixtures/oidc_gate_test.so
#                         (reads tokens with crates/knos-oidc-interface, not with the program's crate)
#
# The test build of knos-oidc also trusts two seed-derived test keys; it is never deployed. The real build is built
# LAST for each program, so what is left in target/deploy is the binary to deploy. Run this again after changing a
# program id (programs/program_ids.json, OIDC_ID in knos_pay, ID in crates/knos-oidc-interface) or ROTATE_SHA: the
# ids are compiled in.
set -euo pipefail
cd "$(dirname "$0")/.."
fix=tests/fixtures

(cd programs/knos_oidc && cargo build-sbf --features testkeys)
cp programs/target/deploy/knos_oidc.so "$fix/knos_oidc_test.so"
(cd programs/knos_oidc && cargo build-sbf)

(cd programs/knos_pay && cargo build-sbf --no-default-features)
cp programs/target/deploy/knos_pay.so "$fix/knos_pay_nodevnet.so"
(cd programs/knos_pay && cargo build-sbf)
cp programs/target/deploy/knos_pay.so "$fix/knos_pay_test.so"

(cd examples/oidc_gate && cargo build-sbf)
cp examples/oidc_gate/target/deploy/oidc_gate.so "$fix/oidc_gate_test.so"

sha256sum programs/target/deploy/knos_oidc.so programs/target/deploy/knos_pay.so "$fix"/*.so
