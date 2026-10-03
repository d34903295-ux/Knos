# knos-oidc: OIDC on Solana

A Solana program that verifies an OpenID Connect token (a JWT signed RS256) from **GitHub Actions** or **GitLab CI**
on chain. Once a token is verified, any other program can read its claims and trust them as much as it trusts the
issuer and the account the workflow ran in: which repository, which commit, which workflow file at which commit,
which account started the run, and any audience string the workflow asked for.

It charges nothing and does not know Knos's escrow exists. There are two deployments on devnet:

| | first deployment ([`programs/knos_oidc`](../programs/knos_oidc)) | second deployment ([`programs-v2/knos_oidc`](../programs-v2/knos_oidc)) |
|---|---|---|
| address | `vpWym9azbPU5f2PH2a6n8c4RfmsyUeW2dMuWr1DSHcE` | `FkwZdsYCmzicJMtHLTkPK76bYNVG4WNwkWJBiVWNtF3W` |
| who can change it | nobody: it has no upgrade authority | Knos, only through a multisig with a public 48-hour delay, until an outside review; then it is made immutable |
| the keys it starts from | GitHub's four and GitLab's three of 2 Oct 2026 | GitHub's four of 2 Oct 2026 |
| a later key | named by a GitHub-signed token of the rotate workflow, from any repository that calls it | named by such a token from one of two repositories of one account; then a day's wait and a guardian's approval |
| how long a key lives | for ever; it cannot be revoked | 30 days after it was last attested; the guardian can revoke it |

A token account has the same layout under both, so the same code reads it. Build on the second. The first cannot
be fixed, and [SECURITY.md](SECURITY.md) says what is wrong with its key rules.

## What you can build with it

- **Pay on a signed fact.** `knos-pay` in this repository: an escrow that pays the author of a pull request when
  the funder's workflow reads the merge and the check results from GitHub, and GitHub signs that workflow run.
- **A release gate.** [`examples/oidc_gate`](../examples/oidc_gate): records, per repository, the last commit for
  which GitHub signed "a workflow ran here". An upgrade multisig or a DAO can then require that the binary it is
  about to deploy was built by CI from that commit. It is written against the first deployment.
- **Anything addressed to a GitHub account.** Grants, airdrops to contributors, access passes: the recipient shows
  who they are by running a workflow in a repository they own.

## Use it from a program

A verified token is an account owned by knos-oidc. Your program reads it. There is no CPI and no oracle.

The interface crate, [`crates/knos-oidc-interface`](../crates/knos-oidc-interface), has no dependency and does not
allocate, so it builds with solana-program, pinocchio or anchor of any version:

```toml
knos-oidc-interface = { git = "https://github.com/drexthealpha/Knos", tag = "v0.3.12" }
```

```rust
use knos_oidc_interface::{v2, ISSUER_GITHUB};

let data = token.try_borrow_data()?;
// refuses an account the second deployment does not own, a token not verified to the end, and one over an hour
// past its expiry. `Token::read` is the same for the first deployment
let tok = v2::read(&token.owner.to_bytes(), &data, clock.unix_timestamp).map_err(|_| ProgramError::InvalidAccountData)?;
if tok.issuer() != ISSUER_GITHUB { return Err(ProgramError::InvalidAccountData); }
let repo = tok.claim_u64("repository_id").ok_or(ProgramError::InvalidArgument)?;
let sha = tok.claim("sha").ok_or(ProgramError::InvalidArgument)?;
let aud = tok.audience().ok_or(ProgramError::InvalidArgument)?;
if repo != MY_REPOSITORY_ID || !aud.is("my-app:release") { return Err(ProgramError::InvalidArgument); }
// GitHub signed that a workflow in that repository, at commit `sha`, asked for this audience
```

[`examples/oidc_gate`](../examples/oidc_gate) is a whole program written this way, with its test; reading a verified
token costs it 21,632 compute units. The instructions and account layouts of both deployments are in
[`idl/`](../idl) (Shank format, checked against the clients by `tests/test_idl.py`). From JavaScript:
[`sdk/settle`](../sdk/settle), one file with no dependency, attached to every release as an npm tarball.

Five things your program must decide for itself, because knos-oidc only says the token is genuine:

1. **Who ran the workflow.** Pin the repository (`repository_id`) or its owner (`repository_owner_id`). These are
   the claims an outsider cannot get GitHub to sign.
2. **What code ran.** Check the workflow file and its commit (`job_workflow_ref`, `job_workflow_sha`) if the
   statement depends on it. A commit fixes the file's content.
3. **Not the runner alone.** `runner_environment` says `github-hosted` for a runner whose image an organisation on a
   paid plan built itself, and a reusable workflow's runner is chosen in the caller's account. So a pinned workflow
   called from a stranger's repository tells you little: whoever owns the repository the run happened in can have
   decided what the workflow saw ([SECURITY.md](SECURITY.md), section 2). Treat what a workflow derived as that
   repository's own word.
4. **The audience, and replay.** Give your program its own audience prefix and require it, so a token minted for
   another program cannot be used with yours. A verified token can be read by any program, any number of times,
   until an hour after its expiry. Bind it to one action in the audience and keep a guard, as `knos-pay` does for
   each of its instructions.
5. **Whether the key is still good** (second deployment). A token account that was verified stays verified if its
   key is revoked or expires afterwards. To refuse such a token, take the key account the token account names (its
   bytes 18 to 50) and ask `knos_oidc::key_usable`, as `key_good` does in
   [`programs-v2/knos_pay/src/gh.rs`](../programs-v2/knos_pay/src/gh.rs).

## Put a token on chain

From a workflow, ask GitHub for a token with your audience (`id-token: write`):

```bash
curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=my-app:release" | jq -r .value
```

Then anyone can carry it to the chain. It is three kinds of instruction, the same bytes for both deployments (the
Python client is [`src/knos/settle/v2/oidc.py`](../src/knos/settle/v2/oidc.py); the instruction bytes are in
[`sdk/settle/fixtures.json`](../sdk/settle/fixtures.json)):

| instruction | what it does | how many |
|---|---|---|
| `Write` | copies the token's bytes into an account derived from the payer and the token's hash | 880 bytes each: 3 for a GitHub token of about 2,100 bytes |
| `Step` | checks the RSA signature in Montgomery form, a few squarings at a time; the last one also checks the header and the issuer and decodes the claims | 2 for RSA-2048 (8 and 8 squarings), 6 for RSA-4096 (2, 3, 3, 3, 4 and 1), each in a transaction of its own |
| `Close` | returns the account's rent to the payer | 1 |

A token account belongs to whoever paid for it; nobody else can write or close it. [BENCH.md](BENCH.md) has the
compute units of every step.

## The trust root of the second deployment

Fixed in [`programs-v2/knos_oidc/src/pins.rs`](../programs-v2/knos_oidc/src/pins.rs); the module documentation of
[`lib.rs`](../programs-v2/knos_oidc/src/lib.rs) lists every instruction.

- **Genesis keys.** The sha256 of the modulus of each of the four RS256 keys GitHub Actions published on 2 Oct 2026
  is a constant in the binary. `RegisterKey` creates a key account for a modulus with one of those hashes, for
  anyone who pays the rent. Such a key verifies at once.
- **Every other key**, a GitLab key or one GitHub adds later, needs a verified GitHub token of the rotate workflow
  (`drexthealpha/knos-oidc-rotate/.github/workflows/rotate.yml` at the one commit in the binary), run by its
  schedule or by hand in one of two repositories of the account drexthealpha, whose audience names the key's hash.
  That key verifies nothing for a day, and nothing at all until the guardian approves it.
- **Every key expires** 30 days after it was registered or last attested. `Refresh` takes the same attestation and
  moves the expiry forward. Anyone may send it.
- **The guardian**, a multisig vault, can approve an attested key and revoke any key for ever. It cannot add one.
- **The arithmetic.** RS256 is PKCS#1 v1.5 with SHA-256. The verifier accepts exactly one encoding, exponent 65537
  only, and is tested against Google's Wycheproof vectors and against OpenSSL ([BENCH.md](BENCH.md)).

`Step` refuses a key that is not ready, is revoked, is neither a genesis key nor approved, is before its start or
past its expiry (errors 68 and 76 to 78). [SECURITY.md](SECURITY.md), sections 5 to 7, says who can do what to a
key and to the program.

## Limits

- Tokens up to 8,192 bytes. RS256 only, 2048- or 4096-bit keys. GitHub Actions and GitLab issuers.
- A token is accepted until one hour after its `exp` (the issuers' tokens live five minutes; relaying takes time).
- On the second deployment a GitLab token verifies only once a GitLab key has been attested and approved. None is a
  genesis key there.
- The second deployment's keys need attesting at least every 30 days. If that stops, nothing verifies until an
  upgrade ([SECURITY.md](SECURITY.md), "When keys run out").
- The first deployment is immutable, so its faults are for ever: a key it trusts never expires and cannot be
  revoked, and a key attestation counts from any repository.
- Devnet only, and no outside review yet. Mainnet waits for one.
