#!/usr/bin/env bash
# Deploy the second deployment (programs-v2) to devnet, set it up, and hand both programs' upgrade authority to the
# vault of the upgrade multisig, so that from then on a program changes only by a proposal that waits 48 hours in
# public. Run it again after any failure: every step reads the chain first and does only what is missing. The last
# line it prints says what is on chain now.
#
#   bash scripts/deploy_v2.sh               devnet (or the cluster of KNOS_RPC; mainnet-beta is refused)
#   bash scripts/deploy_v2.sh --localnet    the same steps against a local validator this script starts and stops:
#                                           a dry run with the real keys that touches no cluster. With KNOS_RPC set
#                                           it uses the local validator or Surfpool already running there instead.
#   bash scripts/deploy_v2.sh --localnet --keep     leaves that validator running, to try governance.mjs against it
#
# Steps (each reads the chain first; a step that is already done says so and sends nothing)
#   1 multisigs  both Squads multisigs exist as the design fixes them (governance.mjs show --check); created with
#                governance.mjs create when they do not (it takes the member keys of the key folder, threshold 2)
#   2 build      the verified build of both programs: solana-verify build programs-v2 --library-name knos_oidc, then
#                knos_pay, in the docker image program.yml pins; the default features, which is the devnet build.
#                Prints each executable hash. Not repeated while programs-v2 is unchanged since the last build.
#   3 deploy     solana program deploy, each program under its own keypair, with a buffer keypair kept in the key
#                folder (the same command continues a deploy that failed half way), --max-sign-attempts 60 and a
#                compute unit price. Skipped for a program whose on-chain bytes are already this build.
#   4 faucet     InitFaucet: the escrow's test-USDC mint
#   5 keys       RegisterKey and KeyParams for GitHub's four genesis keys. They verify for 30 days from now; the
#                rotate workflow's Refresh keeps them alive after that
#   6 fee        the fee owner's token account for the faucet's mint
#   7 hand-over  after checking both multisigs on chain once more: solana program set-upgrade-authority to the
#                upgrade vault, with --skip-new-upgrade-authority-signer-check (a vault cannot sign from a command
#                line). Last, because nothing above can be done by this script afterwards.
#   then         every address of the deployment, every transaction on chain that touches one, and one line that
#                says what is on chain now. The exit code is 0 only when the deployment is complete.
#
# What it reads
#   KNOS_KEYS          the key folder (default: .knos-keys in the repository, which git ignores). It holds
#                      knos_oidc_v2-keypair.json, knos_pay_v2-keypair.json, upgrade-create-key.json,
#                      guardian-create-key.json, payer.json, member-1.json, member-2.json, member-3.json; this script
#                      adds the two buffer keypairs
#   KNOS_FEE_PAYER     the fee payer's keypair (default: payer.json in the key folder). It pays, and it is the
#                      programs' upgrade authority until step 8
#   KNOS_MEMBERS       the multisig members' keypair files or addresses, separated by spaces (default: member-N.json)
#   KNOS_RPC           the cluster (default https://api.devnet.solana.com)
#   KNOS_SO_DIR        a folder with knos_oidc.so and knos_pay.so to deploy instead of building: the verified-build
#                      artifacts of program.yml, say. Step 2 then only prints their hashes
#   KNOS_PRIORITY_FEE  micro-lamports per compute unit for the deploy (default 1000)
#   KNOS_USE_RPC=1     send the deploy's writes through the RPC endpoint (--use-rpc): for a provider's endpoint. It is
#                      always on for an endpoint on this machine (a local validator or a Surfpool fork)
#   KNOS_CLONE_FROM    --localnet: the cluster whose Squads program the local validator copies (default
#                      https://api.devnet.solana.com, the one the real deploy will meet)
#   KNOS_LOCAL_PORT    --localnet: the local validator's RPC port (default 8899)
#   PYTHON             the Python that has this repository's requirements (default python3; pip install -e .)
#
# Needs: solana and solana-keygen (agave 2.3 or later), node 20 or later with `npm ci --prefix scripts` done, Python
# with this repository's requirements (pip install -e .), and for step 2 solana-verify and a running docker.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD

LOCALNET=0 KEEP=0
for arg in "$@"; do
  case "$arg" in
    --localnet) LOCALNET=1 ;;
    --keep) KEEP=1 ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "usage: bash scripts/deploy_v2.sh [--localnet [--keep]]" >&2; exit 2 ;;
  esac
done

KEYS="${KNOS_KEYS:-$ROOT/.knos-keys}"
PAYER="${KNOS_FEE_PAYER:-$KEYS/payer.json}"
PRICE="${KNOS_PRIORITY_FEE:-1000}"
PYTHON="${PYTHON:-python3}"
VERIFY_IMAGE="${KNOS_VERIFY_IMAGE:-solanafoundation/solana-verifiable-build:2.3.11}"   # the image program.yml builds in
RPC="${KNOS_RPC:-https://api.devnet.solana.com}"
PROGRAMS="knos_oidc knos_pay"
WORK="" VALIDATOR=""

die() { echo "stopped: $*" >&2; exit 1; }
step() { echo; echo "[$1/7] $2"; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is not on PATH. $2"; }
pinned() { "$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$ROOT/programs-v2/program_ids.json" "$1"; }
py() { PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$ROOT/scripts/deploy_v2.py" --rpc "$RPC" --payer "$PAYER" "$@"; }
governance() { KNOS_KEYS="$KEYS" KNOS_FEE_PAYER="$PAYER" node "$ROOT/scripts/governance.mjs" "$@" --rpc "$RPC"; }
sol() { solana --url "$RPC" --keypair "$PAYER" --commitment confirmed "$@"; }
# sol, tried up to three times: a public endpoint drops requests, and a fork reading an account for the first time can fail
# the first try. Only for a command that continues where it stopped when it is sent again (the deploy does: same buffer)
sol_again() {
  local n=1
  until sol "$@"; do
    [ "$n" -lt 3 ] || return 1
    n=$((n + 1)); echo "  the solana command failed: trying again in 5 seconds (try $n of 3)" >&2; sleep 5
  done
}
lamports() { sol "$@" --lamports | awk 'NF >= 2 { print $(NF - 1); exit }'; }
# HAVE (the executable hash on chain, or "absent") and AUTHORITY (the upgrade authority, or "none") of a program
program_state() {
  local state
  state="$(py program "$1")" || die "cannot read $1 from $RPC. Run this script again."
  read -r HAVE AUTHORITY <<< "$state"
}

cleanup() {
  if [ -n "$VALIDATOR" ] && [ "$KEEP" = 0 ]; then kill "$VALIDATOR" 2>/dev/null || true; wait "$VALIDATOR" 2>/dev/null || true; fi
  if [ -n "$WORK" ] && [ "$KEEP" = 0 ]; then rm -rf "$WORK"; fi
}
trap cleanup EXIT

# ---- a local validator for the dry run ------------------------------------------------------------------------------
start_localnet() {
  need solana-test-validator "It comes with the Solana command line tools."
  local port="${KNOS_LOCAL_PORT:-8899}" from="${KNOS_CLONE_FROM:-https://api.devnet.solana.com}"
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/knos-localnet.XXXXXX")"
  RPC="http://127.0.0.1:$port"
  # the one program this deployment meets on a cluster that it does not deploy: Squads v4, with its config account
  solana-test-validator --reset --quiet --ledger "$WORK/ledger" --bind-address 127.0.0.1 --rpc-port "$port" --faucet-port "$((port + 2))" \
    --gossip-port "$((port + 3))" --dynamic-port-range "$((port + 4))-$((port + 40))" --url "$from" \
    --clone-upgradeable-program "$(pinned squads_program)" --clone BSTq9w3kZwNwpBXJEvTZz2G9ZTNyKBvoSeXMvwb4cNZr > "$WORK/validator.log" 2>&1 &
  VALIDATOR=$!
  for _ in $(seq 90); do
    solana --url "$RPC" cluster-version >/dev/null 2>&1 && break
    kill -0 "$VALIDATOR" 2>/dev/null || { tail -5 "$WORK/validator.log" >&2; die "the local validator did not start (it copies the Squads program from $from: set KNOS_CLONE_FROM to a cluster you can reach)."; }
    sleep 1
  done
  solana --url "$RPC" cluster-version >/dev/null 2>&1 || die "the local validator did not answer on $RPC within 90 seconds."
  echo "local validator on $RPC (ledger $WORK/ledger, the Squads program copied from $from)"
}

localnet_keys() {
  # the dry run uses the real program keypairs and create keys (the addresses are pinned); what only pays or votes
  # may be a throwaway key
  [ -n "$WORK" ] || WORK="$(mktemp -d "${TMPDIR:-/tmp}/knos-localnet.XXXXXX")"
  if [ ! -f "$PAYER" ]; then
    PAYER="$WORK/payer.json"
    solana-keygen new --no-bip39-passphrase --silent --force --outfile "$PAYER" >/dev/null
    echo "no fee payer key file: using a throwaway one for this dry run"
  fi
  if [ -z "${KNOS_MEMBERS:-}" ] && [ ! -f "$KEYS/member-1.json" ]; then
    KNOS_MEMBERS=""
    for n in 1 2 3; do
      solana-keygen new --no-bip39-passphrase --silent --force --outfile "$WORK/member-$n.json" >/dev/null
      KNOS_MEMBERS="$KNOS_MEMBERS $WORK/member-$n.json"
    done
    export KNOS_MEMBERS
    echo "no member key files: using three throwaway members for this dry run"
  fi
  solana --url "$RPC" airdrop 100 "$(solana-keygen pubkey "$PAYER")" >/dev/null || die "the local cluster at $RPC gave the fee payer no SOL."
}

# ---- the steps ------------------------------------------------------------------------------------------------------
multisigs() {
  if governance show --check >/dev/null 2>&1; then echo "  both multisigs exist as the design fixes them"; return; fi
  governance create | sed 's/^/  /'
  governance show --check >/dev/null || die "the multisigs are not right on chain: node scripts/governance.mjs show --rpc $RPC says what is wrong."
}

sources_hash() { (cd "$ROOT/programs-v2" && find . -type f -not -path './target/*' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1); }

build() {
  if [ -n "${KNOS_SO_DIR:-}" ]; then
    SO_DIR="$(cd "$KNOS_SO_DIR" && pwd)"
    echo "  not built here: deploying the files of $SO_DIR"
  else
    SO_DIR="$ROOT/programs-v2/target/deploy"
    local stamp="$SO_DIR/.verified-build" want
    want="$(sources_hash) $VERIFY_IMAGE"
    if [ -f "$stamp" ] && [ "$(head -1 "$stamp")" = "$want" ] && (cd "$SO_DIR" && tail -n +2 .verified-build | sha256sum --check --status); then
      echo "  programs-v2 is unchanged since the last verified build: not built again"
    else
      need solana-verify "Install it: cargo install --locked solana-verify"
      docker info >/dev/null 2>&1 || die "docker is not running, and the verified build runs in it. Start docker; or deploy files built elsewhere with KNOS_SO_DIR."
      rm -f "$stamp"
      for name in $PROGRAMS; do
        solana-verify build "$ROOT/programs-v2" --library-name "$name" --base-image "$VERIFY_IMAGE"
      done
      { echo "$want"; (cd "$SO_DIR" && sha256sum knos_oidc.so knos_pay.so); } > "$stamp"
    fi
  fi
  for name in $PROGRAMS; do
    [ -f "$SO_DIR/$name.so" ] || die "$SO_DIR/$name.so is missing."
    echo "  $name: executable hash $(py hash "$SO_DIR/$name.so")"
  done
}

deploy() {
  local name id key buffer so want size cost balance
  for name in $PROGRAMS; do
    id="$(pinned "$name")" key="$KEYS/${name}_v2-keypair.json" buffer="$KEYS/${name}_v2-buffer.json" so="$SO_DIR/$name.so"
    want="$(py hash "$so")"
    program_state "$name"
    if [ "$HAVE" = "$want" ]; then echo "  $name $id: this build is on chain already"; continue; fi
    [ -f "$key" ] || die "$key is missing: the program's keypair, whose address is $id."
    [ "$(solana-keygen pubkey "$key")" = "$id" ] || die "$key is the keypair of $(solana-keygen pubkey "$key"), not of $name ($id)."
    if [ "$HAVE" != absent ] && [ "$AUTHORITY" != "$PAYER_ADDRESS" ]; then
      die "$name $id is deployed with another build ($HAVE), and its upgrade authority is $AUTHORITY, not the fee payer. An upgrade now goes through the multisig: write the new build to a buffer, then node scripts/governance.mjs upgrade propose $name <buffer address>"
    fi
    size="$(wc -c < "$so")"
    # what the deploy holds at its peak: the buffer (it comes back) and the program data, each with room for twice the size
    cost=$(( $(lamports rent $((2 * size + 37))) + $(lamports rent $((2 * size + 45))) + 50000000 ))
    balance="$(lamports balance "$PAYER_ADDRESS")"
    [ "$balance" -ge "$cost" ] || die "the fee payer $PAYER_ADDRESS holds $balance lamports and deploying $name takes about $cost (part of it comes back). Fund it: solana airdrop 2 $PAYER_ADDRESS --url $RPC, or https://faucet.solana.com"
    [ -f "$buffer" ] || solana-keygen new --no-bip39-passphrase --silent --outfile "$buffer" >/dev/null
    # shellcheck disable=SC2086  # USE_RPC is one flag or nothing
    if ! sol_again program deploy "$so" --program-id "$key" --buffer "$buffer" --upgrade-authority "$PAYER" --fee-payer "$PAYER" \
      --max-len $((2 * size)) --max-sign-attempts 60 --with-compute-unit-price "$PRICE" $USE_RPC 2>&1 | sed 's/^/  /'; then
      die "deploying $name did not finish. Run this script again: the same buffer ($buffer) continues the deploy where it stopped."
    fi
    program_state "$name"
    [ "$HAVE" = "$want" ] || die "$name was deployed but the chain shows $HAVE, not this build ($want). Run this script again."
    echo "  $name $id: deployed, executable hash $HAVE"
  done
}

handover() {
  local name id vault
  vault="$(pinned upgrade_authority)"
  governance show --check >/dev/null || die "the multisigs are not right on chain (node scripts/governance.mjs show --rpc $RPC says what is wrong): the upgrade authority was NOT handed over."
  for name in $PROGRAMS; do
    id="$(pinned "$name")"
    program_state "$name"
    if [ "$AUTHORITY" = "$vault" ]; then echo "  $name: its upgrade authority is the upgrade vault already"; continue; fi
    [ "$AUTHORITY" = "$PAYER_ADDRESS" ] || die "$name's upgrade authority is $AUTHORITY: neither the fee payer nor the upgrade vault. Nothing was changed."
    # not sent again by itself: a second try after a lost answer would fail because the first one worked. The chain is asked instead.
    sol program set-upgrade-authority "$id" --new-upgrade-authority "$vault" --skip-new-upgrade-authority-signer-check --upgrade-authority "$PAYER" 2>&1 | sed 's/^/  /' || true
    program_state "$name"
    [ "$AUTHORITY" = "$vault" ] || die "$name's upgrade authority is still $AUTHORITY. Run this script again."
    echo "  $name: upgrade authority handed to the upgrade vault $vault"
  done
}

# ---- run ------------------------------------------------------------------------------------------------------------
need solana "Install the Solana command line tools (agave 2.3 or later)."
need solana-keygen "It comes with the Solana command line tools."
need node "Install Node 20 or later."
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)' || die "node is older than 20 ($(node --version)): governance.mjs needs Node 20 or later."
[ -d "$ROOT/scripts/node_modules/@sqds/multisig" ] || die "the scripts' packages are not installed. Run: npm ci --prefix scripts"
PYTHONPATH="$ROOT/src" "$PYTHON" -c 'import solders' 2>/dev/null || die "$PYTHON cannot import solders. Run: pip install -e .   (or set PYTHON to the Python that has it)"

USE_RPC=""
if [ "${KNOS_USE_RPC:-0}" = 1 ]; then USE_RPC="--use-rpc"; fi
if [ "$LOCALNET" = 1 ] && [ -z "${KNOS_RPC:-}" ]; then start_localnet; fi
# an endpoint on this machine (a validator, a Surfpool fork) has no TPU to send to: the deploy goes through the RPC
case "$RPC" in http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) USE_RPC="--use-rpc" ;; esac
CLUSTER="$(py cluster)" || die "cannot reach the cluster at $RPC. Set KNOS_RPC to an endpoint that answers."
# a validator on this machine is local whatever it says it is: a Surfpool fork answers with the genesis of the cluster it copies
case "$RPC" in http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) CLUSTER="local ($CLUSTER)" ;; esac
if [ "$LOCALNET" = 1 ]; then
  case "$CLUSTER" in local*) ;; *) die "--localnet was asked, and $RPC is $CLUSTER, not a validator on this machine. Unset KNOS_RPC, or point it at one." ;; esac
  localnet_keys
elif [ "$CLUSTER" = mainnet-beta ]; then
  die "$RPC is mainnet-beta. This is the devnet deployment: a mainnet deployment needs program ids of its own and an outside review first (knos mainnet-check)."
fi
[ -f "$PAYER" ] || die "the fee payer's keypair $PAYER is missing. Set KNOS_FEE_PAYER, or put payer.json in the key folder $KEYS."
PAYER_ADDRESS="$(solana-keygen pubkey "$PAYER")"
echo "cluster $CLUSTER ($RPC); fee payer $PAYER_ADDRESS; key folder $KEYS"

step 1 "the two multisigs"; multisigs
step 2 "the verified build"; build
step 3 "deploy"; deploy
step 4 "the faucet's test-USDC mint"; py faucet
step 5 "GitHub's four genesis keys"; py keys
step 6 "the fee owner's token account"; py fee-account
step 7 "the upgrade authority goes to the upgrade vault"; handover

echo
echo "addresses"
py addresses
echo
echo "transactions on chain that touch them, oldest first"
py transactions "$CLUSTER"
echo
if [ -n "$VALIDATOR" ] && [ "$KEEP" = 1 ]; then echo "the local validator is still running on $RPC (pid $VALIDATOR, ledger $WORK/ledger): stop it with kill $VALIDATOR"; fi
py summary
