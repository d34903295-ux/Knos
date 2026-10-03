# knos-settle

The JavaScript client for Knos's two Solana programs: `knos-oidc`, which verifies GitHub Actions and GitLab CI OIDC
tokens on chain, and `knos-pay`, the escrow that pays a GitHub account when a pull request is merged with the checks named at funding passing, as a
GitHub-signed workflow run attests.
It derives the addresses, builds the audiences and instructions, reads the accounts and serializes a transaction for
a wallet to sign. One file, no dependencies: it runs as is in a browser and in Node 20+.
Every encoding is checked byte for byte against the Python client (`fixtures.json`, `npm test` in the repository).

## Install

No registry account is needed. From the release:

```bash
npm i https://github.com/drexthealpha/Knos/releases/download/v0.3.12/knos-settle-0.3.12.tgz
```

Or import it in a browser:

```js
import * as knos from "https://cdn.jsdelivr.net/gh/drexthealpha/Knos@v0.3.12/sdk/settle/index.js";
```

## Read a bounty's state

```js
import * as knos from "knos-settle";

const RPC = "https://api.devnet.solana.com";
const k = knos.client({ knos_oidc: "vpWym9azbPU5f2PH2a6n8c4RfmsyUeW2dMuWr1DSHcE", knos_pay: "9UzPFbh2A4e4sEPgngKG523FfYLnQ3qPfFVfFTTAdfDi" });

const address = await k.job(987654321, 7);                       // GitHub repository id, issue number
const job = knos.parseJob(await knos.account(RPC, address));     // null when the issue has no bounty
if (!job) console.log("no bounty on this issue");
else console.log(job.state, job.amount / 1e6, "USDC until", new Date(job.deadline * 1000).toISOString());
```

`k.job(repoId, issue, funder)` is the address of a bounty a wallet added to the same issue. `knos.parseDue` and
`knos.parseRep` read what waits for a GitHub user and their public record; `k.fundIx` and `knos.serializeTx` build
the transaction that funds a bounty.

The instructions and account layouts of both programs are in [`idl/`](https://github.com/drexthealpha/Knos/tree/main/idl) (Shank format). A Solana program reads a
verified token with the Rust crate
[`knos-oidc-interface`](https://github.com/drexthealpha/Knos/tree/main/crates/knos-oidc-interface).
