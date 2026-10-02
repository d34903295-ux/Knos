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

In merge mode the token exists only because a maintainer merged the pull request. In tests mode it exists only
because the funder's acceptance checks, whose hash is fixed on chain, failed on the base and passed on the pull
request, in a job that holds no token; a second job that runs no pull request code then asks for the token.

## What a proof does not prove

- **That the merge was wise.** Merge mode pays for what maintainers merge. That is the point: the decision stays
  with the people who own the code.
- **That the pull request should get this bounty.** A pull request's own description says which issue it closes.
  So a payment is held (an hour by default in merge mode, a day in tests mode) and `/knos veto` from a maintainer
  takes it back. A funder who sets `review 0` gives that up.
- **That tests mean the work is good.** Tests can be gamed. Of 21 cheating pull requests in
  [TAMPER.md](TAMPER.md), an in-process test run is fooled by 2, both for reasons no test runner can fix: a stub
  that returns the expected constants, and code that stops the acceptance tests' bodies from running, from inside
  the process that runs them. The black-box runner is fooled by neither, because the pull request's code is never in
  the process that decides. For in-process tests the defence is the review window.
- **That GitHub is honest or uncompromised.** A leaked GitHub signing key could forge any proof until GitHub
  rotated it.

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

## Tokens posted in public

A repository's workflow posts each token as a comment, so that anyone can relay it. That is safe because a token is
not a bearer credential here: its audience names one action (this issue, this amount; or this issue, this author,
this commit; or this address), the programs accept it for at most an hour past its expiry, and each instruction has
a replay guard (a pay token must be newer than the funding and the last veto; a repository's fund tokens are
accepted only in the order GitHub issued them; a veto must be newer than the proof it vetoes). Whoever relays a token
first only pays the gas.

## The Stop hook

`knos init` adds a Stop hook to Claude Code and Codex. When the agent's last message claims tests pass, CI is green,
a release or a bare "done", Knos runs that check itself and blocks the stop if it fails.

- It is a local aid. A person can remove it, and an agent run without hooks does not have it. What an agent cannot
  remove is the same check on GitHub: `prove.yml`'s `check` job, which a branch ruleset can require.
- After 3 blocks on unchanged evidence it lets the stop through with a warning that names what is unproven, so an
  agent cannot be trapped by a check it cannot fix.
- A network check that cannot reach the network fails; it does not pass. An error inside Knos allows the stop.
- What it learns is kept in Sibyl's store on your machine and nowhere else ([`src/knos/store.py`](../src/knos/store.py)).
  Knos does not route around Sibyl's free-tier cap. Delete `~/.sibyl-memory/memory.db` and the memory is gone.

## Before mainnet

`knos mainnet-check` prints each gate with its evidence: both programs immutable, on-chain bytes equal to the verified
build, security.txt present, the rotate pin in the binary and on GitHub, every key the issuers publish today
accepted, the program checks green, and an outside audit published. The last one fails today. The released binaries
are devnet builds (they include a test-USDC faucet that a real-money build refuses), each job is capped at 500 USDC,
and mainnet stays locked until an audit exists.
