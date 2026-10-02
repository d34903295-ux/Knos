# Security model

AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed. This page says who has
to be trusted for what, what a proof proves, and what it does not. Report a vulnerability privately through GitHub's
security advisories on this repository.

## Who you trust, and for what

| party | trusted for | not trusted for |
|---|---|---|
| **GitHub** | signing true statements about what happened in a repository (who opened a pull request, that it was merged, which workflow ran at which commit), and keeping its signing keys | nothing else. GitHub cannot move money; it can only sign statements the programs then check |
| **Solana** | running the two programs as written | |
| **The repository's maintainers** | deciding what gets merged, and so who is paid, in their repository. This includes a bounty someone else added to one of their issues | they cannot take a funder's money for themselves except by merging their own pull request, which is public |
| **Knos (drexthealpha)** | nothing in the path of a payment | Knos cannot register a key, change a program, pause, redirect a payment, or raise the fee. Its only on-chain role is to receive the fee |
| **A relayer** (Knos's public worker, or anyone) | liveness only: carrying tokens to the chain and paying gas | it cannot change where money goes. If Knos's worker stops, anyone can run `knos relay` |

## Why nobody can forge a proof

**There is no admin.** `knos-oidc` and `knos-pay` have no instruction that only some key may call.

**There is no upgrade authority.** Both programs are deployed and then made immutable (`solana program
set-upgrade-authority --final`). `knos mainnet-check` and the site's Numbers page read the program-data accounts and
show whether an upgrade authority exists. What is on chain is this repository's verified build
(`solana-verify`, in `program.yml`).

**The issuers' keys are not supplied by anyone.** The sha256 of every RSA key GitHub Actions and GitLab published
on 2 Oct 2026 is a constant in the binary (`programs/knos_oidc/src/pins.rs`; `scripts/oidc_pins.py` recomputes them
from the issuers' own URLs). A key account can be created only for a modulus whose hash is one of those constants.

**A new key enters only on GitHub's own signature.** When an issuer adds a key, a workflow in
`drexthealpha/knos-oidc-rotate` reads the issuers' key sets on a GitHub-hosted runner and asks GitHub for a token
whose audience names the new key's hash. `RegisterKey` accepts that token only if GitHub signed it with a key the
program already trusts, and only if it came from that workflow file at the one commit fixed in the binary. A commit
sha fixes the file's content, so not even the owner of that repository can change what the workflow does: a changed
file is another commit, which the program refuses.

What this leaves: if GitHub stopped using every key the program knows before the rotate workflow ran, or GitHub
changed how runs of that workflow are identified, no new key could be added and the program would stop accepting
tokens. Money already in escrow would then be refundable at each job's deadline, and nothing could be forged. That
is the price of having no admin, and it is the right way round.

## What a proof proves

A payment needs a token that `knos-oidc` verified (GitHub's RS256 signature over the exact bytes, by a trusted key,
not more than an hour past its expiry) and whose claims `knos-pay` then checks:

- it came from a **GitHub-hosted runner**;
- it came from **`prove.yml` in the repository and at the commit the bounty pinned** when it was funded
  (`job_workflow_ref`, `job_workflow_sha`). The judge that workflow runs is the code at that same commit; the calling
  repository cannot substitute another;
- it is about **this repository** (`repository_id`), **this issue**, **this pull request head commit**, and names
  the **GitHub account to pay**, all in the audience GitHub signed;
- it was issued **after the bounty was funded**, and after the last veto.

In merge mode the token exists only because a maintainer merged the pull request **and** the Knos check held at the
merged commit: the repository's rules, what its history requires, and, when the description says tests pass or CI
is green, GitHub's record of that commit bearing it out. At the merge that check is strict: a failed check, checks
still running, or a record GitHub would not return all mint nothing, and the job can be run again when the record
is complete. A pull request that claims nothing is paid on the merge and the rules alone; one whose claim was
removed before the merge made no claim. In tests mode it exists
only because the funder's acceptance checks, whose hash is fixed on chain, failed on the base and passed on the pull
request, in a job that holds no token; a second job that runs no pull request code then asks for the token.

**Who is paid** is in the audience GitHub signs: the pull request's author. For a pull request an agent opened under
a bot account, it is the person who ran the agent, found the way that agent names them (the pull request's assignee,
a `Requested by: @login` or `Knos-Pay-To: @login` line, or the co-author of the head commit), and worked out in the
job that mints the token, from GitHub's event, never taken from a job that ran pull request code. An issue that is
assigned to someone pays only that person's pull request.

## What a proof does not prove

- **That the work is good.** GitHub's signature proves what happened (this pull request, by this account, was
  merged; these checks passed at this commit). It does not prove the code is correct or useful. In merge mode that
  judgment is the maintainer's, which is the point: the decision stays with the people who own the code. In tests
  mode it is the funder's acceptance checks, and tests can be gamed.
- **That the pull request should get this bounty.** A pull request's own description says which issue it closes.
  So a payment is held (an hour by default in merge mode, a day in tests mode) and `/knos veto` from a maintainer
  takes it back. A funder who sets `review 0` gives that up. This is the same shape as a letter of credit under
  UCP 600: a fixed window to examine the documents, payment on silence, and a narrow exception.
- **That tests mean the work is good.** Of 21 cheating pull requests in [TAMPER.md](TAMPER.md), an in-process test
  run is fooled by 2, both for reasons no test runner can fix: a stub that returns the expected constants, and code
  that stops the acceptance tests' bodies from running, from inside the process that runs them. The black-box
  runner is fooled by neither, because the pull request's code is never in the process that decides. That benchmark
  is one small Python repository and 21 attacks; it bounds nothing beyond itself. For in-process tests the defence
  is the review window.
- **That GitHub is honest or uncompromised.** See the next section.

## Known limits of this version

These are properties of the two programs as deployed. They cannot be patched, because nothing can.

1. **No key revocation.** A key the program trusts stays trusted for ever. If one of GitHub's signing keys leaked,
   even after GitHub retired it, its holder could forge any token and take every open escrow. GitHub publishes no
   rotation policy and we found no record of such a leak; the exposure is every bounty open at that moment (each
   capped at 500 USDC on devnet builds).
2. **Rotation depends on timing.** A new key is admitted only by a token signed with a key already trusted. If GitHub
   retired every trusted key before the rotate workflow ran (it runs every 6 hours in two repositories, and anyone
   can run it), the verifier would accept nothing from then on. Open bounties would still refund at their
   deadlines, which needs no token. Money already credited to a GitHub account but not yet claimed would be stuck,
   because a claim needs a token.
3. **GitLab's keys are admitted on GitHub's signature**, since the one pinned rotate workflow runs on GitHub.
4. **One pinned workflow, one runner.** A bounty pins `prove.yml` at one commit, the actions it uses are pinned
   to commits (a moved tag cannot change what runs: `tests/test_prove.py`), and the judge's Python dependencies are
   installed as they were published by the release date, never newer. What remains trusted is GitHub's hosted
   runner, PyPI's copies of those dependencies, and the pinned actions' code at those commits.
5. **Bugs are for ever.** An immutable program cannot be fixed. Auditors generally advise against freezing a
   program that has not been audited ([Neodyme](https://neodyme.io/en/blog/solana_upgrade_authority/)); these were
   frozen on devnet, where the money is test money, to make "nobody can change it" a fact that can be checked
   rather than a promise.
6. **Upgrading a repository's pin strands its open bounties.** A bounty is paid only from the workflow commit it
   pinned. A repository that moves to a newer Knos commit while a bounty is open waits for that bounty's refund and
   funds it again.
7. **Private repositories.** A token is posted as a comment for a relayer to find; in a private repository nobody
   outside can see it, so the repository must relay its own (`knos relay --token FILE`, with a funded key). What
   becomes public on chain either way: the repository's numeric id, the issue number, the amount, and the paid
   account's numeric id.
8. **Duplicate work.** Several people can open pull requests for one unassigned bounty; one is merged and paid.
   Assigning the issue reserves it for the assignee.
9. **Devnet money is free.** Funding by comment mints test USDC. With real money, the comment route cannot exist:
   a funder must sign a transfer from a wallet (the escrow already has that instruction, and the site's sponsor
   form uses it).

## What the mainnet version changes

A mainnet deployment is a new pair of programs, not these. The plan, in order:

1. **Keys expire unless re-attested.** A key is valid for a fixed number of days after the last attestation that it
   is still in the issuer's set, as Sui's zkLogin does with a window of about a day
   ([docs](https://docs.sui.io/concepts/cryptography/zklogin)). A leaked, retired key then dies by itself.
2. **Attestation by several independent parties**, not one workflow, and GitLab's keys attested from GitLab.
3. **An emergency path that cannot move money**: a multisig that can remove one key or pause new payments, and
   nothing else, as Aptos Keyless allows by governance
   ([AIP-67](https://github.com/aptos-foundation/AIPs/blob/main/aips/aip-67.md)).
4. **Upgradeable behind a multisig and a timelock through the audit and a stated bake period, then frozen**
   ([Solana operational security standard](https://publish.obsidian.md/sos/standard/wiki/upgrade-authority)).
5. **Funding from a wallet only**, a claim that needs no token when the claimer signs with a wallet already linked
   once, and escrows that name the verifier they trust, so users move to a new version by choice.
6. **An outside audit, published**, before any real money. One audit firm's public price list puts a program of this
   size at $7,000 to $20,000 and about a week ([Accretion](https://accretion.xyz/blog/solana-audit-cost)); the
   Solana audit subsidy programme run through Areta is the route we would apply to.

## The sandbox

In tests mode, everything that runs the pull request's code (its dependency install and its tests) runs as another
user (uid 65534) with an empty environment, and the tests run with no network. So that code cannot write the judge's
files, its memory, or the job's outputs, cannot read CI's variables, and cannot call out. `--sandbox require`, which
`prove.yml` passes, refuses to judge at all without it. `tests/test_judge_langs.py` runs a pull request that tries
all three.

The judge's memory (what it learned from earlier pull requests, in Sibyl's local store, kept in the repository's
Actions cache) is saved only after a refusal that ran no pull request code.

## The relayer cannot be made to burn money

Anyone can get GitHub to sign any audience from a workflow of their own. So the relay refuses, by reading the chain
and before paying anything, a token whose workflow is not the one a bounty pins, whose issue has no open bounty,
whose account has nothing due, or whose key the chain already has. What is left costs the relayer transaction fees
only; the token account's rent always comes back (`precheck` and `submit` in `src/knos/settle/relay.py`).

Each relayer uses a key of its own: a token's account on chain belongs to the key that carried it, so two relayers
never touch each other's work, and the second to arrive finds the action done and stops at a read. A failure that
says nothing about the token (the cluster dropped a transaction, or two runs shared one key) is tried again on the
next pass, up to 12 times.

## Tokens posted in public

A repository's workflow posts each token as a comment, so that anyone can relay it. That is safe because a token is
not a bearer credential here: its audience names one action (this issue, this amount; or this issue, this author,
this commit; or this address), the programs accept it for at most an hour past its expiry, and each instruction has
a replay guard (a pay token must be newer than the funding and the last veto; a repository's fund tokens are
accepted only in the order GitHub issued them; a veto must be newer than the proof it vetoes). Whoever relays a token
first only pays the gas.

## The Stop hook

`knos init` adds a Stop hook to Claude Code and Codex (and registers `knos mcp`, a read-only MCP server). When the agent's last message claims tests pass, CI is green,
a release or a bare "done", Knos runs that check itself and blocks the stop if it fails.

- It is a local aid. A person can remove it, and an agent run without hooks does not have it. What an agent cannot
  remove is the same check on GitHub: `prove.yml`'s `check` job (or `check.yml` alone), which a branch ruleset can
  require, and which a bounty's payment depends on.
- After 3 blocks on unchanged evidence it lets the stop through with a warning that names what is unproven, so an
  agent cannot be trapped by a check it cannot fix.
- A network check that cannot reach the network fails; it does not pass. An error inside Knos allows the stop.
- What it learns is memory, not authority: a bounty's acceptance checks are fixed by their hash on chain, and no
  payment depends on what a local store remembers. It is kept in Sibyl's store on your machine and nowhere else ([`src/knos/store.py`](../src/knos/store.py)).
  Knos does not route around Sibyl's free-tier cap. Delete `~/.sibyl-memory/memory.db` and the memory is gone.

## Before mainnet

`knos mainnet-check` prints each gate with its evidence: both programs immutable, on-chain bytes equal to the verified
build, security.txt present, the rotate pin in the binary and on GitHub, every key the issuers publish today
accepted, the program checks green, and an outside audit published. The last one fails today. The released binaries
are devnet builds (they include a test-USDC faucet that a real-money build refuses), each job is capped at 500 USDC,
and mainnet stays locked until an audit exists.
