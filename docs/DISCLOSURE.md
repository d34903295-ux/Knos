# Disclosure

## What existed before the hackathon (before 14 Sep 2026)

- **Knos 0.1.0 to 0.1.8**, released 1 to 7 Sep 2026: shared local memory for coding agents, with topic claims, built
  on Sibyl. Knos 0.1 **won the Sibyl Labs hackathon**.

That code is no longer in this repository's tree (its git history has it). It lives on in
[drexthealpha/knos-labs](https://github.com/drexthealpha/knos-labs).

## What was built during the hackathon

- **13 to 28 Sep 2026**: two experiments that were never shipped (`knos-residual`; an on-chain control plane).
- **29 Sep to 2 Oct 2026, Knos 0.2.0 to 0.3.9**: claims enforced at edit time across agent vendors, agent budgets, a
  jobs market with an escrow, the Stop hook, the Agent PR Index, and a first escrow that verified GitHub's signature
  on chain but whose keys and workflows were registered by an admin. These also moved to knos-labs, except the
  Stop hook, the index and the judge, which are here.
- **Knos 0.3.10** (this repository): the two programs with no admin (`knos-oidc`, `knos-pay`), payment to a GitHub
  account, the claim, the merge and tests modes with a veto window, the gate on every bounty pull request, the
  sandboxed judge for Python, Node, Go and Rust and its black-box mode, the relay, the JavaScript client, the site.

The story in one line: we measured that 27.2% of agent pull requests that say "tests pass" had a failing check, tried
several products around that fact, and kept the one that fixes it.

`git log` and [CHANGELOG.md](../CHANGELOG.md) are the dated record.

## Ideas and code from elsewhere

- **MergePay** ([github.com/codeswithroh/mergepay](https://github.com/codeswithroh/mergepay), first commit 23 Sep
  2026) verifies GitHub's OIDC signature on an EVM chain, pays a GitHub user id, and links a wallet with a
  `workflow_dispatch` token from a repository the user owns. We read it on 2 Oct 2026. Knos 0.3.7 (1 Oct) already
  verified GitHub's signature on Solana; paying a GitHub id and claiming with a `workflow_dispatch` token, in
  0.3.10, follow MergePay's design. No code was copied; the programs here are written for Solana from scratch.
- **Wycheproof** test vectors (Google, Apache-2.0) are in `programs/knos_oidc/tests/vectors/`, unmodified.
- **Sibyl** (`sibyl-memory-client`, by Sibyl Labs) is the memory engine, used as a dependency.
- Dependencies: `solana-program`, `solana-security-txt` (Rust); `typer`, `rich`, `solders` (Python). The web app
  loads `@wallet-standard/app` from a CDN only when a sponsor connects a wallet.
- The economics references in [WHY.md](WHY.md) (Akerlof, Spence, Holmström, Goodhart) and the letter-of-credit
  comparison are an argument, not a claim of endorsement.

## How the code is written

Commits in this repository are AI-assisted: they were written with AI coding agents. Every commit is authored and
reviewed by drexthealpha, who is responsible for it. Commits carry no AI co-author trailers, because the author of
record is the person who reviewed and committed the change.

## What has not been done

No outside audit. No mainnet deployment. No real money has moved. Outside use is whatever the site's Numbers page
shows, counted apart from Knos's own.
