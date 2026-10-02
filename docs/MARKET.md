# Market and money

Three parts: what Knos charges and what a payment costs to run (measured); the market from the bottom up, with every
input labelled **[SOURCED]**, **[MEASURED]** or **[ASSUMPTION]**; and how it reaches people without anyone selling
it. Nothing here is a forecast. Change an assumption and the arithmetic changes with it.

## 1. What it charges, and what it costs

| | price | where it is fixed |
|---|---|---|
| Verifying a GitHub or GitLab token on chain (`knos-oidc`) | free, for any program, forever | the program takes no fee and cannot be changed |
| A bounty that is paid (`knos-pay`) | 2.5% of the payment, at least 0.05 USDC, taken only when someone is paid | `FEE_BPS = 250`, `FEE_MIN = 50_000` in an immutable program: Knos cannot raise it |
| A bounty that is refunded, vetoed or never proven | nothing | |
| The check on a pull request, the Stop hook, the Agent PR Index | free | MIT |

**What a payment costs to relay** ([BENCH.md](BENCH.md), measured): 105,000 lamports of transaction fees for a whole
bounty (fund, pay, claim: 21 transactions), and about 3,150,000 lamports once for each new person paid (their public
record and their token account). Rent on the job itself comes back.

So the unit economics of the fee, honestly:

- A 20 USDC bounty earns 0.50 USDC. Relaying it costs 0.000105 SOL in fees.
- The first payout to a new person costs the relayer about 0.00315 SOL more. At a small bounty that is more than
  the fee; it is an acquisition cost paid once per person, in the open. Knos runs the public relayer today and
  absorbs it on devnet. The plan for mainnet is that a claimer brings their own token account (they then pay that
  rent, about 0.002 SOL, once), which leaves about 0.0011 SOL per new person.
- Nobody has to use Knos's relayer. `knos relay` is the same code; a repository or a payee can relay their own
  tokens and pay their own gas.
- The fee is not bundled with anything. Earlier versions bundled a memory subscription with payments; that is gone.

**A second line, planned and not built**: the check that reads a pull request's claims is free for public
repositories and will stay free. For private repositories it would be a per-repository plan. The comparable is code
review bots: CodeRabbit charges $12 to $48 a seat a month and reached $50M ARR in July 2026 with 8,000+ paying
customers ([Sacra](https://sacra.com/c/coderabbit/)), which is evidence that teams pay monthly for a machine opinion
on pull requests. Knos's check is not an opinion; it is a comparison with GitHub's own record.

## 2. The market, bottom up

### What is measured

| # | input | value | label |
|---|---|---|---|
| A | Agent-marked pull requests on GitHub a week | about 1.8M (Claude Code about 1.3M of them) | **[SOURCED]** [amplifying.ai](https://amplifying.ai/coding-agents/trends), read 1 Oct 2026. A third-party tracker; attribution-based counts are estimates |
| B | Of agent pull requests that say tests pass, the share with a failing check | 27.2% (660 of 2,431) | **[MEASURED]** [BENCH.md](BENCH.md) |
| C | Revenue run-rate of coding agents, sold by usage | about $5B: Claude Code over $2.5B, Cursor $2B, Cognition $492M | **[SOURCED]** [Anthropic, Feb 2026](https://www.anthropic.com/news/anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation); Bloomberg via [Trending Topics](https://trendingtopics.eu/cursor-breaks-2-billion-in-annual-revenue/); [ai2.work](https://ai2.work/blog/cognition-hits-26b-as-coding-agents-get-priced-like-infrastructure) (secondary) |
| D | Agent payments already on chain, with no proof of delivery | 75.41M transactions, $24.24M in 30 days; 76% on Solana | **[SOURCED]** [Solana Compass, 22 Sep 2026](https://solanacompass.com/news/solana-processes-76-of-all-x402-ai-agent-transactions-232-million-in-four-weeks) |
| E | Open-source bounties paid through the largest board | 1,470 in 2025; 175 in 2026 to 10 Aug | **[SOURCED]** [incubagent](https://incubagent.com/research/agent-bounty-market/); one venue |

### Three markets, smallest first

**(1) Bounties on GitHub issues: where Knos starts.** It is small today, and E shows it shrinking, for the reason
Knos removes: funders cannot tell what to pay for. The arithmetic, with Knos's fee:

| scenario | agent PRs a year (A × 52) | share tied to a bounty **[ASSUMPTION]** | average bounty **[ASSUMPTION]** | paid a year | fee pool at 2.5% |
|---|---|---|---|---|---|
| low | 93.6M | 0.1% | $50 | $4.68M | $117,000 |
| mid | 93.6M | 0.5% | $100 | $46.8M | $1.17M |
| high | 93.6M | 2% | $200 | $374.4M | $9.36M |

This is a wedge and not a business by itself, and we say so. No public source counts the share; it is the number
that matters most and it is an assumption.

**(2) Agent work sold by outcome instead of by usage.** C is about $5B a year paid for attempts. A buyer who pays per
merged pull request instead needs exactly what Knos is: a third party's signature that the work landed, and money
that moves on it. If a share s of that spend moves to pay-on-proof contracts, the fee pool is $5B × s × 2.5%:

| s **[ASSUMPTION]** | settled on proof a year | fee pool at 2.5% |
|---|---|---|
| 1% | $50M | $1.25M |
| 10% | $500M | $12.5M |
| 30% | $1.5B | $37.5M |

Nothing here assumes C keeps growing.

**(3) Any payment that should depend on a signed fact.** `knos-oidc` is not about bounties. It lets any Solana
program require a statement signed by GitHub or GitLab, and the same design takes any OIDC issuer. Uses that need
nothing new from Knos: grants and retroactive funding paid to GitHub accounts with no wallet collection; a program
upgrade that executes only for the commit CI built ([examples/oidc_gate](../examples/oidc_gate) is that gate);
agent-to-agent payments (D) that settle on a delivery receipt instead of on request. Forecasts for agent commerce as
a whole run from $144B to several trillion dollars by 2030 (McKinsey $3–5T; Morgan Stanley $190–385B for the US;
via a [Stellagent roundup](https://stellagent.ai/insights/agentic-commerce-market-size-forecast-2030), not checked
against the originals). We do not size Knos against those numbers. We note that every one of those payments has the
problem in section 3 of [WHY.md](WHY.md): the rail moves money and says nothing about delivery.

### What would change these numbers

- The share of agent pull requests tied to paid work. It can be measured by sampling, and the Agent PR Index is the
  place to do it.
- Whether agent vendors or their customers adopt outcome pricing. Knos does not need the vendors: a customer can
  fund an issue and let any agent take it.
- GitHub. It is the signer, and it could build this itself. `knos-oidc` already verifies GitLab tokens (RSA-4096)
  and is issuer-agnostic by design; the escrow reads GitHub's claims today.

## 3. How it reaches people

Nobody sells Knos. Each of these is in the product:

1. **The free check.** Paste any agent pull request; in about a second the page says whether its "tests pass" is
   true, with a "Protect this repo" button beside the verdict. No install, no login.
2. **The Agent PR Index.** A public, checkable record of how often each agent's claims are false, rebuilt every 6
   hours. It is the dataset nobody else publishes, and it is why a maintainer would look for a tool like this.
3. **It lives where the work already is.** The funder types a comment on a GitHub issue. The worker opens a pull
   request. The result is a comment on that pull request with the transaction. Every paid pull request is a public
   page on GitHub that shows the product working.
4. **Money waiting for you.** A bounty is paid to a GitHub account before its owner has ever heard of Knos or owns a
   wallet. "19.50 USDC is waiting for you" is the invitation, and claiming it needs only a repository of their own.
   This is how PayPal spread through eBay ([WHY.md](WHY.md), section 7).
5. **A primitive others build on.** Any Solana program can use `knos-oidc` for free, with nothing to ask of Knos.

Outside use is counted from the escrow's own logs and published on the site's Numbers page, apart from Knos's own
activity and from funders who paid themselves ([scripts/network_stats.py](../scripts/network_stats.py)). That page
is the traction claim, whatever it says on the day you read it.
