# Knos

**Every vendor now coordinates its own agents. Nobody coordinates everyone's. Knos is the neutral coordination and
memory layer for the agent economy: who works on what, what is known, and what each may spend. It is enforced where
the action happens, arbitrated on Solana, budgeted on Tempo and Solana, and remembered by Sibyl.**

<!-- mcp-name: io.github.drexthealpha/knos -->

```
$ knos team create --cluster devnet
  ✓ team registry on Solana — no server, nothing to host (credential knos-ca29f8d10cccc0b2)
  ✓ wrote .knos/team.json — commit it

# a teammate, on another machine, with another vendor's agent:
$ uvx knos init
  ✓ found .knos/team.json — send this join code to the team owner: knos-join:AGge…:Ym9i (fingerprint: focus-garlic-nutmeg-frost-blade-dance)

# the owner:
$ knos team add knos-join:AGge…:Ym9i
  ✓ verified fingerprint · added · funded key · sealed team secret on chain  (bob)
```

Now Alice's Claude Code claims `src/billing/tax.py` just by editing it. Bob's Codex, on another machine, tries to
patch that file and is refused:

```
src/billing/tax.py is claimed by alice/claude-code since 16:11 (Knos). Ask them, or take other work.
```

No server exists anywhere. This exact run is on Solana devnet, with every transaction linked, in
[docs/network/demo.md](docs/network/demo.md).

## What it enforces, and where

| | what | enforced by |
|---|---|---|
| **claims** | who works on what: files, folders, or any unit (`task:`, `market:`, `wallet:`) | each host's own pre-edit hook (Claude Code, Codex, Cursor, OpenCode, the Copilot cloud agent) and a git commit guard for raw shell writes; across machines by a Solana Attestation Service mutex, with no server |
| **memory** | what is known: past sessions of every host, commits, decisions, team playbooks | [Sibyl](https://sibyllabs.org), and only Sibyl |
| **budgets** | what each agent may spend | the chain itself: a Tempo Keychain access key limited per day, or a Solana delegate. Signing directly with the agent's key cannot get past the limit, even if Knos is bypassed |
| **records** | who did what | one signed attestation per agent per day: counters and a Merkle root anyone can check |

Solo use needs none of the chain: memory, claims and the guard work fully offline, as in 0.2.

## Install

Python 3.10+ on Windows, macOS or Linux:

```
pipx install knos        # or: uv tool install knos; or run it once with uvx knos init
knos init                # wire every agent it finds; undo with: knos init --undo
knos init --team         # also commit the guard with the repo, for every clone and cloud session
```

## Use it

| you type | what happens |
|---|---|
| `knos ask "why did we drop redis?"` | answers from past sessions (Claude Code, Codex, Cursor), commits, CLAUDE.md/AGENTS.md and the code, each with its source |
| `knos claim "the parser" -p "src/parser/**"` | other agents' edits to those files are refused, on this machine and, in a team, on every machine |
| `knos team create` / `add` / `status` | a team registry on Solana; see [CLOUD.md](docs/CLOUD.md) for cloud sandboxes |
| `knos budget set claude 5/day --chain tempo` | Pro: Tempo enforces that agent's daily limit; `--chain solana` sets a delegate |
| `knos agent record codex` | what that agent claimed, finished, abandoned and collided on, checked against its records on chain |
| `knos learn` / `knos lint` | Sibyl Pro: team playbooks from Sibyl's self-learning; Sibyl's linter plus a check for agents that recorded opposite things |
| `knos doctor` | which agents and machines are guarded, and which are not |
| `knos pro buy` | Knos Pro, and Sibyl Pro in the same command through Sibyl's own checkout if you lack it (never charged twice) |

Your agents get the same through the MCP server `knos init` installs, and framework agents through the Python SDK:

```python
from knos.sdk import Knos
k = Knos(agent="researcher")
if k.claim("task:invoice-4411"):
    k.remember("invoice 4411 was a duplicate", about="invoice-4411")
```

[`examples/langgraph_team.py`](examples/langgraph_team.py): two LangGraph agents share Sibyl memory through Sibyl's
own `BaseStore`, and never work the same task.

## Numbers

From [`knos bench`](docs/BENCH.md), which anyone can re-run. Chain rows run on a local validator with the
devnet-deployed programs.

| | knos | without |
|---|---|---|
| conflicting writes to working trees, 3 machines × 3 vendors' hooks × 200 rounds | **0** | 149 with Agent Mail-style advisory reservations (modelled, 90% compliance); 1,062 with none |
| claim protocol property test: double winners / winners ever blocked | **0 / 0** (1,000 rounds, 5,000 claims) | |
| overspend signed directly with an agent's key, Tempo Moderato (200) | **0 received; 200 reverted** | |
| overspend signed directly with an agent's delegate key, Solana (200) | **0 moved; 200 rejected** | |
| edit-guard decision when the agent holds the claim, p95 | **25.6 ms** | |
| edits that waited on the chain (20 per claimed file) | **5%** | |
| coverage cells: 4 vendors × 1 or many machines × edit or commit × bypass-proof budget | **32 / 32** | 8 for MCP Agent Mail |

Verify the core claims yourself:
- `pytest tests/test_collide.py`: on one machine, exactly one of many agents gets a file.
- `pytest tests/test_team_two_homes.py`: across two machines and four vendors' hooks, with no server (needs
  `bash scripts/devchain.sh start`).
- `pytest tests/test_team_property.py`: the claim protocol's property test (same).
- `pytest tests/test_chain_budgets.py`: an agent's own key cannot spend past its chain limit.
- `pytest tests/test_no_network.py`: a solo day of work opens no network connection.

**What it costs:** a 5,000-lamport fee to place a claim, plus a deposit of about 0.0028 SOL while the claim is live,
refunded when it is released. Each member key keeps a small float for those deposits: 20 live claims' worth plus fees,
about 0.065 SOL. On devnet all of it is free.

## Why a chain

For one machine it isn't needed, and Knos doesn't use one. For a team, the chain replaces the server someone would
otherwise have to run and every vendor would have to trust. See [WHY-CHAIN.md](docs/WHY-CHAIN.md) for what it does and
does not protect, and [SECURITY.md](docs/SECURITY.md) for the keys, the member-trust model and the known bypasses.

## Privacy

Nothing in plaintext on chain: paths, repo names, user names and descriptions are salted hashes or sealed boxes.

Without a team, nothing leaves your machine. In a team, Knos talks to one Solana RPC endpoint. Beyond that, Knos Pro
makes the calls you ask for: public RPC reads to check a payment, payments you or your agents' keys sign, and Sibyl's
own tier check.

Secrets (`.env`, keys, certificates, `.ssh`, `.aws`, and paths you add with `knos private`) never reach your agents.

## Plans

- **Free (MIT):** solo; team registries up to 3 keys; any public open-source repo.
- **Pro:** 10 USDC / 30 days (100 / year), plus Sibyl Pro at Sibyl's price if you lack it.
- **Team:** 20 USDC per seat per 30 days, for private registries of 4+ keys.

See [PRICING.md](PRICING.md).

## More

- [WHY.md](docs/WHY.md): the market, with sources.
- [COMPARE.md](docs/COMPARE.md): MCP Agent Mail, vendor projects, Wasteland, Coinbase Agentic Wallet, gateways.
- [INTEGRATE.md](docs/INTEGRATE.md): MCP, hooks, the SAS schemas, the SDK.
- [The network page](https://drexthealpha.github.io/Knos/network/): every Knos team on Solana, counted from the
  chain.

## History

- **0.1.x:** released 1–7 Sep 2026.
- **0.2.x:** 30 Sep 2026.
- **0.3.0:** Oct 2026.

See [CHANGELOG.md](CHANGELOG.md). Knos is built by drexthealpha. Its memory engine is [Sibyl](https://sibyllabs.org).
