# Knos compared

Every price and claim below was read from the linked page on **29 Sep 2026**. If one has changed, the link is the
authority, not this table. Knos's own numbers come from `knos bench` ([BENCH.md](BENCH.md)); re-run it to check them.

## What people pay for today

| product | price (29 Sep 2026) | what it does | where Knos differs | source |
|---|---|---|---|---|
| Sibyl Memory Pro | $12 / month or $108 / year (card, or USDC on Base) | local agent memory, cap removed | Knos is built on Sibyl: every answer comes out of a Sibyl store. Knos adds file claims, the edit guard and spend caps on top. Free Sibyl's 5 MB cap still applies; `sibyl upgrade` removes it | [tiers](https://docs.sibyllabs.org/memory/tiers) |
| Portkey Production | $49 / month | AI gateway: logs and routing through its proxy | Knos needs no proxy: it reads the agents' own logs (Claude Code, Codex), so it sees interactive and subscription use too, and caps every agent together, for 10 USDC | [pricing](https://portkey.ai/pricing) |
| Helicone Pro | $79 / month | LLM observability and cost tracking through its proxy | the same, and Knos's cap refuses the next edit when it is reached rather than only reporting | [pricing](https://www.helicone.ai/pricing) |
| MCP Agent Mail | free | messaging between agents, with *advisory* file reservations and an optional pre-commit guard | Knos's claims are enforced at edit time by each host's own hook, before the file is written, with nothing to run | [README](https://github.com/Dicklesworthstone/mcp_agent_mail) |
| ccusage | free | "Analyze coding (agent) CLI token usage and costs from local data" (its README); report only | Knos reports and also enforces a cap | [README](https://github.com/ryoppippi/ccusage) |
| `claude --max-budget-usd` | free | caps one headless run | Knos's cap spans every run, session and host | Claude Code CLI help |

## Prior art, and what is new here

| idea | earlier work | what Knos does differently |
|---|---|---|
| shared memory for coding agents | Sibyl Memory, CLAUDE.md / AGENTS.md files | reads past sessions of *every* host (Claude Code, Codex, Cursor) and commits into one Sibyl store, and cites the session or commit each answer came from |
| coordination between agents | MCP Agent Mail's advisory leases | claims are path globs taken in one SQLite transaction (exactly one winner), then enforced **at edit time** in each host's pre-tool hook, across vendors |
| cost tracking | ccusage, Portkey, Helicone | no proxy; one cap across tokens and agents' API payments, enforced at the next edit |
| agents paying for APIs | MPP (Tempo, Stripe), x402 (Solana) | each agent pays from its **own** wallet, which holds only what you funded it with: the chain enforces the ceiling even if Knos is bypassed |

## Setup steps (the friction vector)

From nothing to three hosts sharing memory and claims, counted from each README on 29 Sep 2026. One step is one
command to run or one config block to paste per host.

| tool | steps |
|---|---|
| Knos | 1: `knos init` |
| MCP Agent Mail | 5: installer, start the server, register in each of 3 hosts (claims only) |
| Sibyl Memory | 3: install, `sibyl init` (browser sign-in), `sibyl setup` (memory only) |
| both of the last two | 8 |
