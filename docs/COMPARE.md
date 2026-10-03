# Knos compared

What each alternative does, read from its own code, pages and chain on 2 Oct 2026. The Knos row is the second
deployment (`programs-v2`). Knos is on Solana devnet and its money is test USDC; several of the others move real
money today. Where another product is ahead, this page says so.

## Paying for a merged pull request

| | who holds the money | what releases it | what is verified, and by whom | time from merge to paid | fee | who can change the rules | chain |
|---|---|---|---|---|---|---|---|
| **GitHub's own controls** (rulesets, required checks, required reviews, merge queue, the February and June 2026 pull request limits, Copilot code review) | Nobody. There is no money in them. | Nothing. They gate a merge, not a payment. | GitHub checks, before a merge, what the repository requires: named status checks (from a named app if set), approving reviews, code-owner review. A merge queue keeps those checks satisfied. Copilot code review can count as an approval when an admin turns that on (preview, off by default). | Not applicable. | None. | Repository and organisation admins. They set the rules and decide who may bypass them. | None. |
| **MergePay** | A contract on Arc. Anyone funds an issue from a wallet (`fund(...)`, native USDC). | A GitHub-signed token from its pinned workflow, relayed to `award()`. The award then waits for the payee to link a wallet; unlinked after 180 days, it goes back to the funders. | On chain, by the contract: GitHub's RSA signature, the issuer, expiry, `event_name`, the audience, `repository_id` and the workflow's ref. That the pull request was merged is decided by the workflow's own `if:`, not by a signed claim. There is no test or check condition. | 9 seconds on its one award (merged 07:56:18, awarded 07:56:27 UTC, 24 Sep 2026). Its site says 4.5 seconds. | No platform fee. A relayer fee the funder sets: 0.03 USDC by default, 1 USDC at most. | One key. `owner()` is the relayer's hot wallet, the key its relay service signs with (read on chain, 2 Oct 2026). It can propose a signing key, active after 3 days (funders may refund while one is pending), revoke a key at once, and transfer ownership. It cannot take escrowed money. A multisig owner is an open item on its roadmap. | Arc mainnet (chain id 5042) since 24 Sep 2026. Real USDC. |
| **Bounty boards** (Algora, Opire, BountyHub, TaskBounty) | Algora: its pages do not say the money is held before the work; the funder pays through Stripe when rewarding. Opire: nobody; the creator pays the developer through Stripe after accepting. BountyHub: the board, when the creator pays in advance. TaskBounty: not stated. | A person. Algora: the maintainer clicks Reward, or sets up auto-pay on merge. Opire and BountyHub: the bounty's creator decides whether the pull request meets the issue. TaskBounty: a run of the repository's tests and a new regression test in its own sandbox. | Nothing is signed by a third party. On Algora, Opire and BountyHub a person judges. TaskBounty runs tests in a sandbox it operates. | Algora: payout typically 1 to 3 business days after the payment. TaskBounty: 1 to 2 business days by bank, at once in USDC. Opire and BountyHub: not published. | Algora 9%. Opire 4% plus Stripe's fees. BountyHub 10%. TaskBounty 20%. | The board, through its terms, and the person paying. | None: Stripe, and PayPal on BountyHub. TaskBounty can pay out USDC on Base or Solana. |
| **Knos** (second deployment) | A Solana program (`knos-pay`). A job's money sits in the program's vault. A repository owner's prefunded balance sits in a token account the program controls; only the wallet that opened it can withdraw it. | A GitHub-signed token from the workflow commit fixed at funding. The author is paid in the transaction that verifies the proof. No veto and no review window after the merge. With no proof by the deadline the money goes back, and that needs no token. | On chain: GitHub's RSA signature (`knos-oidc`), then the repository id, the workflow file and its commit, the issue, the payee's GitHub id, the head commit and the hash of the terms fixed at funding (`knos-pay`). In that workflow, on a GitHub-hosted runner in the repository the job names: that the pull request was merged, that every check named in the terms concluded `success` at its head commit, and that its changed files are inside the paths the terms allow. GitHub signs that this workflow ran there; it does not sign what the workflow read. | No waiting period: paid in the transaction that verifies the proof. Before it come one GitHub Actions job and the relay, which reports how long it took; that time is measured, not promised ([BENCH.md](BENCH.md)). | 2.5%, at least 0.05 USDC, only when someone is paid. Nothing on a refund. | Knos, only through a multisig with a public 48-hour delay, until an outside review; then the programs are made immutable. A second multisig, the guardian, can approve or revoke a signing key and pause new funding for at most 7 days. It cannot add a key or move money. No instruction changes a job's terms after funding. | Solana devnet. Test USDC. |

## Where the others are ahead

- **MergePay** is on a mainnet and moves real USDC, since 24 Sep 2026. It charges no platform fee. Its one award
  landed 9 seconds after the merge. A new signing key waits 3 days there, and funders may take their money back
  while a key change is pending; on Knos the wait is 1 day and there is no early refund. It also came before
  Knos: it verified GitHub's signature on a chain a week before Knos did so on Solana
  ([DISCLOSURE.md](DISCLOSURE.md)).
- **Bounty boards** pay ordinary money to bank accounts. Algora's pricing page says 120 countries and regions, and
  its home page says 100+ customers. Nobody on a board needs a wallet.
- **GitHub's own controls** are free, built in and already trusted by every repository that uses them. There is
  nothing to install.

## Where Knos differs

- **Checks are part of the payment condition.** The checks the funder named must have concluded `success` at the
  head commit of the merged pull request, and its changed files must stay inside the paths the funder allowed.
  Both are fixed at funding: the terms are logged on chain and their hash is stored with the job. MergePay's
  condition is the merge alone. On the boards a person judges.
- **The workflow is pinned by commit.** The program compares `job_workflow_sha`. MergePay's contract compares the
  workflow's ref, and its default ref is a tag (`@refs/tags/v1`, its `/api/config`).
- **No `pull_request_target`.** From 2 Nov 2026 GitHub blocks workflows triggered by `pull_request_target` in public
  repositories unless an admin adds a policy that allows it
  ([GitHub docs](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)).
  MergePay's contract accepts an award only from that event (`EVENT_MERGE` in `MergePay.sol`). Knos reads a
  merge from the `push` the merge makes.
- **Signing keys.** A new key needs GitHub's own signature, from a workflow at a fixed commit run in one of two
  repositories Knos owns. It then waits a day and needs the guardian's approval. Every key expires 30 days after
  it was last attested, and the guardian can revoke one. MergePay's owner proposes a key, and its keys do not
  expire.
- **Funding by comment.** A repository owner tops up a balance from a wallet once; after that one comment funds an
  issue. On MergePay each funding is a wallet transaction.
- **Solana, and GitLab.** `knos-oidc` also verifies GitLab CI tokens, and any Solana program can read a verified
  token ([OIDC.md](OIDC.md)).

Both records are small. MergePay's feed shows 4 bounties funded (2.25 USDC) and one award (0.47 USDC, to its own
author, on its own repository), read 2 Oct 2026. Knos's first deployment shows 11 bounties funded and 6 paid, every
payment Knos's own account paying itself in test money (measured with `scripts/network_stats.py` on devnet,
2 Oct 2026, 19:56 UTC).

## Where Knos is behind

- It is on devnet. No real money has moved.
- There has been no outside review. Until there is one, Knos can change the second deployment through its
  multisig, after a public 48-hour delay.
- It charges 2.5%. MergePay charges no platform fee.
- No merge-to-paid time for the second deployment is published here. MergePay has shown 9 seconds. On the first
  deployment the median from the funding transaction to the payment, over its 6 payments, was 159 seconds (same
  measurement); [BENCH.md](BENCH.md) says where that time goes.
- A refund waits for the deadline.
- The payee needs a Solana address. The boards pay bank accounts.
- No outside repository has funded a task through it.

## What GitHub's own controls can and cannot do

They can block a merge:

- A ruleset can require named status checks, and name the app that must report them. It can require a number of
  approvals, a code owner's approval, and an approval from someone other than the last person to push
  ([available rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)).
- A merge queue keeps required checks satisfied on a busy branch. It is available in public repositories owned by
  an organisation, and in private ones on GitHub Enterprise Cloud
  ([merge queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)).
- Since [13 Feb 2026](https://github.blog/changelog/2026-02-13-new-repository-settings-for-configuring-pull-request-access/)
  a repository can turn pull requests off, or accept them from collaborators only. Since
  [17 Jun 2026](https://github.blog/changelog/2026-06-17-limit-open-pull-requests-for-users-without-write-access/)
  it can cap the open pull requests of each user without write access. Drafts do not count.
- Since [1 Sep 2026](https://github.blog/changelog/2026-09-01-copilot-code-review-can-now-approve-pull-requests/)
  Copilot code review can approve a pull request, and that approval counts toward the required number. It is a
  public preview and off by default.

None of them moves money. None makes a payment conditional on a merge, and none lets a stranger count on being
paid. The request for a bounty on issues has been open since 6 Jul 2021
([community discussion 4517](https://github.com/orgs/community/discussions/4517)). GitHub Sponsors pays a person
or an organisation, once or monthly, and is not tied to an issue or a merge
([GitHub Sponsors](https://docs.github.com/en/sponsors/getting-started-with-github-sponsors/about-github-sponsors)).

Knos uses these controls; it does not replace them. When a funder names no checks, a job's terms take the default
branch's required status checks. The free Knos check is one more status a ruleset can require.

## The buyer's real question

Do I need an escrow at all?

When better review or better CI is all a team needs, no. GitHub's required checks and the free Knos check are
enough, and the escrow adds nothing. A team that pays its own engineers already has a contract and a payroll; a
merge does not need to move money.

The escrow matters only when the person doing the work is not someone the buyer already pays: an outside
contributor, a stranger with an agent, a contractor with no contract. That person asks whether the money exists
and whether it can be taken back after the work is accepted. The buyer asks what exactly is being bought. The
escrow answers the first: the money is committed before the work and cannot be vetoed after the merge. The terms
answer the second: these checks, these paths, this deadline, fixed when the money went in.

## Other programs that verify GitHub's signature on a chain

On Solana we found none. Searches of crates.io on 2 Oct 2026 for `solana rs256` and `zklogin solana` returned no
crate, and web searches found no program that verifies an RS256, JWT or OIDC token on chain.

Elsewhere: MergePay (above) on Arc. `github-zktls` checks a Sigstore attestation of a GitHub Actions run in zero
knowledge on Base Sepolia; its README carries a notice dated 24 Aug 2026 that the construction "does not provide
the security it claims" ([repository](https://github.com/amiller/github-zktls)). Juno verifies GitHub Actions tokens
inside an Internet Computer canister, to authorise deployments, not payments
([write-up, 23 Feb 2026](https://daviddalbusco.com/blog/building-a-github-actions-integration-with-oidc-authentication)).

## Sources

All read on 2 Oct 2026.

- MergePay: [repository](https://github.com/codeswithroh/mergepay) (`contracts/src/MergePay.sol`,
  `.github/workflows/award.yml`, README; first commit 23 Sep 2026, "Deploy to Arc mainnet" 24 Sep 2026);
  [site](https://mergepay.fun); its live [feed](https://mergepay.codeswithroh.workers.dev/api/feed) and
  [config](https://mergepay.codeswithroh.workers.dev/api/config); `owner()` read from `https://rpc.mainnet.arc.io`
  for contract `0xcff79B144833b36ca53b310C1Ad7854AF9Ff9EeD`.
- Algora: [pricing](https://algora.io/pricing), [home page](https://algora.io),
  [payments](https://algora.io/docs/payments), [bounty workflow](https://docs.algora.io/bounties/workflow). On
  2 Oct 2026 the pricing, payments and workflow pages loaded for us through one route and returned "page not found"
  through two others; the home page loaded every time and now leads with hiring. Treat Algora's figures as its
  last published ones.
- Opire: [terms of service](https://opire.dev/terms-of-service), last updated 24 Aug 2024.
- BountyHub: [bountyhub.dev](https://www.bountyhub.dev/).
- TaskBounty: [for agents](https://www.task-bounty.com/for-agents).
- Knos: this repository; the second deployment's addresses are in `programs-v2/program_ids.json`.
