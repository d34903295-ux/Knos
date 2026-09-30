# Knos bench

Re-run it to check every number. `knos bench` measures the single-machine bars; `knos bench --chain URL` measures the
team bars on a local validator. The methods are in `src/knos/bench.py` and `src/knos/bench_chain.py`.

## Team bars (0.3): `knos bench --chain http://127.0.0.1:8899`

On a local validator running the devnet-deployed Solana Attestation Service and Lighthouse (`scripts/devchain.sh
start`). Method: `src/knos/bench_chain.py`.

| vector | knos | without knos | ratio / target |
|---|---|---|---|
| **Security**, metric 1: conflicting writes to working trees (3 machines × 3 vendor hooks × 200 rounds, 4 files) | **0** (of 738 writes the guard allowed) | advisory at 0.9 compliance: 149; none: 1,062 (of 1,800) | (149+1)/(0+1) = **150×** |
| **Security**, metric 2: conflicting commits | **0** | advisory with its commit guard modelled: 3 | (3+1)/(0+1) = 4×; a model of Agent Mail's guard comes close here, as expected |
| **Budget**, Solana: overspends signed directly with the agent's delegate key (200) | 0 moved; 200/200 rejected | | all rejected |
| **Budget**, Tempo Moderato: over-limit payments signed directly with the agent's access key, with no client-side checks (200) | 0 received; 200/200 reverted on chain; fees 44 units (0.000044 USD) each, from the allowance | | all rejected |
| **Speed**: guard decision when the agent holds the claim (reads the mirror) | p50 2.7 ms / p95 25.6 ms | | p95 ≤ 100 ms |
| **Speed**: edits that waited on the chain (20 edits per claimed unit) | 5% (the first edit of each unit) | | ≤ 5% |
| **Cost**: placing one claim | 5,000 lamports fee + 2,797,920 lamports rent, refunded on release | Agent Mail across machines: an always-on host, from $1.94 / month ([COMPARE.md](COMPARE.md)) | $0 servers |

**Capability** (the coverage matrix: 4 vendors × single/multi machine × edit/commit enforcement × bypass-proof
budget = 32 cells):
- **Knos: 32.** Every vendor's edit hook, on one machine (`tests/test_guard.py`, `tests/test_codex_guard.py`) and
  across machines (`tests/test_team_two_homes.py`: Claude Code, Cursor, Codex, OpenCode). The commit guard for every
  host. Chain budgets that hold when Knos is bypassed (`tests/test_chain_budgets.py`, and the Tempo run above).
- **Best alternative, MCP Agent Mail: 8.** Its reservations are advisory at edit time and its pre-commit guard blocks
  commits, on one machine or across machines through its server, with no budgets
  ([README](https://github.com/Dicklesworthstone/mcp_agent_mail), read 30 Sep 2026).
- 32 / 8 = **4×**.

**Claim protocol property test** (`tests/test_team_property.py`):
- Setup: five member keys, overlapping files and folders, decisions read through an RPC that lags 2–5 slots, dust sent
  to claim addresses, and claimers that crash after creating.
- 1,000 rounds on the current code (four concurrent runs of 250, different seeds): 5,000 claims, 2,059 winners,
  2,607 that lost and closed their own claim, 317 crashed claimers, 300 dust transfers, 17 answered "offline" (the
  RPC took too long; the guard lets that edit go ahead with a warning).
- **Double winners: 0. Winners ever blocked: 0. Overlapping claimers not refused: 0.**
- 3,469 reads were served stale by the lagging endpoint, and 29,073 were refused until it reached the asked slot.
- An earlier run on older code (whole-program reads) completed 763 rounds with the same zeros before a harness
  timeout.

**Signer cap:** 30 member keys. 31 makes the ChangeAuthorizedSigners transaction 1,648 bytes, over the 1,232-byte
limit (measured on the local validator; `knos team status` prints it).

**Limits, said plainly:**
- The advisory arm is a model of Agent Mail's reservations and commit guard, not Agent Mail itself.
- The chain numbers come from a local validator on a busy laptop; devnet adds network latency to the first claim of
  each file (the recorded devnet run is in [network/demo.md](network/demo.md)).
- When the guard cannot get the chain's word within its 1-second budget, it lets the edit go ahead with a warning
  ("fail-open"), because Knos never blocks work on an outage. The bench counts those edits separately: there were
  none in the run above.
- An earlier 200-round run, on the same laptop while a 2.5-hour test suite ran against the same validator, recorded
  15 conflicting writes in the Knos arm. That run did not yet count fail-open edits, so we cannot show how many of
  the 15 were fail-open. Re-running on a quieter machine gave the 0 above, and the property test (which never
  fails open) found no double winners. We report both runs.

## Single-machine bars

Measured by `knos bench` on 2026-09-29 with 0.2.0. In 0.3 the single-machine path changed only in where memory is stored (Sibyl's own store, one tenant per repo); run `knos bench` to measure your machine.

| vector | knos | without knos | ratio / target |
|---|---|---|---|
| **Security**: rounds where a conflicting edit reached the file (3 agents x 200 rounds) | 0 | advisory (simulation) 191; none 200 | (advisory+1)/(knos+1) = 192.0x |
| **Capability**: questions answered from past sessions and commits, cited (20) | 18 | 0 (CLAUDE.md only) | (knos+1)/(base+1) = 19.0x |
| **Friction**: steps to three hosts sharing memory and claims | 1 | 8 (Sibyl Memory + MCP Agent Mail, from their READMEs) | 8.0x |
| **Budget**: spend past the cap (200 attempted payments, cap 5) | 0 | | 0 by construction |
| **Speed**: guard decision p50 / p95 (5,400 files, 21 live claims) | 20.5 / 38.8 ms | | p95 <= 100 ms |
| **Speed**: search p50 / p95 (5,400 files) | 17.2 / 25.9 ms | | p95 <= 300 ms |
| **UX**: `knos init` wiring 4 hosts, self-test included | 21.0 s | | <= 30 s |

## Friction, counted from each README

| tool | steps | what | source | read |
|---|---|---|---|---|
| knos | 1 | `knos init` (memory + claims + edit guard, every host it finds) | this repo | measured |
| MCP Agent Mail | 5 | installer, start the server (`am`), register_agent in each of 3 hosts; claims only (advisory leases), no memory of past sessions | https://github.com/Dicklesworthstone/mcp_agent_mail | 2026-09-29 |
| Sibyl Memory | 3 | `pip install sibyl-memory-cli[mcp]`, `sibyl init` (browser sign-in), `sibyl setup`; memory only, no claims | https://docs.sibyllabs.org/memory/install | 2026-09-29 |
| Sibyl Memory + MCP Agent Mail | 8 | both of the above, to get memory and claims | as above | 2026-09-29 |

Limits, said plainly: the advisory arm simulates a check-then-write lease; the history set is 20 synthetic decisions asked in other words, not a public benchmark; the budget row measures Knos's cap, and the agent wallet's balance is a second, on-chain ceiling this bench does not spend real money to show.
