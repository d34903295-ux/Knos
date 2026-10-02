# Technical walkthrough (under three minutes)

Screen recording with narration. "The live run" at the end says where to see the run shown.

## 1. The whole flow, live on devnet (0:00)

1. On the site, paste an agent pull request: the verdict appears in about a second, with what the description claims
   and what GitHub's CI recorded.
2. "Protect this repo": GitHub opens with one workflow file prefilled. Commit it. No secret, no wallet, no app.
3. On an issue, comment `/knos bounty 20`. Within a minute the Knos comment answers with the escrow's address.
   Show it on the site: 20.00 USDC, open.
4. Open a pull request that says "Fixes #N". The `prove / check` status appears: the repo's rules, and whether its
   "tests pass" is true.
5. Merge it. The Knos comment: the bounty is held for the author's GitHub account.
6. On "Get paid", type the author's GitHub login: the amount is waiting. Run the claim workflow with an address.
   The balance arrives.

## 2. What happens on chain (1:10)

- A GitHub Actions token is about 2,100 bytes. `knos-oidc` takes it in 3 writes, then checks the RSA-2048 signature
  in Montgomery form in 2 transactions of under 1M compute units each. Solana's limit is 1.4M per transaction, which
  is why the arithmetic is split and why this fits at all.
- The token account is now a fact any program can read. `knos-pay` reads the claims GitHub signed: the repository,
  the workflow file and its commit, the pull request's author, the head commit. It checks them against the bounty
  and credits the author's GitHub id.
- Show the program-data accounts: no upgrade authority. Show `pins.rs`: GitHub's key hashes as constants, and the
  rotate workflow's commit.

## 3. Why these choices (2:00)

- **No admin, immutable.** A proof that an operator could forge is not a proof. The cost is that a bug cannot be
  patched, so it was tested accordingly, and mainnet waits for an audit.
- **Pay a GitHub id, claim later.** The worker needs nothing to start. The claim is a token only they can get
  GitHub to sign.
- **Merge is the default trigger, tests are an option.** Agents game tests. A maintainer's merge is a decision.
  When a funder does want tests, they run in a sandbox, there is a black-box mode, and the payment is held for a
  veto window.
- **Why Solana.** Two cheap transactions verify an RSA signature; fees for a whole bounty are a fraction of a cent;
  and most agent payment traffic is already here.
- **Composability.** `examples/oidc_gate` is a second program using the verifier: 22,515 compute units to read a
  verified token.

## The live run

Nothing here is staged. Every payment the escrow has made is on the site's Numbers page
(https://drexthealpha.github.io/Knos/#network), read from the program's own logs on devnet, with Knos's own accounts
counted apart from outside ones. Each row links the transaction; the pull request it paid for carries the Knos
comment with the same transaction. `python scripts/claims_check.py` fails if the programs are not immutable or if no
payment has ever been made.
