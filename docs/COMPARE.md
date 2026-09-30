# Knos compared

Every price and claim about another product below was read from the linked page on the date shown. If one has changed,
the link is the authority, not this table. Knos's own numbers come from `knos bench` ([BENCH.md](BENCH.md)); re-run it
to check them.

## Where Knos has to beat the alternatives

| alternative | what it is | where Knos differs | source (read) |
|---|---|---|---|
| MCP Agent Mail (free) | messaging between agents; *advisory* file reservations; an optional pre-commit guard that blocks commits touching files others reserved; an HTTP server that must be running for cross-machine use | Knos enforces at edit time, in each vendor's own hook, before the file is written; across machines with no server (the claims live on Solana); commit guard as well, for raw shell writes | [README](https://github.com/Dicklesworthstone/mcp_agent_mail) (30 Sep 2026) |
| Claude Code Projects / Cursor Projects | coordination bundled into one vendor's product, in its cloud | Knos works across vendors (Claude Code, Codex, Cursor, OpenCode, Copilot), on laptops and in cloud sandboxes | [Claude](https://claude.com/blog/projects-redesigned), [Cursor](https://cursor.com/changelog/projects) (30 Sep 2026) |
| Wasteland (free) | federated claims and reputation "stamps"; advisory, no chain | Knos's claims are enforced, and its records are verifiable by anyone on chain | [docs](https://gastown.dev/docs/WASTELAND) (30 Sep 2026) |
| Coinbase Agentic Wallet | MPC wallet for agents with session caps and per-transaction limits enforced by Coinbase's signing service; x402 on Base | Knos's agent limits are enforced by the chain's own rules (Tempo Keychain limits per period; an SPL delegate on Solana), not by a hosted signer | [announcement](https://www.paymentsjournal.com/coinbase-unveils-agentic-wallets-to-power-autonomous-ai-spending-and-investing) (30 Sep 2026) |
| LiteLLM / Claude apps gateway | spend caps enforced by routing model calls through a proxy | one meter across vendors that includes subscription use (read from the agents' own logs), plus on-chain payment budgets; no proxy | [LiteLLM budgets](https://docs.litellm.ai/docs/proxy/users) (30 Sep 2026) |
| Conductor Teams ($60 / user / month) | **a different product**: isolated cloud workspaces for coding agents | listed for price context only | [pricing](https://www.conductor.build/pricing) (30 Sep 2026) |
| Sibyl Memory Pro ($12 / month or $108 / year) | local agent memory without the 5 MB cap; self-learning and the memory linter | Knos is built on Sibyl, and sells you Sibyl Pro in the same `knos pro buy` command through Sibyl's own checkout, never charging twice; Knos adds everything above | [tiers](https://docs.sibyllabs.org/memory/tiers), [pro](https://sibyllabs.org/pro) (30 Sep 2026) |
| buying Knos and Sibyl separately | two checkouts, two accounts, two renewals | one command, two payments through Sibyl's own checkout (Path B, live). A one-transaction split (Path A) is built and tested on testnets but stays off on mainnet until Sibyl confirms | this repo |

## Running cost for three machines to share enforced claims

| setup | monthly cost |
|---|---|
| Knos Free | $0 for servers. Chain fees: 5,000 lamports per claim placed, plus a refundable deposit of about 0.0028 SOL per live claim (measured, [BENCH.md](BENCH.md)) |
| MCP Agent Mail across machines | its server must be always on and reachable. The smallest always-on VM on Fly.io is $1.94 / month ([pricing](https://fly.io/docs/about/pricing/), 30 Sep 2026), before storage for its SQLite files and any tunnel |

Team-tier prices are compared separately in [PRICING.md](../PRICING.md).

## Setup steps

From nothing to three hosts sharing memory and claims, counted from each README. One step is one command to run or
one config block to paste per host.

| tool | steps |
|---|---|
| Knos, one machine | 1: `knos init` |
| Knos, a second machine joining a team | 2: `knos init` (prints a join code), then the owner runs `knos team add <code>`; plus funding the owner key once from a faucet on devnet |
| MCP Agent Mail | 5: installer, start the server, register in each of 3 hosts (claims only) |
| Sibyl Memory | 3: install, `sibyl init` (browser sign-in), `sibyl setup` (memory only) |

## Market context (sources for the pitch and docs/WHY.md)

- Anthropic's annualized revenue run rate reached $65B by the end of July 2026 (reported by CNBC; see
  [Bloomberg Government](https://news.bgov.com/financial-accounting/anthropic-revenue-run-rate-surpasses-65-billion-ahead-of-ipo)).
- Cursor passed $4B annualized revenue ([Dealroom](https://dealroom.co/news/134107-cursor-tops-4b-annualized-revenue/)),
  and SpaceX agreed to buy it for about $60B in stock on 16 Jun 2026
  ([iTWire](https://itwire.com/it-industry-news/deals/spacex-to-buy-cursor-for-us-60-billion)).
- JetBrains, Aug 2026: at work, 39% of professional developers use Claude Code, 21% Copilot, 16% Codex and 12% Cursor
  ([JetBrains Research](https://blog.jetbrains.com/research/2026/08/ai-coding-agent-adoption-2026/)).
- Stack Overflow, 27 May 2026: 17% use multiple specialized agents and 16% multiple coordinated agents; 68% prefer
  single-agent setups ([Stack Overflow](https://stackoverflow.blog/2026/05/27/agents-on-a-leash-agentic-ai-remains-mostly-monitored-at-work/)).
- Gartner, 24 Jun 2026: AI coding costs will pass the average developer's salary by 2028
  ([Gartner](https://gartner.com/en/newsroom/press-releases/2026-06-24-gartner-predicts-ai-coding-costs-will-surpass-average-developer-salary-by-2028-as-token-consumption-surges)).
