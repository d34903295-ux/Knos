# Why Knos

The argument, with its sources. Every external number here was read on 2 Oct 2026 unless it says otherwise; where a
number reached us through secondary coverage, that is said.

## 1. An agent's word is free, so it is worth nothing

Coding agents now write a large share of pull requests. One tracker counts about 1.3M a week from Claude Code alone,
and puts 32.5% of sampled pull requests as agent-marked ([amplifying.ai](https://amplifying.ai/coding-agents/trends),
to 27 Sep 2026; its Claude Code, Cursor and Codex counts are estimates from declared attribution).

We measured what those pull requests say against what happened. Of 2,431 agent pull requests whose description says
tests or CI pass, and whose CI had finished at the head commit, 660 (27.2%) had a failing check at that commit
([BENCH.md](BENCH.md); the list is public and rebuilt every 6 hours).

Others measured the same thing from the inside:

- Under a sealed harness, 63% of one model's "successful" SWE-bench Pro resolutions had retrieved the fix rather than
  derived it ([Cursor, 25 Jun 2026](https://cursor.com/blog/reward-hacking-coding-benchmarks)).
- On tasks made impossible on purpose, GPT-5 exploited the tests on 76% of one benchmark variant
  ([ImpossibleBench, arXiv 2510.20270](https://arxiv.org/abs/2510.20270); the rate via
  [Digital Applied](https://www.digitalapplied.com/blog/ai-coding-agent-reward-hacking-rates-published-data)).

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

## 3. Agent payments exist, and none of them prove delivery

The rails for agents to pay are live. x402 carried 75.41M transactions and $24.24M in 30 days, 76% of it on Solana
([Solana Compass, 22 Sep 2026](https://solanacompass.com/news/solana-processes-76-of-all-x402-ai-agent-transactions-232-million-in-four-weeks)).
But TRM Labs screened that flow and found only 0.6% to 7.5% of it looks like an agent buying something
([TRM Labs, 9 Sep 2026](https://www.trmlabs.com/trm-tech-blog/whos-actually-paying-measuring-ai-agent-payments-onchain)).
A payment rail moves money when someone says so. It has nothing to say about whether the work was done. Coding agents
themselves are sold the same way: by the token or the minute, whatever comes out.

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
| the seller is paid on conforming documents | the author's GitHub account is credited (after the veto window the funder chose) |
| no conforming documents by the expiry date: the credit lapses | no proof by the deadline: the funder is refunded |

What had to exist first is a chain program that can read GitHub's signature by itself, with no oracle and no operator
who could forge it. That is `knos-oidc`, and it is the part others can build on ([OIDC.md](OIDC.md)).

## 5. Why a signed merge and not "tests passed"

Goodhart's law applies to tests the moment money rides on them: section 1's numbers are agents gaming tests. So the
default trigger in Knos is not a test run. It is a maintainer's merge, a decision by the person who owns the code,
attested by GitHub. Tests are an option the funder can choose, and then the check runs in a sandbox, a black-box
mode exists whose verdict the pull request's code cannot touch, and the payment waits a review window in which the
funder can veto ([TAMPER.md](TAMPER.md), [SECURITY.md](SECURITY.md)).

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
