# Demo (under three minutes, all screen)

One buyer, one task, a refusal, a payment, a second task; then how it works and what is not proven. Every scene is
to be a screen recording of the real thing on Solana devnet, in test USDC, with nothing staged, so that each
transaction shown can be found afterwards on the site's Numbers page (https://drexthealpha.github.io/Knos/#network),
which is read from the programs' own logs. A number that only the release run can give is a slot, `[[stat: name]]`.

Caption under the first scene, no narration: "Built during the hackathon: everything shown. Older work is listed in
docs/DISCLOSURE.md."

## 1. A buyer funds a task, with terms (0:00, 25 seconds)

**On screen.** A GitHub issue in the buyer's repository. She types `/knos fund 20 checks: test` and sends it. Knos's
reply appears under it: the amount, the terms in plain sentences, the deadline. Then the site: the bounty, open,
with its terms and the funding transaction.

**Narration.** This maintainer wants one issue fixed and will pay for it. One comment funds it, and names the check
that must pass. The reply says what she has bought: paid when a pull request that closes this issue is merged, if
`test` passed at its last commit. Those terms are now on chain, and nothing in the program changes them.

## 2. A submission that fails the terms is refused (0:25, 25 seconds)

**On screen.** A pull request for the issue whose check `test` is red. She merges it anyway. The workflow runs on
the push. Knos's comment on the pull request: not paid, and the reason, the required check `test` failed at this
commit. On the site the bounty is still open.

**Narration.** Here is a pull request that does not meet the terms. Its test failed. She merges it all the same,
and nothing is paid. The comment says exactly why: the check she named failed at the merged commit. The money has
not moved, and the bounty is still open for a pull request that passes.

## 3. A valid one is merged, and paid (0:50, 37 seconds)

**On screen.** A second pull request for the same issue: `test` is green. Its author has commented
`/knos address <address>`. She merges it. Knos's comment follows with the payment's transaction. Then the explorer:
that transaction, the transfer to the author's wallet and the fee. Then the token GitHub signed, decoded:
`repository_id`, `job_workflow_ref`, `job_workflow_sha`, `event_name`, `run_attempt`, and the `aud` line that names
the repository, the issue, the payee, the head commit and the hash of the terms.

**Narration.** This one passes. She merges it, and that is the last thing anyone does. Her repository's workflow
reads GitHub's record of the merged commit and asks GitHub to sign what it found. A Solana program checks that
signature and pays the author's wallet: 110 seconds from merge to paid, at the
median. This is the token. GitHub signed which repository it ran in, which workflow file at which commit, and what
it asked for. The program checked each of those against the bounty.

## 4. The same buyer funds the next task (1:27, 14 seconds)

**On screen.** Another issue in the same repository. She types `/knos fund 20`. Knos's reply. The site: two bounties
from this repository, one paid, one open.

**Narration.** And she does it again. Same comment, next issue. A buyer who comes back is the number this product
lives on, and on devnet it cannot be measured in real money yet.

## 5. How it works (1:41, 43 seconds)

**On screen.** The explorer: the two transactions that verify the token, with their compute units. Then
`programs-v2/knos_oidc/src/pins.rs`: GitHub's four key hashes, the rotate workflow's commit, the account and the two
repositories an attestation must come from. Then the bounty's account next to the funding transaction's log line
`knos2:terms`, and the same hash inside the token's `aud`.

**Narration.** Three choices carry this. First, GitHub's RSA signature is verified on chain, in two transactions,
because one cannot hold the arithmetic. Second, the keys. The program starts from GitHub's four keys. A new key
needs GitHub's own signature, from a workflow at a fixed commit run in my own repositories. Then it waits a day and
needs a guardian's approval. Every key expires after 30 days unless attested again, and the guardian can revoke
one. The guardian cannot add a key or move money. Third, the terms. Their hash is stored at funding, and a pay
token that carries another hash pays nothing.

## 6. What is not proven (2:24, 31 seconds)

**On screen.** `knos mainnet-check` in a terminal: every gate with its evidence, and the one that fails, no outside
review. Then docs/SECURITY.md, "Known limits".

**Narration.** What this does not show. It is devnet and test money. No outside review has been done, and until one
is, I can change these programs, through a multisig, after a public 48-hour delay. No outside buyer has funded a
task. And GitHub signs that the workflow ran, not what it read: what that signature backs is only as honest as the
funder's own repository, which is why it can only ever move that funder's money.
