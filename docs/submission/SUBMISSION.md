# Knos

**AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed.**

## What it is

Knos pays for agent work on proof instead of on the agent's word. A maintainer funds a GitHub issue with one
comment. Whoever's pull request is merged for it is paid to their GitHub account, with no wallet or sign-up. The
money is held by a Solana program that releases it only on a token GitHub signed, and that checks GitHub's RSA
signature on chain. The program has no admin and no upgrade authority.

- Site: [drexthealpha.github.io/Knos](https://drexthealpha.github.io/Knos/)
- Code: [github.com/drexthealpha/Knos](https://github.com/drexthealpha/Knos) (MIT)
- Network: Solana devnet. The money is test USDC.

## The six criteria

**Functionality.** The whole flow runs on devnet today: fund with a comment, a check on the pull request, paid on
merge to a GitHub account, claimed from a repository the author owns ([demo_script.md](demo_script.md) has the
links). The programs are tested against 517 Wycheproof vectors, differentially against OpenSSL, and with a
10,000-step random walk; the judge against 21 cheating pull requests ([BENCH.md](../BENCH.md),
[TAMPER.md](../TAMPER.md)). Not done: an outside audit, and mainnet.

**Potential impact.** Two things. A payment rail for agent work that pays for results: coding agents sell about
$5B a year of attempts, and 75.41M agent payments a month already settle on chain with no proof of delivery
([MARKET.md](../MARKET.md), every assumption labelled). And a primitive for the ecosystem: `knos-oidc` lets any
Solana program require a fact GitHub or GitLab signed, for free.

**Novelty.** Verifying GitHub's signature on a chain has been done once before, a week earlier, on an EVM chain,
with an owner who registers the keys ([COMPARE.md](../COMPARE.md), [DISCLOSURE.md](../DISCLOSURE.md)). What is new
here is that nobody holds that power: the issuer's keys are constants in an immutable program and rotate only on
the issuer's own signature. We found no OIDC verifier on Solana at all.

**UX.** The funder types one comment on GitHub. The worker needs a GitHub account and nothing else. The chain is
what makes that possible: money committed up front with no company holding it, payable to an account id anywhere
in the world, claimable without asking anyone.

**Open source and composability.** MIT throughout. The verifier is a separate program with a documented interface
([OIDC.md](../OIDC.md)), an example consumer with its test, and a dependency-free JavaScript client checked byte
for byte against the Python one. Builds are reproducible (`solana-verify`).

**Business.** 2.5% of each payment (at least 0.05 USDC), only when someone is paid, fixed in the program. A payment
costs 105,000 lamports to relay. Planned and not built: a per-repository plan for the pull-request check on private
repositories. [MARKET.md](../MARKET.md) has the unit economics, including what the first payout to a new person
costs.

## Team

drexthealpha, working alone. Knos 0.1 won the Sibyl Labs hackathon in September 2026.

## Traction and demand

What exists: the Agent PR Index (2,431 agent pull requests, checked and published, rebuilt every 6 hours), which is
the evidence for the problem; and a public count of outside use, kept apart from our own, on the site's Numbers
page. There are no paying customers and no real payments yet, and we do not claim otherwise.

## How it reaches people

It lives inside GitHub (a comment to fund, a pull request to earn), the free check needs no install, and a payee
finds money waiting under their GitHub name before they know the product ([MARKET.md](../MARKET.md), section 3).

## Prior work

Knos 0.1 (shared memory for agents) predates the hackathon and is not in this submission. Everything in this
repository was built from 29 Sep 2026 ([DISCLOSURE.md](../DISCLOSURE.md)).

## What is next (plans, not shipped)

An outside audit, then mainnet with real USDC. More issuers for the verifier. The per-repository plan.
