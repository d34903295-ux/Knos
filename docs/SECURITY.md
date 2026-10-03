# Security model

Who has to be trusted for what, what a payment's signed statement covers, and what it does not. What is said about the programs and
the judge names the file or the test that backs it, so it can be checked.

This page describes the second deployment ([`programs-v2`](../programs-v2)), where every new bounty is funded. The
first deployment has [a section of its own](#8-versions-and-migration). Knos runs on Solana devnet, the money is
test USDC, and there has been no outside review. Report a vulnerability privately: [SECURITY.md](../SECURITY.md).

## The short version

- A bounty's money sits in a program, `knos-pay`. It is not with Knos and not with the repository.
- The program pays only on a token that GitHub signed and that a second program, `knos-oidc`, verified on chain.
- GitHub signs which workflow ran, where, and who started it. It does not sign what that workflow read. That the
  named checks passed, and who the author is, is the workflow's own reading of GitHub's record, on a runner of the
  funder's repository. So the signed statement is only as honest as the funder's own repository. That is enough to move the funder's
  own money, and it is never enough to move anyone else's. Every rule below follows from this.
- Until an outside review, Knos can change both programs through a multisig, after a public 48-hour delay. Today
  one person holds every key of that multisig.

## Who you trust, and for what

| party | trusted for | not trusted for |
|---|---|---|
| **GitHub** | signing true statements about a workflow run (the repository, the workflow file and its commit, the event, the account that started it); keeping its signing keys; answering its API truthfully | it cannot move money. It signs statements, and the programs check them |
| **Solana** | running the two programs as written | |
| **The funder's repository** (its maintainers, its workflow files, its runner) | everything a pay token says beyond GitHub's signature: that the pull request was merged, that the named checks passed, who the author is, which address to pay | anyone's money but the funder's. A pay token from one repository pays only bounties funded for that repository |
| **Knos** (drexthealpha) | until the outside review: not misusing the upgrade multisig; keeping the signing keys attested; approving new keys | it cannot add a signing key alone, pay or redirect a bounty, change a bounty's terms, or block a refund or a withdrawal. Changing the programs or the fee takes the 48-hour delay |
| **A relayer** (the job itself, Knos's public worker, or anyone) | being there: it carries tokens to the chain and pays the fees | it cannot change where money goes |
| **The issuer of the token that is paid** (a stablecoin's issuer) | what its mint lets it do to every holder, such as freezing an account | |

## 1. What GitHub signs, and what only the workflow derives

**What GitHub signs.** A workflow run can ask GitHub for a token. GitHub signs these claims in it, among others
(`github` in [`programs-v2/knos_pay/src/gh.rs`](../programs-v2/knos_pay/src/gh.rs) reads them):

| claim | what it says |
|---|---|
| `repository_id`, `repository_owner_id` | the repository the run happened in, and its owner, as numbers that survive a rename |
| `job_workflow_ref`, `job_workflow_sha` | the workflow file the job came from, and the commit that fixes that file's content |
| `event_name`, `actor_id` | what started the run, and which account did. For a comment, the account is the commenter |
| `run_attempt`, `run_number` | whether this is the run's first attempt, and the run's number |
| `runner_environment` | `github-hosted` or `self-hosted`. [Section 2](#2-why-the-runner-is-not-trusted-and-what-is-pinned-instead) says why this alone is not trusted |
| `iat`, `exp` | when the token was issued and when it expires (five minutes later) |
| `aud` | a text the workflow chose. GitHub signs that this run asked for it, not that it is true |

`knos-oidc` checks GitHub's RSA signature over the whole token on chain
([`programs-v2/knos_oidc/src/lib.rs`](../programs-v2/knos_oidc/src/lib.rs), instruction `Step`).

**What only the workflow derives.** Everything in `aud`. For a payment it is
`knos2:pay:<repository id>:<issue>:<payee's GitHub id>:<head commit>:<terms hash>:<mode>:<address>`. The workflow
found the merged pull request, read its checks from GitHub's API, worked out who is paid and where, and asked for
that text. GitHub's signature covers the asking. The reading happened on the runner.

**Why that is acceptable.** A runner belongs to the account that owns the repository. So each instruction of
`knos-pay` takes a token only where the repository it ran in is deciding about its own side's money:

| instruction | what GitHub signed, and the program checks | what the workflow derived | whose money moves |
|---|---|---|---|
| `FundBalance` | the run was in a repository of the balance's owner; a comment or a new issue started it; the commenter is the owner or a spender the balance lists; first attempt; the file is `fund.yml` | the issue, the amount (within the balance's cap per bounty), the terms, which balance to spend | the balance's. Its wallet opened it for that owner's repositories |
| `Pay` | the run was in the bounty's repository; the file is `prove.yml` of the workflow repository and commit the bounty recorded at funding; issued after the funding | that the terms were met, the payee, the address | that one bounty's. Its funder funded it for that repository |
| `Bind` | the file is the claim workflow at the one commit fixed in the program; the account that started the run owns the repository; the repository is named `knos-claim`; started by hand (`workflow_dispatch`), never by a push or a repository's description; first attempt | the address | none. It sets where that account's own payments go |

A wallet can also fund a bounty directly (`FundWallet`): it signs the transfer itself and names the workflow
repository and commit that may sign for it. No token is involved.

Two things follow, and both are limits ([the list](#known-limits)):

- **A balance trusts its owner's repositories.** Whoever can change the workflow files on the default branch of any
  repository of that owner can make the owner's next comment fund any issue there, up to the cap per bounty. Open a
  balance only for an owner whose repositories you control, and set a cap.
- **A sponsor trusts the repository.** Someone who funds an issue in a repository they do not control trusts that
  repository's maintainers and runner to say who earned it, as they trust them to merge honestly.

A wallet is bound by hand only: create a repository from the template `drexthealpha/knos-claim`, open its Actions
tab, choose "knos claim", press "Run workflow" and paste your address yourself (`knos claim <address>` does both).
The program takes only a `workflow_dispatch` token for it, and no link or repository description carries an address
in, because an address in a link could be someone else's.

The tests that hold the program to this: `test_a_comment_in_another_owners_repository_cannot_spend_a_balance`,
`test_only_the_owner_and_the_listed_spenders_spend_a_balance`, `test_a_cap_per_job_is_enforced`,
`test_a_fund_token_spends_only_the_balance_it_names`, `test_a_job_is_not_paid_with` and
`test_nobody_binds_a_wallet_for_someone_else` in [`tests/test_pay2_chain.py`](../tests/test_pay2_chain.py).

## 2. Why the runner is not trusted, and what is pinned instead

**What `runner_environment` does not say.** In August 2026 a researcher showed that an organisation on a paid plan
can create a GitHub-hosted "larger runner" with an image of its own, give it the name of a standard runner label,
and have it serve jobs pinned to that label. The token still says `github-hosted`. Nothing GitHub signs names the
runner, its group or its image
([research note](https://github.com/amiller/github-zktls/blob/master/tasks/substitution-demo/research-note.md),
runs of 21–24 Aug 2026; [GitHub community discussion 205732](https://github.com/orgs/community/discussions/205732),
with no answer from GitHub's staff when read on 2 Oct 2026). GitHub's documentation adds that a reusable workflow's
runner is chosen in the caller's account
([reusable workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/reusable-workflows)).
So pinning a workflow file and its commit fixes the code that was asked to run. It does not fix the machine.

**What is pinned instead.** The account. Every rule of the two programs names who ran the workflow, in claims an
outsider cannot get GitHub to sign:

| pinned | by | why |
|---|---|---|
| the repository (`repository_id`) | `Pay`; the key attestation | a pay token counts only from the bounty's own repository; an attestation only from two named repositories |
| its owner (`repository_owner_id`) | `FundBalance`, `Bind`; the key attestation | a balance is spent only from its owner's repositories; a wallet is bound only from the user's own |
| the workflow file and its commit (`job_workflow_ref`, `job_workflow_sha`) | `Pay`: the ones the bounty recorded at funding. `Bind` and the key attestation: the ones fixed in the program. `FundBalance` takes a `fund.yml` and records which repository and commit it came from | a commit fixes the file's content, so the owner of the workflow repository cannot change what runs under the pin |
| the event (`event_name`), and for a comment the commenter (`actor_id`) | `FundBalance`, `Bind`; the key attestation | a comment by a stranger, or a pull request, cannot start what moves money |
| the first attempt (`run_attempt`) | `FundBalance`, `Bind` | a re-run keeps the first actor's name whoever starts it |

`runner_environment` must still be `github-hosted`, as a necessary check and nothing more.

**Where that leaves the runner.** A repository's runner can still lie about what it read. With `github-hosted`
required, a personal account's repository runs on GitHub's own pool: personal accounts cannot have larger runners
([GitHub's documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners): they are for
organisations and enterprises on paid plans). For an organisation on a paid plan it can be an image the organisation
built. In both cases the runner decides only about its own repository's money, which is the rule of
[section 1](#1-what-github-signs-and-what-only-the-workflow-derives).

**What this changed.** The first deployment took the key attestation from any repository that called the rotate
workflow. So an organisation that got that job onto a runner of its own could have asked GitHub to sign a key of its
own choosing. We did not try this against the first deployment, and nothing we know of prevents it. The second
deployment pins who ran the workflow: one personal account and two of its repositories
([section 5](#5-signing-keys)).

## 3. The terms: what must be true, fixed at funding

A bounty's terms are a small JSON document ([`src/knos/terms.py`](../src/knos/terms.py)):

```json
{"accept":"","checks":[{"app":15368,"name":"test"}],"deny":[".github/**",".knos/**"],"mode":"merge","paths":[],"reserve":7,"v":1}
```

- `checks`: the checks that must have passed at the pull request's last commit, each with the GitHub App that must
  have produced it (`0`: a commit status of that name; `-1`: any source).
- `paths`: when not empty, every changed file must match one of these globs. `deny`: no changed file may match one
  of these. By default a pull request may not change `.github/**` or `.knos/**`, so it cannot edit what judges it.
- `accept`: in tests mode, the hash of the funder's black-box acceptance bundle in `.knos/acceptance/<issue>/`. Tests mode is offered only for a black-box bundle ([section 14](#14-the-sandbox-tests-mode)); everything else is funded in merge mode.
- `reserve`: how many days a `/knos take` reservation lasts.

**They cannot be edited.** The funding instruction carries the JSON, stores its sha256 in the bounty and logs the
JSON (`knos2:terms`), so the terms are public from the first transaction. A pay token must carry the same hash or
the program refuses it (`fund.rs` and `pay.rs` in [`programs-v2/knos_pay/src`](../programs-v2/knos_pay/src)). No
instruction edits a bounty.

**Where the checks come from**, decided once at funding (`build` in `terms.py`): the names the funder wrote
(`checks: test, build`); else the default branch's required status checks, from its rulesets and its branch
protection; else every check that ran to an end on the default branch's latest commit. Knos's own jobs are never
among them. A repository with no checks gets none, and the reply says so: "your merge alone is the acceptance".

**The six states of evidence.** When the pay token is asked for, each required check is in exactly one state at the pull request's
last commit (`evidence` in `terms.py`):

| state | meaning |
|---|---|
| `passed` | completed with `success`, from the app the terms name |
| `failed` | completed with failure, timed out, cancelled, action required, startup failure or stale |
| `skipped` | completed as skipped or neutral |
| `pending` | not completed |
| `absent` | no such check on this commit. One of the same name from another app is not it |
| `unreadable` | GitHub could not be read, or cut the listing short |

Only `passed` for every required check, with every changed file in scope, is acceptance (`accepted`). Anything else
pays nothing, and the comment on the pull request names the check and its state. What a pull request's description
says never changes what is required. [`tests/test_terms.py`](../tests/test_terms.py) covers each state.

This evidence is read by the workflow, on the runner. It is the part GitHub does not sign.

## 4. Who is paid, and where

Decided in [`src/knos/who.py`](../src/knos/who.py), from facts GitHub authenticates:

- A pull request a person opened pays that person: GitHub's `user.id` of the pull request.
- A pull request a bot account opened (a coding agent) pays a person only when GitHub authenticates an act of
  theirs or of a maintainer. In order: the issue is assigned to exactly one person, by a maintainer or by their own
  `/knos take`; else a maintainer wrote `/knos pay @login` on the pull request; else a person named in the pull
  request's assignees wrote `/knos mine` on it.
- An issue that is assigned pays only its assignee's pull request. A `/knos take` lasts the bounty's `reserve` days
  and then lapses.
- The pull request must close the issue: GitHub lists the issue among those it closes, or its description says
  `Fixes #N` and it is against the default branch ([`src/knos/closing.py`](../src/knos/closing.py)).

**Prose never decides.** A line in the description or in a commit ("Requested by", "Knos-Pay-To", a co-author) is
shown as a hint and pays nobody: an agent can be talked into writing a line. An edited comment never counts either,
because anyone with write access can edit anyone's comment and GitHub's API says only that the text changed. A
"maintainer" is whoever GitHub says can write to the repository now, not a label on a comment.

**Where it is paid**, in this order (`payout_address` in `who.py`; `pay` in
[`programs-v2/knos_pay/src/pay.rs`](../programs-v2/knos_pay/src/pay.rs)): the wallet the payee bound to their GitHub
account; else the address in their newest, unedited `/knos address` comment on the pull request; else nowhere yet.
The program itself looks up the bound wallet, so a pay token cannot route around it.

**A held payment.** A pay token that names no address, for a payee with no bound wallet, pays nobody yet. The bounty
becomes held for that payee (`HELD` in [`programs-v2/knos_pay/src/state.rs`](../programs-v2/knos_pay/src/state.rs)):

- It is theirs for 180 days. The funder cannot take it back in that time, and no other pay token can claim it.
- When they bind a wallet (by hand, or `knos claim <address>`), anyone can send `Settle`, and the bounty is paid to that wallet.
  No new pay token is needed.
- After 180 days with no wallet, `Refund` returns it to the funder.

`test_with_no_wallet_the_job_is_held_for_the_payee_and_paid_once_they_bind_one` and
`test_a_held_job_returns_to_the_funder_after_180_days_and_not_before`.

**Nothing is decided on half an answer.** At the merge, if GitHub did not return a fact the decision rests on,
nobody is paid until `/knos settle` is run again (`strict` in `who.payee`; [`tests/test_who.py`](../tests/test_who.py)).

**The fee.** 2.5% of the bounty, at least 0.05 USDC, taken in the paying instruction and sent to a token account of
`FEE_OWNER`, an address of Knos's that has no other power in the program (`fee_of` and `FEE_OWNER` in
[`programs-v2/knos_pay/src/lib.rs`](../programs-v2/knos_pay/src/lib.rs)). A refund pays no fee.

## 5. Signing keys

`knos-oidc` accepts a token only under a key it holds. How a key gets in, and how long it lives, is fixed in
[`programs-v2/knos_oidc/src/pins.rs`](../programs-v2/knos_oidc/src/pins.rs) and
[`lib.rs`](../programs-v2/knos_oidc/src/lib.rs).

**Genesis.** The program holds the sha256 of GitHub's four signing keys of 2 Oct 2026 as constants.
`python scripts/oidc_pins.py` prints the hashes of the keys GitHub publishes today, to compare. Anyone can register
one of those four keys, and no other key, without further attestation.

**Every other key needs GitHub's own signature** (the attested path). That means a key GitHub adds later, and every
GitLab key. A run of the rotate workflow reads the issuer's published keys and asks GitHub for a token whose `aud`
names the new key's hash. The program accepts that token only if all of these hold (`attested` in `lib.rs`):

| pinned | value |
|---|---|
| the workflow file | `drexthealpha/knos-oidc-rotate/.github/workflows/rotate.yml` |
| its commit | `6ddf031b64febecfcd510763ecee37637a8047fa` |
| the owner of the repository the run happened in | GitHub account id 142920951 (drexthealpha, a personal account) |
| that repository | id 1353152983 (drexthealpha/Knos) or 1401432540 (drexthealpha/knos-oidc-rotate) |
| what started the run | its schedule, or a person by hand |
| the runner | `github-hosted` |
| the token itself | verified under a key the program already accepts, and at most an hour past its expiry |

`test_every_condition_of_the_attestation_is_needed_to_register_a_key_and_to_refresh_one` in
[`tests/test_oidc2_chain.py`](../tests/test_oidc2_chain.py) removes each condition in turn.

**A one-day delay, then the guardian's approval.** A key admitted by an attestation verifies nothing for a day
(`KEY_DELAY`), so that anyone can see it on chain first, and nothing at all until the guardian approves it
(`test_an_attested_key_verifies_only_after_its_delay_and_the_guardians_approval`). An attestation alone admits no
key, and the guardian alone admits no key.

**Thirty days, unless attested again.** Every key, genesis keys included, expires 30 days after it was registered or
last attested (`KEY_TTL`). `Refresh` takes the same attestation and moves the expiry to 30 days from now, never
earlier. Anyone may send it. A key GitHub retires stops being attested and dies by itself.

**Revocation is for ever.** The guardian can revoke any key. No instruction clears the mark, and the key's account
stays, so the same key can never be registered again (`test_revoke_is_for_ever`).

**A revoked or expired key also stops the tokens it verified.** Every instruction of `knos-pay` that takes a token
also takes the key's account and asks the verifier's own rule whether that key is still good (`key_good` in `gh.rs`;
`test_a_token_is_refused_once_its_key_is_revoked` and
`test_a_token_is_refused_while_its_key_is_expired_and_works_again_once_the_key_is_refreshed`). So revoking a key
also stops every payment that depends on a token it signed: the bounty waits for a pay token under a good key, or goes
back to its funder at the deadline.

**When keys run out.** The attestation is itself verified under one of GitHub's keys. If no usable key is left
(nothing attested them for 30 days, or all were revoked), nothing can be verified and nothing new can be attested.
Then the escrow is refund-only ([section 10](#10-recovery-without-github-and-without-knos)). Nothing can be forged
in that state. Getting out of it takes an upgrade of the verifier
([section 7](#7-the-upgrade-authority)).

## 6. The guardian

The guardian is the vault of a Squads multisig with no time lock (`GUARDIAN` in `pins.rs` and in
[`programs-v2/knos_pay/src/lib.rs`](../programs-v2/knos_pay/src/lib.rs); addresses in
[`programs-v2/program_ids.json`](../programs-v2/program_ids.json)). It has no time lock so that a revocation is not
delayed.

| the guardian can | the guardian cannot |
|---|---|
| approve a key that an attestation admitted | add a key alone: that takes GitHub's signature |
| revoke any key, for ever. Tokens that key verified stop working at once, and so do the payments that depend on them | undo a revocation, or change a delay or an expiry |
| refuse new funding for at most 7 days at a time (`Pause`). A pause ends by itself; keeping one takes its signature again | move money, pay a bounty, or change where one is paid |
| | block a refund or a withdrawal, or a payment or a wallet binding whose token was verified under a key that is still good. None of these reads the pause |

So the guardian can stop the system, and it cannot take from it. By revoking every key, or by never approving a new
one, it can make the escrow refund-only. `test_only_the_guardian_approves_and_revokes_and_it_can_do_nothing_else`
and `test_the_guardian_pauses_new_funding_only_and_nobody_else_can` hold the programs to the table.

## 7. The upgrade authority

Both programs are upgradeable. Their upgrade authority is the vault of a second Squads multisig
(`upgrade_authority` and `upgrade_multisig` in `program_ids.json`):

- **A 48-hour time lock.** An upgrade can run 172,800 seconds after the vote that approved it, and not before. In
  that time the proposal and the new program's bytes are on chain for anyone to read, and the members can cancel it.
- **No config authority.** No single key can change the members, the threshold or the delay. Changing them is a
  proposal like any other, and waits the same 48 hours.
- **What an upgrade could do: anything**, including taking every vault. The delay is the protection.
- **Today every member key is the founder's.** So the delay, not the number of signers, is what protects users.
  Outside signers are not there yet.
- **On devnet the multisig program is itself upgradeable.** Squads v4 has no upgrade authority on mainnet. On devnet
  its program data names one, `HM5y4mz3Bt9JY9mr1hkyhnvqxSH4H2u2451j7Hc2dtvK`, which we take to be Squads' own key
  (read from both clusters on 3 Oct 2026). A devnet time lock is only as strong as that.

What the 48 hours are good for: a balance's wallet can withdraw its unspent money at once, at any time. Money
already in a bounty cannot leave early; it is paid on a pay token or refunded at its deadline. So the notice protects
balances, and bounties whose deadline falls inside it.

After an outside review the upgrade authority is removed, and the programs can no longer be changed.
`knos mainnet-check` reads all of this from the chain and prints it
([`src/knos/mainnet_check.py`](../src/knos/mainnet_check.py)).

## 8. Versions and migration

| | first deployment ([`programs`](../programs)) | second deployment ([`programs-v2`](../programs-v2)) |
|---|---|---|
| released as | 0.3.10 and 0.3.11 | 0.3.12 |
| can it be changed | no. Neither program has an upgrade authority (read from devnet, 3 Oct 2026) | yes, through the multisig, after 48 hours, until an outside review |
| new funding | none from this release's workflows | all of it |
| the payment condition | a merge, and what the description claims, checked at the merge by the workflow | the checks named at funding ([section 3](#3-the-terms-what-must-be-true-fixed-at-funding)) |
| after the pay token | merge mode: a review window (an hour by default) in which the funder could veto, and so take back a merged payment. Tests mode: a review window the funder set, up to 7 days | final at once |
| where the money goes | to the author's GitHub account, claimed later | to a wallet, in the paying instruction |
| signing keys | never expire, cannot be revoked, and an attestation counted from any repository | [section 5](#5-signing-keys) |

**Nothing migrates.** No account moves from one deployment to the other, and a token that funds, pays or binds on
one does nothing on the other: every address is derived from the program's own id, and the audiences differ
(`knos:` and `knos2:`). Only a key attestation reads the same on both, since it names a key and nothing else.

**How bounties on the first deployment finish.** Each recorded, at funding, the workflow commit that may sign for it.
A repository that still runs that workflow finishes them as before: a merge is attested, waits out its veto window,
and the author claims it. The first deployment's client and relay stay in this release
([`src/knos/settle/oidc.py`](../src/knos/settle/oidc.py), `pay.py`, `relay.py`), and the public worker still carries
`knos:` tokens. A bounty that gets no pay token is refunded at its deadline, which needs no workflow. The first
deployment's limits stay as long as its bounties do; they cannot be patched. The 0.3.11 version of this page lists
them.

**An upgrade of the second deployment** replaces the program's code at the same address, so every balance, bounty,
binding and record stays where it is. An upgrade that changed an account's layout would have to say how it reads
the old one; a balance and a binding carry a version byte for that.

**Mainnet** will be a third pair of program ids ([Before mainnet](#before-mainnet)).

## 9. When something goes wrong

| what happens | what the system does | what still works |
|---|---|---|
| One of GitHub's signing keys leaks | the guardian revokes it. Tokens it verified stop at once. A bounty waits for a pay token under a good key | everything else |
| GitHub is down, or changes its tokens | pay tokens wait. Nothing is paid | refunds at the deadline, withdrawals |
| No key is attested for 30 days | every key expires. The escrow is refund-only | refunds, withdrawals, held bounties whose payee has a bound wallet |
| A fault is found in a program | the guardian can refuse new funding, 7 days at a time. A fix is an upgrade: public, and 48 hours away | payments, refunds, withdrawals, bindings |
| The upgrade keys are stolen | a hostile upgrade is public for 48 hours before it can run. The members can cancel it | withdrawals of balances at once; bounties as above |
| Knos's public relay stops | tokens wait as comments | anyone can relay with `knos relay`; a repository with a relay key relays its own |
| Knos disappears | no attestation, no approval: the keys expire within 30 days | refunds and withdrawals, for ever. They need no token and no operator |

The three emergency tools, and their reach: a **pause** stops new funding and nothing else, for at most 7 days per
signature; a **revocation** stops one key and what it signed, for ever; an **upgrade** can change anything, 48 hours
after it is approved in public.

## 10. Recovery without GitHub, and without Knos

Four ways out need no token, so they need neither GitHub nor Knos. Anyone can send a refund or a settlement; a
withdrawal is signed by the balance's own wallet. None of them reads the pause:

- **Refund.** A bounty with no payment by its deadline goes back where it came from: to the balance it was funded
  from, or to a token account of the funding wallet (`refund` in `pay.rs`).
- **Withdraw.** The wallet that opened a balance takes its unspent money back, to itself, at any time (`withdraw`
  in `fund.rs`).
- **Settle to a bound wallet.** A held bounty is paid once its payee has a bound wallet (`settle` in `pay.rs`).
  Binding a wallet does need a token, so this path is for a payee who bound one before GitHub was lost.
- **The 180-day return.** A held bounty whose payee never binds a wallet goes back to its funder after 180 days.

`test_with_no_proof_by_the_deadline_the_money_goes_back_where_it_came_from`,
`test_unspent_money_goes_back_only_to_the_wallet_that_opened_the_balance` and the two tests of
[section 4](#4-who-is-paid-and-where).

## 11. Who can refuse a payment, and until when

- **Before the merge.** A maintainer who does not want a pull request to take the bounty writes `/knos reject` on
  it, with a reason. It counts when the writer can write to the repository, the comment is unedited, and it was
  written before the merge (`rejected` in `who.py`). The pull request can still be merged; it is then not paid.
  Deleting the comment undoes it. A maintainer can also simply not merge.
- **After the merge and the pay token: nobody.** The program has no veto and no review window. Its instructions are
  listed at the top of [`programs-v2/knos_pay/src/lib.rs`](../programs-v2/knos_pay/src/lib.rs), and none takes a
  payment back.
- **Nobody judges a dispute.** There is no arbiter, in the program or at Knos. The terms were public before the
  work, the merge is the acceptance, and the program reads only the token and the terms.
- **Tests mode pays with no human, and only for a black-box check.** Payment without a merge is offered only when
  the acceptance bundle is black-box: the submission runs as a separate process and only its output is compared.
  It is paid when that check passes. Nobody merges and nobody approves at that moment. The funder's decision was
  the check, and it was fixed by its hash before the work began.

**What a buyer can do to a seller, within the rules.** Never merge. Reject before merging, with or without a good
reason. Assign the issue to someone else before merging, so that this pull request is not the one the bounty pays.
Read the pull request, decline it, and write the same change: nothing stops that, and the merge button stays with
the buyer. Name a check that cannot pass, though the terms are public before anyone starts. Pin other workflows
than the published ones at funding, which a seller can read on chain before starting. And one thing after
accepting: the pay token is asked for by the buyer's own repository, so a buyer who removes the workflow file or turns
Actions off before merging takes the work and no pay token is ever made. The money then goes back at the deadline.
Nothing on chain can stop that, as nothing can stop a buyer from copying a pull request without merging it. What is
left to the seller is the public record: a merged pull request that closed a funded issue, and a bounty that was
refunded.

What a buyer cannot do: change the terms after funding, take the money out before the deadline, stop a payment once
its pay token exists, or take one back.

**What a seller can do to a buyer, within the rules.** Deliver work that passes the named checks and is merged, and
be paid, whatever the work is worth: the buyer chose the checks and pressed merge. In tests mode, pass the
black-box check without solving the problem: a stub that returns the expected constants passes any check made of
fixed examples, so the check must use generated inputs ([TAMPER.md](TAMPER.md) says what was measured). Reserve an issue
with `/knos take` and do nothing for the bounty's `reserve` days. What a seller cannot do: be paid without the
funded checks having passed at the merged commit, as read by the funder's own workflow, or without the merge.

## 12. Private repositories and organisations

**Nothing in public, with a relay key.** Without a key, a workflow posts its token as a comment and Knos's public
worker finds it there. In a private repository nobody outside can read that comment. So a private repository
relays its own: the job carries its tokens to Solana itself, with a Solana key kept in the repository's secrets
(`KNOS_RELAY_KEY`). Then nothing is posted as a comment and nothing is written to Knos's public relay log. The key
pays transaction fees. It never holds a bounty's money and decides nothing.

**What the chain still shows.** A token goes on chain whole, and a chain is public for ever. For a private
repository the chain then shows:

- the repository's name and its owner; the account that commented or merged; the branch and the commit; the
  workflow's path;
- the issue number, the amount, the payee's GitHub id, the address paid;
- the bounty's terms: the names of the required checks and the path globs.

It never shows code, the text of an issue or a pull request, or a check's output.

**Treasury control for an organisation.** An organisation's money is a balance for the organisation's GitHub id,
opened from the organisation's own wallet (`OpenBalance`, `SetBalance` and `Withdraw` in `fund.rs`):

- The wallet names up to four GitHub accounts that may spend the balance by comment, and a cap per bounty. Nobody
  comments as an organisation, so only those named accounts spend it.
- A comment by anyone else, or in a repository of another owner, funds nothing.
- Only that wallet changes the list or the cap, and only that wallet takes unspent money back. A refund returns to
  the balance, not to whoever commented.

## 13. The supply chain

What runs between a merge and a payment, and what pins it:

- **The workflow, by commit.** A bounty records the repository that holds the workflows (as a hash of its name) and
  their commit, from the token that funded it. `Pay` takes a pay token only from `prove.yml` of that repository at that
  commit (`pay.rs`). A commit fixes a file's content. The published workflows take no inputs, so a calling
  repository chooses when they run, never what they do.
- **A funder can pin other workflows than Knos's.** Nothing forces a repository to call the published ones: it
  decides about its own money. A contributor can see what a bounty pins: the hash and the commit are in its account
  on chain (`wf_repo`, `wf_sha` in `state.rs`). The published ones are in `drexthealpha/knos-workflows`.
- **The judge, as one exact release.** The published workflows install `knos` from PyPI as one version
  (`knos==0.3.12`), with dependencies no newer than a fixed date. PyPI does not let a published file be replaced.
  The dependencies are pinned by date, not by hash.
- **That release, behind a gate.** A release is published by one workflow, only after the whole test suite passed on
  the tagged commit, and PyPI accepts the upload on that run's own GitHub token
  ([`.github/workflows/release.yml`](../.github/workflows/release.yml), `tests/test_release_gate.py`).
- **Actions, by commit.** Every action in every workflow and example is named by a full commit, never a tag
  ([`scripts/action_pins.json`](../scripts/action_pins.json)).
- **The programs, reproducibly.** What is deployed is the `solana-verify` build of this repository, made in a pinned
  image ([`.github/workflows/program.yml`](../.github/workflows/program.yml)). To check it, build the same way and
  compare the hash with the program on chain:

  ```
  solana-verify build programs-v2 --library-name knos_pay --base-image solanafoundation/solana-verifiable-build:2.3.11
  solana-verify get-executable-hash programs-v2/target/deploy/knos_pay.so
  solana-verify get-program-hash -u devnet 5y7iWJ1VAMJjnnWbbdo2a2PsWJEwTExSNpzrvQSEnS8k
  ```

  and the same for `knos_oidc` (`FkwZdsYCmzicJMtHLTkPK76bYNVG4WNwkWJBiVWNtF3W`). `knos mainnet-check` makes the
  same comparison.
- **The runner.** It is assumed to be what the repository's owner chose
  ([section 2](#2-why-the-runner-is-not-trusted-and-what-is-pinned-instead)). That is why a runner decides only
  about its own repository's money.

## 14. The sandbox (tests mode)

Payment without a merge (tests mode) is offered only when the acceptance bundle is black-box: the submission runs as a
separate process and only its output is compared. Everything else is funded in merge mode. The reason is the
measured result in [TAMPER.md](TAMPER.md): of 63 cheating pull requests, plain CI passed 56, acceptance tests that
run in the same process as the code passed 7, and the black-box check passed none.

Everything that runs a pull request's code (its dependency install and its tests) runs as another user (uid 65534)
with an empty environment, and the tests run with no network. That code cannot write the judge's files or the job's
outputs, cannot read CI's variables, and cannot call out while the tests run. The job that runs it has no id-token
and no secret; a second job, which runs none of the pull request's code, asks for the token
([`src/knos/judge.py`](../src/knos/judge.py), `tests/test_judge_langs.py`).

What it assumes about the runner: a Linux machine where the job can become another user (`setpriv`) and take the
network away (`unshare`), and where user 65534 has no way to become the job's own user. That is a separation
between users on one machine, not a virtual machine. A flaw in the runner's kernel or image that lets one user
become another would let a pull request write its own verdict. The dependency install does have the network.

## 15. Tokens posted in public

Without a relay key, a workflow posts its token as a comment for the public relay. That is safe because a token is
not a secret here. Its `aud` names one action, and each instruction takes it once: a fund token only if it is newer
than the balance's last and from the run's first attempt; a pay token only while the bounty is open, and a bounty
closes when it is paid; a bind token only if it is newer than the last. Every token stops working an hour after its
expiry, and one dated more than five minutes ahead of the chain's clock is refused (`gh.rs`). Whoever relays a
token first only pays the fees.

## 16. What the check remembers

The check on pull requests keeps what it learned (a rule this repository's history made required) as comments in an
issue labelled `knos-memory`. Only comments that GitHub Actions itself wrote there, and that nobody edited, are read
([`src/knos/proof/memory.py`](../src/knos/proof/memory.py)). A run with a read-only token, such as the check on a
pull request from a fork, writes nothing. Closing the issue makes Knos forget. No payment depends on this memory: a
payment depends on the bounty's terms alone.

## 17. The local Stop hook

`knos init` adds a Stop hook to Claude Code and Codex and registers `knos mcp`, a read-only server. When the agent's
last message claims that tests pass, CI is green, a release or a bare "done", Knos runs that check itself and blocks
the stop if it fails.

- It is a local aid. A person can remove it, and an agent run without hooks does not have it.
- After 3 blocks on unchanged evidence it lets the stop through with a warning that names what is unproven.
- A network check that cannot reach the network fails. An error inside Knos allows the stop.
- What it learns stays in Sibyl's store on your machine ([`src/knos/store.py`](../src/knos/store.py)). Delete
  `~/.sibyl-memory/memory.db` and it is gone.

## Before mainnet

- **An outside review first.** `knos mainnet-check` prints every gate with its evidence: the bytes on chain are the
  verified build; the upgrade authority is the multisig's vault, with its 48-hour delay and no config authority;
  the guardian is the pinned multisig; GitHub's keys verify; and an outside review is published. The last one fails
  today, on purpose.
- **Different program ids.** Every address is derived from the program's id, so a token signed for devnet names
  nothing on a mainnet deployment with ids of its own. The devnet ids will not be reused.
- **No faucet.** The released programs are devnet builds. A build without the `devnet` feature has no faucet: money
  enters only from a wallet (`test_the_faucet_exists_once_and_the_real_money_build_has_none`).
- **The cap stays** at 500 per bounty until the review, and the upgrade authority is removed after it.

## Known limits

The first three cannot be removed. No system of this kind can remove them, and they come with the design.

1. **A signature authenticates a statement, not the truth.** GitHub signs that a workflow ran and what it asked for.
   It does not sign what the workflow read. A funder's repository that lies can pay whom it likes, from its own
   bounties only.
2. **Passing checks and a merge do not show that the work is good.** A check that passed shows that this check
   passed. Of the 63 cheating pull requests in [TAMPER.md](TAMPER.md), plain CI passed 56. A merge is one person's
   judgment. In tests mode nobody judges at all, which is why it is offered only for a black-box check: in-process
   tests were fooled by 7 of the 63, the black-box check by none.
3. **Knos inherits GitHub's failures.** If GitHub signs something false, or a key of its leaks, the programs believe
   it until the key is revoked or expires. If GitHub is down, pay tokens wait. If GitHub closes an account, its owner
   cannot bind a wallet.
4. **One person can change the programs, after 48 hours.** Until the outside review
   ([section 7](#7-the-upgrade-authority)).
5. **A bounty has no early exit.** Its money leaves on a pay token, or at its deadline (14 days by default, 90 at most).
   The 48-hour notice of an upgrade does not free it. Each bounty is capped at 500 until the review.
6. **The keys need Knos to stay alive.** They are attested from two repositories of one account, and a new key
   needs the guardian. If that stops for 30 days, the escrow becomes refund-only, and only an upgrade brings it
   back. Once the programs can no longer be changed, nothing would.
7. **The guardian can stop the system.** It cannot take from it ([section 6](#6-the-guardian)).
8. **A balance trusts its owner's repositories, and a sponsor trusts the repository**
   ([section 1](#1-what-github-signs-and-what-only-the-workflow-derives)).
9. **A maintainer can pay a friend.** Merging one's own or a friend's pull request for a sponsor's money is not
   stopped. The public record counts a payment apart only when the funder and the payee are the same account or
   wallet.
10. **A buyer can accept the work and withhold the pay token.** The pay token is asked for by the buyer's own repository. A
    buyer who removes the workflow before merging is never made to pay; the money returns at the deadline
    ([section 11](#11-who-can-refuse-a-payment-and-until-when)). What the chain enforces is narrower: the terms
    cannot change, and once a pay token exists nobody can stop or reverse the payment.
11. **Tests mode has no second look.** A pull request that passes the black-box check is paid, whether or not it
    solves the problem. That is why tests mode is offered only for a black-box check with generated inputs: in-process
    tests can be satisfied from inside. Every other bounty is funded in merge mode, which pays on a maintainer's merge.
12. **The paid token's issuer keeps its powers.** A mint with a freeze authority or a permanent delegate can freeze
    or take what is in escrow; the bounty then waits. The program refuses mints that could tax or block a payout
    (`token.rs`), and it cannot remove an issuer's powers.
13. **Everything in a token is public for ever** ([section 12](#12-private-repositories-and-organisations)).
14. **A payout address in a comment is only as safe as the GitHub account.** Whoever controls the payee's account
    can post an address or bind a wallet.
15. **Several people can do the same work.** One pull request is merged and paid. `/knos take` reserves an issue for
    one person for the bounty's `reserve` days, 7 by default.
16. **The judge's dependencies are pinned by date, not by hash** ([section 13](#13-the-supply-chain)).
17. **The sandbox is a user boundary, not a machine boundary** ([section 14](#14-the-sandbox-tests-mode)).
18. **GitLab is verified, not paid.** `knos-oidc` verifies GitLab CI tokens for other programs to use, once a
    GitLab key has been attested and approved. `knos-pay` takes GitHub's only.
19. **Devnet only.** The comment route is free there because a faucet in the program mints test USDC. A build
    without the faucet has no such route: real money enters only from a wallet. By 3 Oct 2026 no outside repository
    had funded a task.
