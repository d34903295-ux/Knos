# Market and money

Four parts: what Knos charges and what a payment costs to run (measured); the market, with every input labelled
**[SOURCED]**, **[MEASURED]** or **[ASSUMPTION]**; what would have to be true for this to be a business; and how it
reaches people without anyone selling it. Nothing here is a forecast. Figures marked "secondary" were read in
coverage of the original, not in the original.

## 1. What it charges, and what it costs

| | price | where it is fixed |
|---|---|---|
| The check on a pull request (`check.yml`, or the paste box on the site), the Stop hook, `knos mcp`, the Agent PR Index | free | MIT |
| Verifying a GitHub or GitLab token on chain (`knos-oidc`) | free, for any program | the program takes no fee and cannot be changed |
| A bounty that is paid (`knos-pay`) | 2.5% of the payment, at least 0.05 USDC, taken only when someone is paid | `FEE_BPS = 250`, `FEE_MIN = 50_000` in an immutable program: Knos cannot raise it |
| A bounty that is refunded, vetoed or never proven | nothing | |

**What a payment costs to relay** ([BENCH.md](BENCH.md), measured): 105,000 lamports of transaction fees for a whole
bounty (fund, pay, claim: 21 transactions), and about 3,150,000 lamports once for each new person paid (their public
record and their token account). Rent on the job itself comes back.

So the unit economics of the fee:

- A 20 USDC bounty earns 0.50 USDC. Relaying it costs 0.000105 SOL in fees.
- The first payout to a new person costs the relayer about 0.00315 SOL more. At a small bounty that is more than
  the fee; it is an acquisition cost paid once per person, in the open. Knos runs the public relayer today and
  absorbs it on devnet. On mainnet a claimer brings their own token account (they then pay that rent, about 0.002
  SOL, once), which leaves about 0.0011 SOL per new person.
- Nobody has to use Knos's relayer. `knos relay` is the same code; a repository or a payee can relay their own
  tokens and pay their own gas.
- At a 2.5% fee, 1M USDC of fees a year takes 40M USDC of payments a year.

## 2. The market

### What exists today is small, and we say so

| # | what | size | label |
|---|---|---|---|
| A | Open bounties on GitHub issues, all boards together | 59 verified bounties, $64,291 on 2 Oct 2026 | **[SOURCED]** [bountyos.rovidev.com](https://bountyos.rovidev.com/en/github-bounty-board/), read 2 Oct 2026 |
| B | Payouts on the largest board | 1,470 in 2025; 175 in 2026 to 10 Aug | **[SOURCED]** [incubagent](https://incubagent.com/research/agent-bounty-market/); one venue |
| C | Superteam Earn, the largest crypto bounty and grant board | $15.9M paid in all, 3,219 listings; mostly content and design, judged by the sponsor, none tied to a pull request | **[SOURCED]** [superteam.fun stats](https://superteam.fun/api/homepage/stats), read 2 Oct 2026 |

Bounties on issues are where Knos starts because they need no sales and no contract: a comment funds one. They are
not the business. At a 2.5% fee, all of A is about $1,600. We do not size Knos against this market.

### Where the money is: agents are paid for attempts

| # | what | size | label |
|---|---|---|---|
| D | Revenue run-rate of coding agents, all billed by seat, token or compute | about $7B: Cursor about $4B, Claude Code over $2.5B, Cognition $492M | **[SOURCED]** [Dealroom, 9 Jun 2026](https://dealroom.co/news/134107-cursor-tops-4b-annualized-revenue/); [Anthropic, Feb 2026](https://www.anthropic.com/news/anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation); [TechCrunch, 27 May 2026](https://techcrunch.com/2026/05/27/ai-coding-startup-cognition-raises-1b-at-25b-pre-money-valuation/) |
| E | Agents already sold per outcome, where the vendor defines the outcome | Intercom Fin near $100M a year at $0.99 per resolution; Sierra $200M | **[SOURCED]**, secondary: [EnterpriseDNA, 2 Aug 2026](https://enterprisedna.co/resources/ai-pulse/ai-pulse-2026-08-02-intercom-s-fin-ai-agent-is-nearing-100m-arr-roughly-half-of/); [Value Add VC](https://valueaddvc.com/blog/how-does-sierra-ai-make-money-outcome-based-pricing-enterprise-agents-and-the-business-model-breakdown) |
| F | Share of AI companies pricing by outcome | 18% to 23% in six months | **[SOURCED]** [ICONIQ, State of AI 2026](https://www.iconiq.com/growth/reports/state-of-ai-2026) |
| G | Agent pull requests a week | about 1.8M | **[SOURCED]** [amplifying.ai](https://amplifying.ai/coding-agents/trends), an estimate from declared attribution |
| H | Of agent pull requests that say tests pass, the share with a failing check | 17.8% of repositories' first such pull request (147 of 826) | **[MEASURED]** [BENCH.md](BENCH.md) |

E and F say buyers will pay per outcome when they can. D says that for code they cannot yet: we found no coding-agent
vendor that prices per merged pull request. The reason is the meter. In support, the vendor's own system decides
what "resolved" means and the buyer trusts it or leaves. For code, an agent vendor grading its own work has the
problem in H.

Knos is a meter nobody owns: GitHub signs what happened, a program nobody can change checks the signature and moves
the money. A buyer who wants to pay an agent, a vendor or a contractor per merged pull request can do it today with
a comment; the seller cannot fake the outcome and the buyer cannot withhold the money once it is proven.

If a share s of D is settled on proof instead of on usage, the fee pool is D × s × 2.5%:

| s **[ASSUMPTION]** | settled on proof a year | fee pool at 2.5% |
|---|---|---|
| 0.5% | $35M | $0.9M |
| 5% | $350M | $8.75M |
| 20% | $1.4B | $35M |

No public source gives s; today it is zero. It is the number that decides whether this is a business, and the next
section says what would move it.

### A second use of the same primitive

`knos-oidc` is not about bounties. It lets any Solana program require a statement GitHub or GitLab signed. One use
needs nothing new from Knos and answers losses that have already happened: a program upgrade that executes only for
the commit CI built. Bybit lost about $1.5B in February 2025 through one compromised developer machine
([BleepingComputer](https://www.bleepingcomputer.com/news/security/lazarus-hacked-bybit-via-breached-safe-wallet-developer-machine/)),
and Drift $285M in April 2026 after a contributor was compromised
([Halborn](https://www.halborn.com/blog/post/explained-the-drift-hack-april-2026)).
[examples/oidc_gate](../examples/oidc_gate) is that gate as a working program. We do not count this market; it is
why the verifier is separate, free and usable without the escrow.

## 3. What would have to be true

Stated so that each can be checked, and none of them is true yet:

1. **A buyer pays per merged pull request instead of per seat.** The first evidence would be one team or one agent
   vendor that funds a repository's issues through Knos for a month. This needs real money, which needs the mainnet
   version ([SECURITY.md](SECURITY.md)), which needs an audit.
2. **Maintainers install the check because it is useful without money.** The check alone (`check.yml`) costs
   nothing and touches no chain. The Numbers page counts repositories that fund through Knos; outside use there is
   zero as of 2 Oct 2026.
3. **Agents find the work.** `knos mcp` lists funded issues to any agent that has it; the MCP registry lists the
   server.
4. **The fee survives a fork.** The code is MIT and a fork can set the fee to zero (MergePay charges none). What a
   fork cannot copy is the record: every payment is public under a GitHub account's id (`knos due <login>`), and
   repositories, agents and their operators accumulate a history on one deployment. That is a thin advantage today,
   and it is the honest answer.

## 4. How it reaches people

Nobody sells Knos. Each of these is in the product:

1. **The free check.** Paste any agent pull request on the site, or commit one workflow file: every pull request's
   "tests pass" is then compared with GitHub's own record. No money, no wallet, no chain. This is the part a
   maintainer drowning in agent pull requests can use today (GitHub itself shipped pull-request limits for this in
   [February](https://github.blog/changelog/2026-02-13-new-repository-settings-for-configuring-pull-request-access/)
   and [June 2026](https://github.blog/changelog/2026-06-17-limit-open-pull-requests-for-users-without-write-access/)).
2. **The Agent PR Index.** A public, checkable record of how often each agent's claims are false, rebuilt every 6
   hours.
3. **It lives where the work already is.** The funder comments on a GitHub issue. The worker opens a pull request.
   The result is a comment on that pull request with the transaction.
4. **Agents are users too.** `knos init` gives a coding agent an MCP server that lists paid work it can take. The
   agent needs no wallet; its operator is paid to their GitHub account and claims with one command.
5. **Money waiting for you.** A bounty is paid to a GitHub account before its owner has ever heard of Knos or owns a
   wallet. This is how PayPal spread through eBay ([WHY.md](WHY.md), section 7).
6. **A primitive others build on.** A dependency-free crate, IDLs and a JavaScript client, all installable without
   an account anywhere ([OIDC.md](OIDC.md)).

Outside use is counted from the escrow's own logs and published on the site's Numbers page, apart from Knos's own
activity and from funders who paid themselves ([scripts/network_stats.py](../scripts/network_stats.py)). That page
is the traction claim, whatever it says on the day you read it.
