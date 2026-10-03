# Security

Report a vulnerability privately through GitHub's security advisories on this repository
(Security > Report a vulnerability). Please do not open a public issue for it.

The security model, who must be trusted for what, what a Knos payment's signed statement does and does not cover, and every known limit:
[docs/SECURITY.md](docs/SECURITY.md).

Knos runs on Solana devnet only, and the money is test USDC. There are two deployments, and a report is handled
differently for each:

- **The second deployment** (`programs-v2/`), where every new bounty is funded, is upgradeable only through a
  multisig with a public 48-hour delay, until an outside review; then it is made immutable. A fix there is an
  upgrade, and it is public for those 48 hours before it can run. In the meantime a guardian can pause new funding
  for at most 7 days at a time and can revoke a signing key. It cannot move money.
- **The first deployment** (`programs/`) has no upgrade authority. A fault there cannot be patched. What can be done
  is to say so in public and to fix it in the second deployment.

There has been no outside review, and `knos mainnet-check` fails on that line until there is one.
