# Knos: the submission

**Bounties that pay when the pull request is merged with the checks you named passing. Attested by a GitHub-signed workflow run, verified on Solana.**

- Site: [drexthealpha.github.io/Knos](https://drexthealpha.github.io/Knos/)
- Code: [github.com/drexthealpha/Knos](https://github.com/drexthealpha/Knos) (MIT)
- Network: Solana devnet. The money is test USDC. Track: Solana.

The text for each field of the form, each under a thousand characters. A number that only the release run can measure
is a slot, `[[stat: name]]`; `python scripts/bench_docs.py --slots` lists the ones still open. Three fields state
facts about the founder that nothing in the repository can back; each says so, and the founder confirms it before
pasting. The scripts for the two videos are [pitch_script.md](pitch_script.md) and [demo_script.md](demo_script.md);
the weekly update is [weekly_update.md](weekly_update.md).

## whatBuilding

Knos pays the author of a GitHub pull request from an escrow on Solana when it is merged with the checks the funder
named passing, attested by a GitHub-signed workflow run. A maintainer funds an issue with one comment and names the
checks that must pass: `/knos fund 20 checks: test`. Those terms are fixed on chain. When she merges a pull request
that closes the issue, her repository's workflow reads the merge and the check results from GitHub and asks GitHub
to sign that run. A Solana program verifies GitHub's RSA signature on chain and pays the author's wallet, less 2.5%. There is no veto after the merge, and no company holds
the money. The verifier is a separate program: any Solana program can use it to require a fact GitHub signed. It
runs on devnet, in test USDC. Until an outside review, I can change the programs only through a multisig with a
public 48-hour delay.

## whyNow

Coding agents open pull requests in very large numbers, and a description is not evidence. In 826 repositories, the
first agent pull request that said tests pass had a failed check of some kind in 147 (17.8%), and a failed test or
build in 80 (9.7%). Pricing by outcome has begun where the seller keeps the count: on 14 Sep 2026 one vendor
started billing per merged changeset. And GitHub is changing what a workflow may do. From 2 Nov 2026 it blocks
`pull_request_target` workflows on public repositories unless an admin allows them, and that event is how Knos's
first deployment read a merge. The second reads it from the push the merge makes. On Solana, checking
GitHub's RSA signature takes two transactions.

## repoContext

The hackathon began on 14 Sep 2026. The public repository's history starts on 1 Sep 2026. Of its 209 commits up to
Knos 0.3.11, 83 predate the hackathon: Knos 0.1 (1 to 7 Sep 2026), more work on it until 12 Sep, and daily
automatic commits. Knos 0.1 was shared memory for coding agents built on Sibyl, and it won the Sibyl Labs
hackathon. By `git blame` at the 0.3.11 commit, 1.1% of the lines are older than the hackathon: the changelog
entries for Knos 0.1, the licence, package metadata and scaffolding. The other 98.9% were last changed between
30 Sep and 2 Oct 2026, and 0.3.12 was built after that. Work on two experiments that never shipped began on 13 Sep; none
of their code is in the repository. The commits were written with coding agents; I review and commit each one.
docs/DISCLOSURE.md has the lists, what came from elsewhere, and the commands that reproduce every count.

## marketValidation

No interviews and no outside funder yet. This is what has been measured. The problem: of 241 merged agent pull
requests whose description said tests pass, 30 had a failed check at the head commit. The market for bounties on
issues is small, and we say so: 64,291 USD was open across every board on 2 Oct 2026. The buyers we expect first
are teams that already hold USDC and pay strangers per outcome, companies that post bounties today, and engineering
teams that buy fixed-scope changes from people who run agents (docs/MARKET.md, section 4). One funnel decides
whether this is a payment business or a free check: repositories that install the check, those that fund a task,
tasks paid, funders who fund again. The last stage cannot be read on devnet, where the money is free.

## traction

No traction is claimed beyond these counts. By 3 Oct 2026 no outside repository had funded a task: 11 bounties
had been funded and 6 paid on the first deployment on devnet, every one of those payments Knos's own account paying
itself to prove the path. On the second: 6 tasks paid. Across both, 0 paid
tasks were funded by someone other than Knos with their own tokens, by 0 funders, of whom
0 funded again. The median from merge to paid is
110 seconds, over 2 payments. The site's Numbers page shows
today's counts, read from the programs' own logs, with Knos's own accounts kept apart from everyone else's.

## competition

GitHub's own controls (required checks, rulesets, the merge queue) gate a merge and move no money. Bounty boards
(Algora, Opire, BountyHub, TaskBounty) pay on a person's decision, or on a test run in the board's own sandbox.
MergePay verified GitHub's signature on an EVM chain before Knos did on Solana. It has been on a mainnet with real
USDC since 24 Sep 2026 and charges no platform fee, and it is ahead there. Its payment condition is the merge
alone. What Knos adds: the checks a funder names are part of the condition and are fixed at funding; the workflow
is pinned by commit; a merge is read from a push, not from `pull_request_target`; signing keys wait, expire and
can be revoked; one comment funds from a prefunded balance; and the verifier is on Solana, where we found no other.
Where Knos is behind: devnet only, no outside review, a 2.5% fee. docs/COMPARE.md has every source.

## monetization

2.5% of each payment, at least 0.05 USDC, taken by the program only when someone is paid. Nothing on a refund or a
withdrawal. The check on pull requests and the verifier are free. At 2.5%, 1 million USD of fees a year takes 40
million USD of settled payments; our three bottom-up scenarios together reach about an eighth of that, and we claim
no more (docs/MARKET.md). The code is MIT, so a fork can set the fee to zero. What it would have to match is a
service: signing keys kept attested, a relay kept running, settlement for private repositories. The path to real
money is an outside review first, then a mainnet deployment with program ids of its own.

## teamCommitment

One person, full time, under the handle drexthealpha, which is a pseudonym. I work with coding agents every day,
and this repository was built with them; I review and commit every change. Before this hackathon I built Knos 0.1
(1 to 7 Sep 2026), shared memory for coding agents built on Sibyl, and it won the Sibyl Labs hackathon. I built what
is here because my own agents told me the tests passed when they had not. What comes next is an outside review of
the second deployment, then mainnet.

## externalContributors

One outside account, jaystay-bot, wrote three pull requests: #32 (`knos_bounties` says what each bounty is about),
#33 (a repository's own record in the Agent PR Index, on the site) and #34 (a Ruby runner for the judge). Each
answers a bounty Knos funded on its own repository through the first deployment on 2 Oct 2026, in test USDC: issues
#29, #30 and #31. Their code is in 0.3.12, in commits under that account's name. On 3 Oct 2026 the three bounties
were funded and still open on devnet, and the pull requests were not yet merged. Merged and paid when the release
ran: 3 of the three.

## legalEntity

*The founder confirms this before pasting; nothing in the repository can back it.*

None. No company has been formed for Knos. It is one person's work, published under the MIT licence.

## investmentReceived

*The founder confirms this before pasting; nothing in the repository can back it.*

None.

## liveToken

*The founder confirms this before pasting.*

No. Knos has no token of its own. It pays in the token a funder chooses, and the one it is built for is USDC. On
devnet a faucet inside the program mints test USDC, which has no value.

## liveProductLink

https://drexthealpha.github.io/Knos/ is the product: paste a pull request to check it, put the workflow file in a
repository, fund an issue with one comment, and see every payment the programs have made. The code is at
https://github.com/drexthealpha/Knos and the package at https://pypi.org/project/knos/. Everything is on Solana
devnet, and the money is test USDC.

## chains

Solana, on devnet. Track: Solana.

## chainUsage

Two programs on Solana devnet, written for Solana directly, with no framework. `knos-oidc`
(`FkwZdsYCmzicJMtHLTkPK76bYNVG4WNwkWJBiVWNtF3W`) verifies a GitHub Actions or GitLab CI token on chain: the token
is written into an account, and its RS256 signature is checked in Montgomery form, in two transactions for GitHub's
keys, because one transaction's compute budget cannot hold the arithmetic. `knos-pay`
(`5y7iWJ1VAMJjnnWbbdo2a2PsWJEwTExSNpzrvQSEnS8k`) is the escrow. It reads the verified claims, checks them against
the bounty (repository, workflow file and commit, issue, the hash of the terms) and moves SPL Token or Token-2022
tokens: into a vault at funding, to the payee's wallet and the fee account on a pay token, back to the funder at the
deadline. Every account is a PDA. Squads holds the upgrade authority, with a 48-hour time lock, and the guardian
role. The site builds and reads transactions in the browser; there is no server.
