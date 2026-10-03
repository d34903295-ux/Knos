# Market and money

Seven parts: what Knos charges and what a payment costs to relay; a bottom-up market; what a seller keeps; who buys
first; the test that decides whether this is a payment business or a free check; what a zero-fee fork changes; and
what is not known. Every input is labelled **[sourced]** (with its link; read on 2 Oct 2026 unless it says
otherwise), **[measured]** (with where) or **[assumption]**. The rest is arithmetic on those inputs. Nothing here
is a forecast.

## 1. What Knos charges, and what a payment costs to relay

| | price | where it is fixed |
|---|---|---|
| The check on a pull request, `knos mcp`, the local hook, the Agent PR Index | free | MIT |
| Verifying a GitHub or GitLab token on chain (`knos-oidc`) | free, for any program | the program takes no fee |
| A payment (`knos-pay`) | 2.5% of the amount, at least 0.05 USDC, taken only when someone is paid | `FEE_BPS = 250` and `FEE_MIN = 50_000` in the program |
| A refund, a held payment that returns to the funder, a withdrawal from a balance | nothing | |

A job is between 1 and 500 USDC until an outside review (`MIN_AMOUNT`, `MAX_AMOUNT`). The program is upgradeable
only through a multisig with a public 48-hour delay, until an outside review, and is then made immutable. So a
change to the fee would be public 48 hours before it took effect.

**What a payment costs to relay.** The transaction counts were measured on the first deployment
([BENCH.md](BENCH.md)); the second deployment writes and verifies a token the same way.

- A Solana transaction costs 5,000 lamports for its signature **[sourced]**
  ([Solana docs](https://solana.com/docs/core/fees)).
- Carrying one GitHub-signed token takes 7 transactions, 35,000 lamports **[measured]**: 3 to write the token, 2 to
  check the signature, 1 for the escrow's instruction and 1 to close the token's account.
- A whole bounty on the first deployment was three tokens (fund, pay, claim): 21 transactions, 105,000 lamports
  **[measured]**. The second deployment pays straight to a wallet, so a bounty is two tokens, and a third the first
  time a person binds a wallet. We keep 105,000 lamports as the cost of a bounty below.
- Rent. Solana holds (128 + bytes) × 5,080 lamports against each account since September 2026 **[sourced]**
  ([SIMD-0437](https://solana.com/upgrades/reduced-rent); mainnet and devnet RPC both returned 1,488,440 lamports
  for 165 bytes on 2 Oct 2026). BENCH.md's rent figures come from the local test chain, which still charges the old
  6,960 per byte; today's are 27% lower. A job's own rent comes back when the job is paid or refunded.
- One-time costs for a new payee, which the relay pays: their token account, 1,488,440 lamports; their public
  record (64 bytes), 975,360; and a marker the first time a given funder pays them (1 byte), 655,320. Together
  3,119,120 lamports.

**Contribution margin**, at 120 USD per SOL **[sourced]** (SOL traded between 117 and 124 USD on 2 Oct 2026;
[CoinGecko](https://www.coingecko.com/en/coins/solana)). The relay's fees for a bounty are then 0.0126 USD, a
token account 0.179 USD, and all three one-time accounts 0.374 USD.

| payment | fee at 2.5% | kept when the payee was paid before | kept on a first payment (new token account) | kept on a first payment (record and marker too) |
|---|---|---|---|---|
| 20 USDC | 0.50 | 0.487 (97.5%) | 0.309 (61.8%) | 0.113 (22.6%) |
| 100 USDC | 2.50 | 2.487 (99.5%) | 2.309 (92.4%) | 2.113 (84.5%) |
| 500 USDC | 12.50 | 12.487 (99.9%) | 12.309 (98.5%) | 12.113 (96.9%) |

A first payment to a new payee breaks even at 7.65 USDC with the token account alone, and at 15.48 USDC with all
three accounts. Below that the relay pays out more than the fee brings in. At the smallest job, 1 USDC, the fee is
the 0.05 minimum and a first payment costs the relay 0.14 to 0.34 USD more than it earns. That is a cost of
acquiring a payee, paid once per person.

What the table leaves out: priority fees, which a busy mainnet may need (not measured); the machine that runs the
relay; and GitHub Actions minutes, which are free on public repositories with standard runners and cost a private
repository's owner 0.006 USD a minute on a 2-core Linux runner, beyond the minutes its plan includes **[sourced]**
([billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[prices](https://docs.github.com/en/billing/reference/actions-runner-pricing)).

Nobody has to use Knos's relay. `knos relay` is the same code. Whoever relays pays these costs; the fee goes to
Knos either way.

Solana plans to cut rent further, to 696 lamports per byte (same SIMD page, expected November 2026). The one-time
costs would then fall by 86%.

At 2.5%, 1 million USD of fees a year takes 40 million USD of settled payments a year.

## 2. The market, bottom up

Reachable buyers × eligible tasks a year × spend per task × adoption = settled volume. The fee is 2.5% of that.
An eligible task is one on GitHub, with a fixed scope and checks, done by someone the buyer does not already pay.

| scenario | reachable buyers | tasks a year, each | spend per task | adoption | settled a year | fees a year |
|---|---|---|---|---|---|---|
| A. Open-source companies that post bounties today | 100 **[sourced]**: Algora's home page says "100+ happy customers". It now leads with hiring, so this is a ceiling for bounty posters. | 25 **[assumption]** | 150 USD **[assumption]** | 20% **[assumption]** | 75,000 USD | 1,875 USD |
| B. Crypto teams and foundations that pay strangers per outcome | 3,380 **[sourced]**: 2,730+ sponsors on Superteam Earn and 650+ protocols on Immunefi. Overlap unknown. That they hold USDC is an **[assumption]**. | 25 **[assumption]** | 200 USD **[assumption]** | 5% **[assumption]** | 845,000 USD | 21,125 USD |
| C. Engineering teams buying fixed-scope changes from outside | 78,500 **[assumption]**: one in ten of Upwork's 785,000 active clients **[sourced]** | 25 **[assumption]** | 100 USD **[assumption]** | 2% **[assumption]** | 3,925,000 USD | 98,125 USD |

Sources for the buyers: [Algora](https://algora.io); [Superteam Earn](https://superteam.fun/earn);
[Immunefi](https://immunefi.com);
[Upwork, results for 2025, 9 Feb 2026](https://www.globenewswire.com/news-release/2026/02/09/3234886/0/en/upwork-reports-fourth-quarter-and-full-year-2025-financial-results.html).

What anchors the assumptions:

- Spend per task. Of the 59 open bounties on all boards, 18 are under 50 USD, 14 are from 50 to 149, 10 are from
  150 to 499 and 17 are 500 or more **[sourced]** ([BountyOS](https://bountyos.rovidev.com/en/github-bounty-board/)).
  Knos caps a job at 500 USDC until an outside review.
- Tasks a year. No source. 25 is one every two weeks.
- Adoption. No source. Today it is zero.

What the scenarios say:

- Together they settle 4.8 million USD a year and earn 121,125 USD of fees. None reaches 1 million USD of fees. The
  40 million USD that takes is 8 times the three together.
- 40 million USD is 267,000 payments of 150 USD. It is 622 times the open bounties on every board today (64,291
  USD **[sourced]**, BountyOS). It is about half of what HackerOne's programmes paid in a year (81 million USD
  **[sourced]**,
  [BleepingComputer, 2 Oct 2025](https://www.bleepingcomputer.com/news/security/hackerone-paid-81-million-in-bug-bounties-over-the-past-year/)),
  and 1% of the work bought through Upwork in 2025 (4.03 billion USD **[sourced]**, same Upwork release).
- Bounties on issues are where Knos starts, not a business. 2.5% of every open bounty on every board today is
  1,607 USD.

Where the larger money is: coding agents are billed by seat or by usage. Enterprises spent 4.0 billion USD on
coding tools in 2025 **[sourced]** (Menlo Ventures, via
[SaaStr, 10 Dec 2025](https://www.saastr.com/55-of-all-departmental-ai-spend-is-now-on-coding-and-its-not-slowing-down)),
and Cursor alone passed 2 billion USD of annualised revenue **[sourced]**
([TechCrunch, 2 Mar 2026](https://www.techcrunch.com/2026/03/02/cursor-has-reportedly-surpassed-2b-in-annualized-revenue/),
citing Bloomberg). We do not multiply these by a share. No source says what part of that spend would move to
payment per merge, and the part settled through Knos today is zero.

## 3. What a seller keeps

The seller is a person running an agent, or working by hand, who is not on the buyer's payroll.

- After the fee a 20, 100 or 500 USDC task pays 19.50, 97.50 or 487.50.
- Model cost per attempt: 1, 3 and 15 USD for those three sizes **[assumption]**. The anchor is a published
  estimate of 0.04 to 4.50 USD for a simple task and 0.08 to 13.50 USD for a medium one, depending on the tool
  **[sourced]** ([KSPL Academy, 2 Jun 2026](https://academy.kspl.tech/blog/2026-06-02-ai-coding-agent-cost-ladder-2026);
  its own derived estimates, not measurements).
- Expected attempts per accepted result = 1 ÷ merge rate.
  - Published: 71.5% of 33,596 agent pull requests in repositories with more than 100 stars were merged, from 43%
    to 83% by agent **[sourced]** ([arXiv 2601.15195](https://arxiv.org/html/2601.15195)). That is every agent pull
    request, not strangers answering bounties, so it is the favourable case: 1.4 attempts.
  - For a stranger on a contested bounty there is no published rate. One hunter's 30-day log gives about 15% on
    Algora, about 24% overall and about 0% on repositories new to them **[sourced, one person's account]**
    ([dev.to, 1 Jun 2026](https://dev.to/zeroknowledge0x/the-open-source-money-map-every-way-developers-are-actually-making-money-in-2026-with-real-45ba)).
    At 15%: 6.7 attempts.

| task | paid after the fee | model cost per attempt | kept at 71.5% merged | kept at 15% merged |
|---|---|---|---|---|
| 20 USDC | 19.50 | 1 | 18.10 | 12.83 |
| 100 USDC | 97.50 | 3 | 93.30 | 77.50 |
| 500 USDC | 487.50 | 15 | 466.51 | 387.50 |

A person's time changes this. GitLab puts a manual review at 15 minutes of a senior engineer, about 25 USD
**[sourced]** ([GitLab, 19 Mar 2026](https://about.gitlab.com/blog/agentic-code-reviews-with-flat-rate-pricing)).
If the seller checks each attempt that carefully, a 20 USDC task loses money at any merge rate. Small tasks pay only
when the agent runs unattended.

The seller pays no rent: the relay creates their token account (section 1). The payment itself is in the
transaction that verifies the pay token. The wait is before that, for a maintainer. In the same study, 38% of the
rejected pull requests examined had been abandoned by reviewers and 23% were duplicates **[sourced]**.

**Who bears which risk**

| risk | who bears it |
|---|---|
| Attempts that are not merged: model cost and time | the seller |
| A maintainer who never looks, or declines after reading the diff | the seller. Reserving the issue (`/knos take`) removes the race with other sellers. It does not remove this. |
| A merged pull request that is not paid, because a funded check did not pass or a changed file was outside the allowed paths | the seller. The terms are public before the work starts. |
| Money locked until the deadline (90 days at most) | the funder |
| Paying for work that passed weak checks | the funder, who chose the checks |
| A maintainer who merges their own or a friend's pull request for someone else's money | the funder, when the funder is not the maintainer. The record marks self-payment only when the accounts or wallets match. |
| Transaction fees, rent, and tokens that fail on chain | whoever relays |
| GitHub wrong or down, a bug in the program, the program changed through the multisig | everyone ([SECURITY.md](SECURITY.md)) |
| What USDC is worth and where it can be spent | the seller |

## 4. Who buys first, and why

**Teams and foundations that already hold USDC and already pay strangers per outcome.** They need no new treasury
and no new habit. Immunefi reports "$140M+ paid in bounties across 650+ protocols" **[sourced]**
([immunefi.com](https://immunefi.com)). Superteam Earn has 2,730+ sponsors, and its stats endpoint reports a
total of 15.9 million USD **[sourced]** ([stats](https://superteam.fun/api/homepage/stats), field `totalInUSD`).
OnlyDust paid 18 million USD in grants to 4,000 contributors before it closed, saying low-skill contributors "were
flooding them with AI-generated code" **[sourced]** ([onlydust.com](https://www.onlydust.com/)). That is the
demand and the failure in one line: these buyers pay, and they judge every submission by hand. What Knos changes
for them: the outcome is a merge plus named checks, taken from GitHub's record instead of judged by hand. What is
missing: a mainnet deployment and an outside review.

**Open-source companies that post bounties today.** Algora charges them 9% and pays contributors through Stripe
Connect, typically 1 to 3 business days after the payment; a contributor Stripe will not onboard is not paid
**[sourced]** ([pricing](https://algora.io/pricing), [payments](https://algora.io/docs/payments); these pages did
not load on every attempt, see the sources in [COMPARE.md](COMPARE.md)). Knos charges 2.5% and pays any GitHub
account that gives a Solana address, in the transaction that verifies the pay token. Two honest limits. Algora pays
money a bank accepts; Knos pays USDC, test USDC today, and the payee has to be able to use it. And reach is not a
clean win: Algora's payments page lists Nigeria among its payout countries, while Stripe's own list of countries
for payouts to individuals does not ([Stripe](https://docs.stripe.com/global-payouts/recipient-requirements)). We
could not settle which is current, so we claim only that Knos needs no payment company to accept the payee. These
buyers are references more than revenue: scenario A is 1,875 USD of fees a year.

**Engineering teams buying fixed-scope changes from agent operators.** The unit already exists. On 14 Sep 2026
Sourcegraph began billing one product by "outcome-based pricing: you pay per changeset merged into your codebase"
**[sourced]** ([changelog](https://sourcegraph.com/changelog/agentic-batch-changes-ga)). There the seller counts its
own merges. With Knos the buyer's repository and GitHub's signature do the counting, for any seller, on terms fixed
before the work. These teams need private repositories, real money and an outside review. Knos settles private
repositories today; the other two are not done. They also have to be buying from someone they do not already pay
([COMPARE.md](COMPARE.md), the buyer's real question).

## 5. The test: a payment business or a free check

Knos measures one funnel.

| stage | what is counted |
|---|---|
| 1. Installed | repositories whose workflows call the Knos check |
| 2. Funded | of those, repositories that funded at least one task |
| 3. Completed | funded tasks that were paid, leaving out self-payment and test money from the faucet |
| 4. Funded again | funders who funded another task after one was paid |

Stages 2 to 4 are read from the program's own logs and shown on the site's Numbers page, with Knos's own accounts
kept apart. On 2 Oct 2026 all three were zero for outside funders **[measured]** (`scripts/network_stats.py` on
devnet, 19:56 UTC; [BENCH.md](BENCH.md)).

What each result would mean:

- **Few install.** The check is not wanted. GitHub's own controls are enough for most repositories. There is no
  business and no free tool worth running.
- **Many install, few fund.** Knos is a free check. It is worth keeping as one, and the escrow is a feature few
  need.
- **Funded, not completed.** The tasks or the terms are wrong: nobody takes them, or the checks refuse honest work.
  That is a product fault to fix before anything else.
- **Completed, not funded again.** A trial, not a habit. The buyer got the work and did not come back, so the
  escrow was not worth its trouble.
- **Funded again, by funders who are not Knos, with real money.** A payment business. The number to watch is how
  many such funders there are and what each settles in a month.

The last stage decides. It cannot be read on devnet, where the money is free.

## 6. A fork with a zero fee

Knos is MIT. Anyone can deploy the same programs with the fee set to zero, and MergePay already charges none
([COMPARE.md](COMPARE.md)). What a fork would have to match today:

- a relay that is always running and reports its latency, which someone has to run and pay for (section 1);
- settlement for private repositories;
- a payment record that counts distinct funders and leaves out self-payment and test money.

Each of these is a service, not a lock. The payment history is public: anyone can read it from the chain, a fork
included. It is not an exclusive asset and we do not count it as one. A fork takes the business the day it runs a
relay as good for less. Until then the fee is the price of that service, and competition can take it to zero.

## 7. What is not known

- Whether anyone will fund a second task with real money. There is no outside funder yet.
- What share of the spend on coding agents would move to payment per merge. No source gives it; today it is zero.
- The merge rate for strangers who answer funded tasks. The published rate covers every agent pull request; the
  only figure for bounties is one person's log.
- What relaying costs on mainnet. Priority fees are not in the measurements, and the second deployment's
  transaction counts are taken from the first.
- How long a merge takes to become a payment across many repositories.
- How many payees can turn USDC into money they can spend, and in which countries.
- Whether maintainers will put up with what a bounty attracts. curl ended its bug bounty over the reports it drew
  ([WHY.md](WHY.md), section 2).
- How these payments are taxed and regulated, in any country. Not examined.
- The market price of a merged change. Sourcegraph does not publish its rate.
- How many of Algora's customers still post bounties.
- When an outside review will happen and what it will cost.
