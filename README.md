# Knos

**Bounties that pay when the pull request is merged with the checks you named passing. Attested by a GitHub-signed workflow run, verified on Solana.**

A pinned workflow reads the merge and the check results from GitHub. GitHub signs that workflow run. A Solana program verifies the signature itself and pays the author in the same minute. Nobody holds the money in between, and nobody decides after the fact.

One buyer, one task, one decision, one payment, and then the next task:

1. **A buyer.** A maintainer has an issue she would pay 20 USDC to have fixed.
2. **A task, with terms.** She comments on the issue: `/knos fund 20 checks: test`. The money goes into a program on
   Solana, and Knos replies with the terms: paid when a pull request that closes this issue is merged, if the check
   `test` passed at that pull request's last commit. The terms are logged on chain, and nothing in the program
   changes them afterwards.
3. **An acceptance decision.** Someone opens a pull request: a person, or a coding agent somebody runs. She reads it
   and merges it. That is the only decision, and it is hers. Had `test` failed at that commit, her merge would not
   pay, and a comment on the pull request would name the check that failed.
4. **A payment.** Her repository's workflow reads GitHub's record of the merged commit and asks GitHub to sign what
   it found. A Solana program checks GitHub's signature and pays the author's wallet, less a 2.5% fee. Nobody
   approves the payment, and once it is made nobody can take it back.
5. **A repeat purchase.** She comments `/knos fund 20` on the next issue.

Everything runs on **Solana devnet**, and the money is **test USDC**. There has been no outside review. Until there
is one, Knos can change the programs, only through a multisig and only after a public 48-hour delay
([the trust model](docs/SECURITY.md)).

Try it: [drexthealpha.github.io/Knos](https://drexthealpha.github.io/Knos/). Paste any agent pull request to see
whether its "tests pass" is true, put the workflow file in a repository, and fund an issue with one comment.

## Why

Coding agents open pull requests by the million, and a pull request's own description is not evidence.

<!-- bench:headline -->
**Agent PR Index, 2026-10-02.** In 826 repositories, the first pull request by an AI coding agent whose description said tests or CI pass had:

- **a failed check of any kind** at its head commit in 147 (17.8%; 95% interval 15.3% to 20.6%);
- **a failed test, build, lint or type-check job** in 80 (9.7%; 95% interval 7.9% to 11.9%). Names decide this one, so read it as the cautious figure.

Of the 241 merged agent pull requests in the smaller sample that records merges, 30 (12.4%) had a failed check while their description said tests pass. A failed check is GitHub's record, not a judgment of why it failed.
<!-- /bench:headline -->

So nobody pays an agent on its word, and agents are mostly billed by the seat or by the token, whatever comes out.
Code has what most agent work lacks: a third party that already records the result. GitHub records who opened a pull
request, what its checks concluded and who merged it, and it signs statements about a workflow run. A letter of
credit works the same way: the bank pays against a document a third party signed, never against the seller's word.
Here the document is GitHub's signed token and the bank is a program.

One limit belongs next to that picture. GitHub signs that a workflow ran, in which repository, at which commit, and
what it asked for. It does not sign what the workflow read. That the checks passed, and who the author is, is the
workflow's own reading of GitHub's record, on a runner of the funder's repository. So the signed statement is only as
honest as the funder's own repository, and the programs let it move that funder's money and nobody else's.
[docs/WHY.md](docs/WHY.md) has the argument and its sources; [docs/MARKET.md](docs/MARKET.md) says how small the
bounty market is today.

## How it works

**Install.** One workflow file in the repository, `.github/workflows/knos.yml`
([examples/knos-workflow.yml](examples/knos-workflow.yml)). It needs no secret, and nothing in the repository holds
a key or money. A second, optional file puts a check on every pull request
([examples/knos-check.yml](examples/knos-check.yml)).

**Fund with terms.** The repository's owner, or someone the owner lets spend its balance, comments on an issue:

```
/knos fund 20 checks: test, build paths: src/** days 30 reserve 3
```

Only the amount is needed. The rest are the terms, fixed at that moment:

- `checks`: the checks that must have passed at the merged pull request's last commit. When none are named, Knos
  takes the default branch's required status checks; when there are none of those, every check that ran to an end
  on the default branch's latest commit. A repository with no checks gets none, and the reply says so: "your merge
  alone is the acceptance".
- `paths`: the files a pull request may change. By default it may not change `.github/**` or `.knos/**`, so it
  cannot edit what judges it.
- `days`: how long the money waits for a payment before it goes back (14 by default, 90 at most).
- `reserve`: how many days a contributor's `/knos take` holds the issue (7 by default).

The funding transaction logs the terms and the bounty stores their hash. A payment must carry the same hash.

On devnet a faucet inside the program mints the test USDC, so the comment is all it takes. With a token of real
value the owner first sets money aside from a wallet: a balance for one GitHub owner, with a cap per bounty and up
to four other GitHub accounts that may spend it by comment. Only that wallet can take unspent money back out.

**The check.** With the second file, every pull request gets a check that runs none of its code: whether a "tests
pass" in its description is true at its head commit, the repository's `CONTRIBUTING.md` rules, and, for a pull
request that closes a funded issue, what the payment still needs. It is advice. What decides a payment is read
again at the merge, from the default branch, where a pull request cannot change it.

**Merge.** The push a merge makes starts the workflow. For each pull request that push merged and that closes a
funded issue, it reads the terms from the chain and GitHub's record of the pull request's last commit. Every
required check must have passed there, and every changed file must be in scope. If so, it asks GitHub for a token
that says so. If not, it asks for nothing and comments which check was in which state: failed, skipped, still
running, absent, or unreadable. `/knos settle` on the pull request tries again.

A maintainer who does not want a pull request to take the bounty says so before merging: `/knos reject`. After the
merge and the signed token there is no veto and no waiting time.

**Paid.** Anyone can carry the token to Solana; Knos's public relay does, and a repository with its own relay key
does it itself. `knos-oidc` checks GitHub's RSA signature on chain. `knos-pay` checks the token against the bounty
(the repository, the workflow file and its commit, the issue, the terms' hash) and pays: 2.5% to Knos, at least
0.05 USDC, and the rest to the author. How long that takes from the merge is measured on devnet, not promised:
[docs/BENCH.md](docs/BENCH.md).

**Who is paid, and where.** The person who opened the pull request. When a bot account opened it (a coding agent),
a person is paid only if GitHub authenticates an act of theirs or of a maintainer: the issue is assigned to them, a
maintainer wrote `/knos pay @login`, or they are an assignee of the pull request and wrote `/knos mine`. A line in
the description never decides. The money goes to the wallet they bound to their GitHub account, else to the
address in their own `/knos address <address>` comment on the pull request. With neither, the bounty is held for
them for 180 days. It is paid when they bind a wallet, and goes back to the funder if they never do.

**Binding a wallet is by hand.** Once: create a repository from the template `drexthealpha/knos-claim`, open its
Actions tab, choose "knos claim", press "Run workflow" and paste your address yourself. Or run
`knos claim <address>`, which does both. No link, repository description or push can carry an address in, because an
address in a link could be someone else's.

**No payment.** With no payment by the deadline the money goes back where it came from. A refund needs no token, so it
works whatever happens to GitHub or to Knos.

**Paid without a merge ("tests mode"), for a black-box check only.** Payment without a merge is offered only when
the acceptance bundle is black-box: the submission runs as a separate process and only its output is compared.
Everything else is funded in merge mode. The reason is measured in [docs/TAMPER.md](docs/TAMPER.md): of 63 cheating
pull requests, plain CI passed 56, acceptance tests that run in the same process as the code passed 7, and the
black-box check passed none. The bundle sits in `.knos/acceptance/<issue>/` before funding, is fixed by its hash in
the terms, and runs in a sandbox (another user, an empty environment, no network while it runs). Nobody merges or
approves at that moment.

Every comment with a line that starts with `/knos` gets a reply, a mistyped one included. `/knos help` lists the
commands.

## What we found nowhere else

Read on 2 Oct 2026; [docs/COMPARE.md](docs/COMPARE.md) has the neighbours, the method, and where they are ahead.

- **GitHub's signature verified by a Solana program.** `knos-oidc` verifies an RS256 OpenID Connect token from
  GitHub Actions on chain, and any other program can read the verified claims. We found no other program on Solana
  that verifies an RS256, JWT or OIDC token. On an EVM chain MergePay did it first.
- **Named checks as a condition of payment, fixed at funding.** The checks that must have passed at the merged
  commit, and the paths that may change, are hashed into the bounty when the money goes in. On MergePay the
  condition is the merge alone. On the bounty boards a person judges.
- **Payment on a black-box check, by a program.** The funder's check is fixed by hash before the work. The
  submission runs as a separate process in a sandbox in the funder's own workflow, only its output is compared, and
  the program pays with no person in between. One board, TaskBounty, also pays on a test run, in a sandbox it
  operates.
- **GitLab CI tokens and 4096-bit keys.** The same verifier takes GitLab CI tokens and RSA-4096 signatures (one of
  GitLab's three keys is 4096-bit). On the second deployment a GitLab key has to be admitted first, like every key
  that is not one of GitHub's four. The escrow itself pays on GitHub's tokens only.

## Install

[docs/INSTALL.md](docs/INSTALL.md) lists every route with its exact commands:

- **A repository:** the workflow file above, or only the free check as a GitHub Action.
- **A terminal:** `pip install knos`, then `knos --help`.
- **Coding agents:** `knos init` adds a Stop hook and a read-only MCP server to the agents on the machine. There
  are also a plugin for Claude Code and Codex, an extension for Gemini CLI, and one-click links for Cursor and VS
  Code. The hook runs the check itself when an agent says tests pass or it is done. The MCP server lets an agent
  list funded issues, check a pull request's claims and see what its operator is owed.
- **Other programs:** a Rust crate with no dependency that reads a verified token, both programs' IDLs, and a
  JavaScript client ([docs/OIDC.md](docs/OIDC.md)).

## What is in this repository

| | |
|---|---|
| [`programs-v2/knos_oidc`](programs-v2/knos_oidc) | **OIDC on Solana, second deployment.** Verifies a GitHub Actions or GitLab CI token (RS256, 2048- and 4096-bit keys) on chain. A new signing key needs GitHub's own signature, waits a day, needs a guardian's approval, and expires 30 days after it was last attested. |
| [`programs-v2/knos_pay`](programs-v2/knos_pay) | **Pay on a signed token, second deployment.** Balances, bounties with terms, payment to a wallet, held payments, refunds. SPL Token and Token-2022 mints. |
| [`programs`](programs) | The first deployment of both programs, exactly as deployed. Immutable. |
| [`examples/oidc_gate`](examples/oidc_gate) | Another program built on `knos-oidc`: it records the last commit GitHub signed for a repository. |
| [`.github/workflows`](.github/workflows) | `fund.yml`, `prove.yml`, `check.yml`: the workflows a repository calls. They are published at one pinned commit of `drexthealpha/knos-workflows`, and a bounty records that commit. |
| [`crates/knos-oidc-interface`](crates/knos-oidc-interface), [`idl`](idl) | What another team's program depends on to read a verified token, and the IDLs of both deployments. |
| [`src/knos`](src/knos) | The four commands the workflows run (`knos command`, `knos settle`, `knos review`, `knos check`), the judge, the relay anyone can run (`knos relay`), `knos claim`, the Stop hook and `knos mcp`. |
| [`sdk/settle`](sdk/settle) | A JavaScript client with no dependency, checked byte for byte against the Python client. |
| [`web`](web) | The site. It reads GitHub and Solana in the browser; there is no Knos server. |
| [`docs`](docs) | [SECURITY](docs/SECURITY.md) (who is trusted for what, and every known limit), [ASSURANCE](docs/ASSURANCE.md) (the rules the tests enforce, how to reproduce them, where a reviewer should start), [BENCH](docs/BENCH.md) (every measured number and how to re-run it), [TAMPER](docs/TAMPER.md), [COMPARE](docs/COMPARE.md), [MARKET](docs/MARKET.md), [WHY](docs/WHY.md), [OIDC](docs/OIDC.md), [INSTALL](docs/INSTALL.md), [DISCLOSURE](docs/DISCLOSURE.md). |

## Two deployments

| | first deployment (`programs`) | second deployment (`programs-v2`) |
|---|---|---|
| `knos-oidc` | `vpWym9azbPU5f2PH2a6n8c4RfmsyUeW2dMuWr1DSHcE` | `FkwZdsYCmzicJMtHLTkPK76bYNVG4WNwkWJBiVWNtF3W` |
| `knos-pay` | `9UzPFbh2A4e4sEPgngKG523FfYLnQ3qPfFVfFTTAdfDi` | `5y7iWJ1VAMJjnnWbbdo2a2PsWJEwTExSNpzrvQSEnS8k` |
| who can change it | nobody: neither program has an upgrade authority | Knos, through a Squads multisig whose vault `CKCrTBN542pVhizxuSPVg8tvdnPooNxt9B7o97VuTjz2` is the upgrade authority. An approved upgrade waits 48 hours in public before it can run |
| what it is for | the bounties funded on it finish on it. This release's workflows fund nothing new there. It stays as the record of 0.3.10 and 0.3.11 | every new bounty |

The first deployment was made immutable on the day it was deployed, before anyone outside had read it. Three outside
reviews then found defects that cannot be patched there: a funder could merge and then veto, taking the money back;
what a merge had to satisfy was not fixed on chain when the money went in; and a signing key could neither expire
nor be revoked, and could be attested from any repository. The second deployment answers them, and stays changeable
until an outside review so that the next defect can be fixed. Today the members of its upgrade multisig are all the
founder's own keys, so it is the 48-hour delay, not the number of signers, that protects a user. After the review
the upgrade authority is removed.

A second multisig, the guardian (vault `AT1aKj1DpgaWerxmS4YjDkNpWPNUtCCKVDvxLhFxg5Jc`, no delay), can approve or
revoke a signing key and can pause new funding for at most 7 days. It cannot add a key or move money, and its pause
does not reach payments, refunds or withdrawals. Revoking a key does stop the payments that depend on tokens that
key signed. `knos mainnet-check` reads all of this from the chain.

## Limits

[docs/SECURITY.md](docs/SECURITY.md) has all of them. The ones to know first:

- **Devnet, test USDC, no outside review.** `knos mainnet-check` fails on that line on purpose, and mainnet stays
  locked until a review exists. A mainnet deployment will use new program ids.
- **A signature authenticates a statement, not the truth.** A funder's repository that lies can pay whom it likes,
  from its own bounties only.
- **The signed token is asked for by the buyer's own repository.** A buyer who removes the workflow before merging takes
  the work and is never made to pay; the money goes back at the deadline. What the chain enforces is narrower:
  the terms cannot change, and once the signed token exists nobody can stop or reverse the payment.
- **A passed check is not good work.** A bounty buys what its terms say, and the funder chose the terms. Of 63
  cheating pull requests in [docs/TAMPER.md](docs/TAMPER.md), plain CI passed 56.
- **Knos inherits GitHub's failures.** If GitHub signs something false or is down, the programs believe it or wait.
- **One person can change the second deployment, after 48 hours,** until the outside review.
- **A bounty has no early exit.** Its money leaves on a signed token or at its deadline. Each bounty is capped at 500 USDC
  until the review.
- **Nothing had been bought by an outsider when this was written.** By 3 Oct 2026 no outside repository had funded
  a task, and every payment until then was Knos's own account paying itself to prove the path. The site's Numbers
  page shows today's counts.

## History

Knos 0.1 (1–7 Sep 2026, before this hackathon) was shared memory for coding agents built on Sibyl, and it won the
Sibyl Labs hackathon. 0.2 and 0.3.0–0.3.9 (29 Sep – 2 Oct) were built during this one and tried coordination,
budgets and a jobs market before the measurement above showed where the problem was. Those are kept in
[drexthealpha/knos-labs](https://github.com/drexthealpha/knos-labs). 0.3.10 and 0.3.11 were the first deployment;
0.3.12 is the second. [docs/DISCLOSURE.md](docs/DISCLOSURE.md) says what was built when, what is older than the
hackathon, and what came from elsewhere; [CHANGELOG.md](CHANGELOG.md) is the dated record.

MIT, all of it. Built by drexthealpha. Its memory engine is [Sibyl](https://sibyllabs.org).
<!-- mcp-name: io.github.drexthealpha/knos -->
