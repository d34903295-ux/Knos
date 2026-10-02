# Security

Report a vulnerability privately through GitHub's security advisories on this repository
(Security > Report a vulnerability). Please do not open a public issue for it.

The security model, who must be trusted for what, and what a Knos proof does and does not prove:
[docs/SECURITY.md](docs/SECURITY.md).

Knos runs on Solana devnet only. The two programs have no admin and no upgrade authority. There has been no outside
audit, and `knos mainnet-check` fails on that line until there is one.
