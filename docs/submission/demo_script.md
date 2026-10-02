# Technical walkthrough (under three minutes)

Screen recording with narration. "The live run" at the end says where to see the run shown.

## 1. An agent finds paid work, does it, and its operator is paid (0:00)

1. On a GitHub issue, a maintainer comments `/knos bounty 20`. Within a minute the Knos comment answers with the
   escrow's address. On the site: 20.00 USDC, open.
2. In a terminal, a coding agent with `knos mcp` is asked to find paid work. It calls `knos_bounties`, sees the
   issue, writes the fix and opens a pull request that says "Fixes #N".
3. The `prove / check` status appears on the pull request: the repo's rules, and whether its "tests pass" is true.
   Show a second pull request that claims passing tests while its CI failed: refused, with the reason.
4. Merge the good one. Knos runs the check again at the merged commit, then GitHub signs. The Knos comment: the
   bounty is held for the author's GitHub account.
5. `knos claim <address>` in the terminal. The balance arrives.

## 2. What happens on chain (1:20)

- A GitHub Actions token is about 2,100 bytes. `knos-oidc` takes it in 3 writes, then checks the RSA-2048 signature
  in Montgomery form in 2 transactions of under 1M compute units each. Solana's limit is 1.4M per transaction, which
  is why the arithmetic is split and why this fits at all.
- The token account is now a fact any program can read. `knos-pay` reads the claims GitHub signed: the repository,
  the workflow file and its commit, the account to pay, the head commit. It checks them against the bounty and
  credits that GitHub id.
- Show the program-data accounts: no upgrade authority. Show `pins.rs`: GitHub's key hashes as constants, and the
  rotate workflow's commit.

## 3. Why these choices, and what they cost (2:05)

- **No admin, immutable.** A proof that an operator could forge is not a proof. The cost: a bug cannot be patched
  and a trusted key cannot be revoked. So it is on devnet, and the mainnet version expires keys.
- **Pay a GitHub id, claim later.** The worker needs nothing to start. The claim is a token only they can get
  GitHub to sign.
- **A merge plus a check, and tests as an option.** Agents game tests. A maintainer's merge is a decision; the check
  keeps a false "tests pass" from being paid. When a funder wants tests alone, they run in a sandbox and the payment
  is held for a veto window.
- **Why Solana.** Two cheap transactions verify an RSA signature; fees for a whole bounty are a fraction of a cent.
- **Composability.** `examples/oidc_gate` is a second program reading a verified token through a crate with no
  dependency: 21,632 compute units.

Built in this hackathon: everything shown. Before it: an unrelated memory tool, not in this repository.

## The live run

Nothing here is staged. Every payment the escrow has made is on the site's Numbers page
(https://drexthealpha.github.io/Knos/#network), read from the program's own logs on devnet, with Knos's own accounts
counted apart from outside ones. Each row links the transaction; the pull request it paid for carries the Knos
comment with the same transaction. `python scripts/claims_check.py` fails if the programs are not immutable or if no
payment has ever been made.
