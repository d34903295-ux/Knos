# knos-oidc: OIDC on Solana

A Solana program that verifies an OpenID Connect token (a JWT signed RS256) from **GitHub Actions** or **GitLab CI**
on chain. Once a token is verified, any other program can read its claims and trust them as much as it trusts the
issuer: which repository, which commit, which workflow file at which commit, which account started the run, on a
hosted runner or not, and any audience string the workflow asked for.

It has no admin and no upgrade authority, charges nothing, and does not know Knos's escrow exists.

## What you can build with it

- **Pay on a signed fact.** `knos-pay` in this repository: an escrow that pays a GitHub account when GitHub signs
  that their pull request was merged.
- **A release gate.** [`examples/oidc_gate`](../examples/oidc_gate): records, per repository, the last commit for
  which GitHub signed "a workflow ran here". An upgrade multisig or a DAO can then require that the binary it is
  about to deploy was built by CI from that commit.
- **Anything addressed to a GitHub account.** Grants, airdrops to contributors, access passes: the recipient proves
  who they are by running a workflow in a repository they own, and needs no wallet until they claim.

## Use it from a program

A verified token is an account owned by knos-oidc. Your program reads it. There is no CPI and no oracle.

```rust
use knos_oidc::claims::{fields, number, text};

if *token.owner != OIDC_ID { return Err(ProgramError::IllegalOwner); }
let data = token.try_borrow_data()?;
let v = knos_oidc::verified(&data).ok_or(ProgramError::InvalidAccountData)?;      // None unless fully verified
if v.issuer != knos_oidc::pins::ISSUER_GITHUB || !knos_oidc::fresh(v.exp, clock.unix_timestamp) {
    return Err(ProgramError::InvalidAccountData);
}
let [repo, sha, runner, aud] = fields(v.payload, [b"repository_id", b"sha", b"runner_environment", b"aud"])?;
if text(runner)? != b"github-hosted" || text(aud)? != b"my-app:release" { return Err(ProgramError::InvalidArgument); }
let (repo, sha) = (number(repo)?, text(sha)?);          // facts GitHub signed
```

In `Cargo.toml`: `knos_oidc = { path = "...", features = ["no-entrypoint"] }`. Reading a verified token cost 22,515
compute units in the example.

Three things your program must decide for itself, because knos-oidc only says the token is genuine:

1. **Which claims matter.** Check `runner_environment`, the repository, and the workflow (`job_workflow_ref` and
   `job_workflow_sha`) if the statement depends on what code ran.
2. **The audience.** Give your program its own audience prefix and require it, so a token minted for another
   program cannot be used with yours.
3. **Replay.** A verified token can be read by any program, any number of times, until an hour after its expiry.
   Bind it to one action (in the audience) and keep a guard, as `knos-pay` does for each of its instructions.

## Put a token on chain

From a workflow, ask GitHub for a token with your audience (`id-token: write`):

```bash
curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=my-app:release" | jq -r .value
```

Then anyone can carry it to the chain; it is three kinds of instruction (the Python client is
[`src/knos/settle/oidc.py`](../src/knos/settle/oidc.py), `relay.verify` does all of it; the instruction bytes are in
[`sdk/settle/fixtures.json`](../sdk/settle/fixtures.json)):

| instruction | what it does | transactions |
|---|---|---|
| `Write` | copies the token's bytes into an account derived from the payer and the token's hash | 3 for a GitHub token (880 bytes each) |
| `Step` | checks the header and issuer, then the RSA signature in Montgomery form, a few squarings at a time | 2 for RSA-2048, 6 for RSA-4096 |
| `Close` | returns the account's rent to the payer | 1 |

A token account belongs to whoever paid for it; nobody else can write or close it.

## The trust root

- **Genesis keys.** The sha256 of the modulus of every RS256 key GitHub Actions and GitLab published on 2 Oct 2026
  is a constant in the binary: 4 GitHub keys and 3 GitLab keys. `RegisterKey` creates a key account for a modulus
  with one of those hashes, for anyone who pays the rent, and for no other modulus.
- **Later keys.** `RegisterKey` also accepts a modulus named by a verified GitHub token from the rotate workflow
  (`drexthealpha/knos-oidc-rotate/.github/workflows/rotate.yml`) at the one commit in the binary, on a
  GitHub-hosted runner. That workflow can be called by anyone from any repository; the token names the workflow
  file and commit whoever calls it. See [SECURITY.md](SECURITY.md).
- **The arithmetic.** RS256 is PKCS#1 v1.5 with SHA-256. The verifier accepts exactly one encoding, exponent 65537
  only, and is tested against Google's Wycheproof vectors and against OpenSSL ([BENCH.md](BENCH.md)).

## Limits

- Tokens up to 8,192 bytes. RS256 only, 2048- or 4096-bit keys. GitHub Actions and GitLab issuers.
- A token is accepted until one hour after its `exp` (GitHub's tokens live five minutes; relaying takes time).
- Immutable means bugs are forever too. That is why it has been tested the way [BENCH.md](BENCH.md) describes, and
  why mainnet waits for an outside audit.
