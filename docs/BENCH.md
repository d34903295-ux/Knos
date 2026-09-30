# Knos bench

Measured by `knos bench` on 2026-09-29 20:31 UTC: Linux x86_64, Python 3.12.13, knos 0.2.0 (the 0.2.0 working tree on top of commit 51d7cac, not yet committed).
Re-run it to check every number. The method for each is in `src/knos/bench.py`.

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
