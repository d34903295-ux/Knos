# Knos compared

What each alternative does, read from its own code and pages on 2 Oct 2026. Where we could not confirm something, it
says so. Knos is on devnet; several of these handle real money today, and that is their advantage.

## Paying for a pull request

| | who decides the payment | who could take or redirect the money | what the worker needs before starting | funding step | fee | where |
|---|---|---|---|---|---|---|
| **Knos 0.3.10** | GitHub's signature that a maintainer merged the pull request (or that the funder's checks passed), verified by a program on chain | nobody: no admin, no upgrade authority, issuer keys fixed in the binary and rotated only on GitHub's own signature | a GitHub account. No wallet, no stake, no sign-up | one comment on the issue: `/knos bounty 20` | 2.5%, at least 0.05 USDC, only when paid; fixed in the program | Solana devnet, test USDC |
| **MergePay** | GitHub's OIDC signature, verified on chain (the same idea, on an EVM chain) | its owner: issuer keys are proposed by the contract owner and become active after a 3-day delay, so the owner can register a key of their own and sign anything | a GitHub account; links a wallet later with a `workflow_dispatch` token | not confirmed from the code we read | not confirmed | Arc (EVM); repository created 23 Sep 2026 |
| **GH Bounty** | AI validators score the pull request, then the bounty's creator signs the payout | the creator, who must sign `resolve_bounty` | a wallet and a 0.035 SOL stake, locked 14 days | post on its site and deposit SOL | the site says 2.5%; the program pays the full amount | Solana; SOL only |
| **Octasol** | the maintainer signs `complete_bounty` | the maintainer | a wallet | install a GitHub App, set up escrow | none in the contract | Solana; its site was down when read |
| **Algora** | the maintainer awards it by hand after merge | the funder decides whether to pay; the payment goes through Algora | an Algora account with payouts set up | on Algora or by a bot command | 9% (its pricing page) | fiat; 120 countries |
| **GitHub Sponsors** | the sponsor | the payment goes through GitHub | a Sponsors profile in a supported country | on GitHub | 0% from personal accounts, up to 6% from organisations | fiat; not tied to any piece of work |

What is different about Knos, in one line each:

- **Against MergePay**, which had the idea of verifying GitHub's signature on chain and of paying a GitHub id a week
  earlier ([DISCLOSURE.md](DISCLOSURE.md)): Knos removes the owner. There is no key-registration role at all, the
  programs are immutable, and new keys come in on GitHub's signature. It also verifies GitLab (RSA-4096), runs a
  check on every bounty pull request before merge, and ships a judge with a sandbox for bounties paid on tests.
- **Against GH Bounty and Octasol**: nobody signs the payout. A person decides by merging, which they were going to
  do anyway; the money then moves on GitHub's signature.
- **Against Algora and Sponsors**: the money is committed up front in a program, not promised; the worker needs no
  account anywhere; and the fee is 2.5% against Algora's 9%.
- **Where Knos is behind**: it is devnet only; it has not been audited; it has no real payment volume; a bounty's
  refund waits for its deadline (14 days by default).

Sources: MergePay, [github.com/codeswithroh/mergepay](https://github.com/codeswithroh/mergepay) (its contracts and
README; 25 commits, 23–30 Sep 2026). GH Bounty, [ghbounty.com](https://www.ghbounty.com) and
[github.com/Ghbounty/GhBounty](https://github.com/Ghbounty/GhBounty): `contracts/solana/programs/ghbounty_escrow/src/lib.rs`
(`ResolveBounty` needs `creator: Signer`; payout of the full `bounty.amount`), `src/constants.rs`
(`MIN_STAKE_LAMPORTS = 35_000_000`, 14-day lock). Octasol,
[github.com/Octasol/octasol_contract](https://github.com/Octasol/octasol_contract) (`CompleteBounty` with
`has_one = maintainer`) and octasol.io (TLS failed; plain HTTP said the domain had expired). Algora,
[algora.io/pricing](https://algora.io/pricing). GitHub Sponsors,
[docs.github.com](https://docs.github.com/en/sponsors/getting-started-with-github-sponsors/about-github-sponsors).

## Telling whether a pull request's claims are true

| | what it does | what it costs | what it cannot do |
|---|---|---|---|
| **Knos check** (`prove / check`, and the paste box on the site) | compares the description's "tests pass" with GitHub's record of the head commit; applies the repo's CONTRIBUTING rules; remembers what a repo's history made required | free | judge design or style. It reports facts |
| **CodeRabbit** | an LLM reviews the diff and comments | $12 to $48 a seat a month ([Sacra](https://sacra.com/c/coderabbit/)) | be held to anything: it is an opinion |
| **Required CI** (a branch ruleset) | blocks the merge while a check fails | free | say anything about what the description claimed, or pay anyone |
| **Vouching** (e.g. Ghostty's) | maintainers vouch for people before their pull requests are accepted | free | scale to strangers, which is where bounties come from |

## Verifying an OIDC token on a chain

We searched GitHub and the web on 2 Oct 2026 for a Solana program that verifies an OIDC or RS256 token on chain and
found none. On EVM chains there is MergePay (above) and a draft proposal for a standard
([ERC proposal, GitHub Actions attestation verification](https://ethereum-magicians.org/t/erc-proposal-github-actions-attestation-verification/27661)).
Sui's zkLogin and Aptos Keyless verify OIDC tokens for wallet login with zero-knowledge proofs and a trusted set of
keys maintained by the chain's validators; they answer "who is this user", not "what did this workflow do", and are
not callable as a general fact-checker by other programs in the way a token account here is. That last sentence is
our reading of their designs, not a tested claim.
