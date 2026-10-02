# Why Knos

The argument, with its sources. Every external number here was read on 2 Oct 2026 unless it says otherwise; where a
number reached us through secondary coverage, that is said.

## 1. An agent's word is free, so it is worth nothing

Coding agents now write a large share of pull requests. One tracker counts about 1.3M a week from Claude Code alone,
and puts 32.5% of sampled pull requests as agent-marked ([amplifying.ai](https://amplifying.ai/coding-agents/trends),
to 27 Sep 2026; its Claude Code, Cursor and Codex counts are estimates from declared attribution).

We measured what those pull requests say against what happened. In 826 repositories, the first agent pull request
whose description said tests or CI pass had a failing check at its head commit in 147 (17.8%). Counting every such
pull request instead of one per repository it is 660 of 2,431 (27.2%), a figure that leans on a few busy
repositories, so we quote the first ([BENCH.md](BENCH.md); the list is public and rebuilt every 6 hours). A failing
check is GitHub's record; it does not say why the check failed, and a description can be written before CI finishes.
What it shows is that the description is not evidence.

Others measured the same thing from the inside:

- Under a sealed harness, 63% of one model's "successful" SWE-bench Pro resolutions had retrieved the fix rather than
  derived it ([Cursor, 25 Jun 2026](https://cursor.com/blog/reward-hacking-coding-benchmarks)).
- On tasks made impossible on purpose, GPT-5 exploited the tests on 76% of one benchmark variant
  ([ImpossibleBench, arXiv 2510.20270](https://arxiv.org/abs/2510.20270); the rate via
  [Digital Applied](https://www.digitalapplied.com/blog/ai-coding-agent-reward-hacking-rates-published-data)).

- Passing tests is not the same as mergeable work either: when maintainers reviewed 296 agent pull requests that
  passed the benchmark's tests, they would have merged roughly a third to a half
  ([METR, 10 Mar 2026](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/)).

This is what Spence described in 1973: a signal carries information only if it is costly to fake. "All tests pass" in
a description costs nothing to write.

## 2. So the market for paid agent work is failing, the way Akerlof said it would

When buyers cannot tell good from bad, they stop paying for either (Akerlof, 1970). That is happening to open-source
bounties now:

- One study of the Algora bounty board found 1,470 payouts in 2025 against 175 in 2026 to its date, 73.2% of 529 open
  bounties unreachable in practice, and 2 payouts in the trailing 30 days
  ([incubagent, 10 Aug 2026](https://incubagent.com/research/agent-bounty-market/); one venue, one date).
- A single bounty issue on one repository drew 253 bot comments and 27 untested AI pull requests in a day
  ([AI Weekly](https://aiweekly.co/alerts/archestra-blocks-ai-bot-spam-with-git-contributor-gate), secondary).
- curl closed its bug bounty on 26 Jan 2026; Ghostty added vouching; Jazzband announced its sunset
  ([codenote](https://codenote.net/en/posts/oss-ai-slop-contribution-policy-shift/), secondary).

Supply is not the problem. Agents will swarm any bounty. Trust is.

## 3. Agents are paid for attempts, because nobody neutral says what was delivered

Coding agents are sold by the seat, the token or the minute of compute, whatever comes out: Cursor about $4B a year
([Dealroom, 9 Jun 2026](https://dealroom.co/news/134107-cursor-tops-4b-annualized-revenue/)), Claude Code over
$2.5B ([Anthropic, Feb 2026](https://www.anthropic.com/news/anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation)),
Cognition $492M ([TechCrunch, 27 May 2026](https://techcrunch.com/2026/05/27/ai-coding-startup-cognition-raises-1b-at-25b-pre-money-valuation/)).
We found no coding-agent vendor that charges per merged pull request.

Where the seller can define the outcome, buyers already pay per outcome. Intercom's Fin charges $0.99 per resolved
conversation and is near $100M a year
([Mostly Metrics via EnterpriseDNA, 2 Aug 2026](https://enterprisedna.co/resources/ai-pulse/ai-pulse-2026-08-02-intercom-s-fin-ai-agent-is-nearing-100m-arr-roughly-half-of/),
secondary); the share of AI companies pricing by outcome rose from 18% to 23% in six months
([ICONIQ, State of AI 2026](https://www.iconiq.com/growth/reports/state-of-ai-2026)). In each case the vendor's own
system decides what counts as "resolved", and the buyer has to trust it.

Code is the one kind of agent work where a neutral third party already signs the outcome. GitHub records who opened
a pull request, what its checks said and who merged it, and will sign that statement for free. What was missing is
something that moves money on that signature without anyone in the middle who could forge it.

The same gap shows in agent payments on chain: x402 carried 75.41M transactions in 30 days, 76% of it on Solana
([Solana Compass, 22 Sep 2026](https://solanacompass.com/news/solana-processes-76-of-all-x402-ai-agent-transactions-232-million-in-four-weeks)),
and a rail moves money when someone says so; it has nothing to say about whether the work was done.

Holmström (1979) is the reference for what to do when effort cannot be observed: pay on a verifiable signal of the
output.

## 4. Trade solved this centuries ago: pay against a third party's document

A merchant in one port and a buyer in another do not trust each other, and neither can see the goods. The letter of
credit fixed that: the bank pays the seller when the seller presents documents that conform, above all a bill of
lading, signed by the carrier, a third party with no stake in the sale. The bank never inspects the cargo. Banks
reported about $550B of documentary trade exposure in 2022 with export letter-of-credit loss rates of 0.02%
([ICC Trade Register via TFG](https://www.tradefinanceglobal.com/posts/breaking-trade-finance-default-rates-rise-icc-trade-finance-register)).

Knos is a letter of credit for agent work:

| letter of credit | Knos |
|---|---|
| the buyer's bank holds the money | a Solana program holds it, and nobody can change the program |
| the carrier signs a bill of lading | GitHub signs a token: this pull request, by this account, was merged in this repository |
| the bank checks the document, not the goods | the program checks GitHub's RSA signature and the token's claims, on chain |
| the bank has a fixed time to examine the documents, pays on silence, and refuses only for a narrow reason ([UCP 600, articles 14 and 16](https://www.skrine.com/insights/alerts/july-2022/banks-duty-in-examining-documents-presented-u-1)) | a review window the funder chose; silence pays; `/knos veto` from a maintainer takes it back |
| the seller is paid on conforming documents | the author's GitHub account is credited |
| no conforming documents by the expiry date: the credit lapses | no proof by the deadline: the funder is refunded |

What had to exist first is a chain program that can read GitHub's signature by itself, with no oracle and no operator
who could forge it. That is `knos-oidc`, and it is the part others can build on ([OIDC.md](OIDC.md)).

## 5. Why a signed merge and not only "tests passed"

Goodhart's law applies to tests the moment money rides on them: section 1's numbers are agents gaming tests. So the
default trigger in Knos is a maintainer's merge, a decision by the person who owns the code, attested by GitHub,
**plus** a check that runs none of the pull request's code: if the description says tests pass and GitHub's record
of the merged commit says a check failed, nothing is paid. Tests alone are an option the funder can choose; then the
checks run in a sandbox, a black-box mode exists whose verdict the pull request's code cannot touch, and the payment
waits a review window in which the funder can veto ([TAMPER.md](TAMPER.md), [SECURITY.md](SECURITY.md)).

What neither mode proves is that the work is good. A signature proves what happened, not whether it was wise
([SECURITY.md](SECURITY.md) says exactly where the line is).

## 6. Why now, and why on a chain

- **Now**: agent pull requests went from about 4M a month to 17M+ in six months (The Information, via
  [ai2.work](https://ai2.work/blog/github-agentic-pull-requests-surge-28-fold-in-ten-months); the paywalled original
  was not verified), while the bounty market that should pay for them shrank.
- **A chain**, because the three things this needs are things a chain does and a company cannot promise: money held
  by code nobody can change; a payee who needs no account with anyone (a GitHub id is enough, anywhere in the
  world); and a verifier any other program can call. A company running the same escrow would be one more party to
  trust, which is the problem being solved.
- **Solana**, because verifying GitHub's RSA-2048 signature on chain takes two transactions of under 1M compute units
  each, and relaying a whole proof is seven transactions, 35,000 lamports of fees ([BENCH.md](BENCH.md)); and because
  most agent payment traffic is already there.

## 7. How new infrastructure like this has spread before

- Let's Encrypt made a signature free and automatic, and HTTPS went from under 30% of page loads to about 80% in
  roughly five years ([Let's Encrypt, Dec 2025](https://letsencrypt.org/2025/12/09/10-years)). Verification that
  costs nothing and needs no human becomes the default.
- PayPal grew inside someone else's marketplace: 68.3% of its payment volume came from online auctions in the nine
  months to Sep 2001 ([S-1/A](https://www.sec.gov/Archives/edgar/data/0001103415/000091205702004514/a2060419zs-1a.htm)).
  Knos lives inside GitHub the same way: the funder comments on an issue, the worker opens a pull request, and
  neither leaves.

Neither comparison is a forecast. They are why the product is free to verify with, and why it asks nobody to go
anywhere new.
