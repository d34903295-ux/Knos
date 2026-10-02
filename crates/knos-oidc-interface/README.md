# knos-oidc-interface

Read a GitHub Actions or GitLab CI OIDC token that the knos-oidc program verified on Solana.

knos-oidc checks the token's RS256 signature on chain and leaves the result in an account it owns. Your program
takes that account and reads the claims the issuer signed: which repository, which commit, which workflow file at
which commit, which audience. There is no CPI and no oracle.

This crate has no dependency and does not allocate, so it builds with solana-program, pinocchio or anchor of any
version, and off chain. knos-oidc is immutable, so the account layout this crate reads does not change.

```toml
knos-oidc-interface = { git = "https://github.com/drexthealpha/Knos", tag = "v0.3.11" }
```

```rust
use knos_oidc_interface::{Token, ISSUER_GITHUB};

// token: the AccountInfo of a token account; now: Clock::get()?.unix_timestamp
let data = token.try_borrow_data()?;
// refuses an account knos-oidc does not own, a token not verified to the end, and one over an hour past its expiry
let tok = Token::read(&token.owner.to_bytes(), &data, now).map_err(|_| ProgramError::InvalidAccountData)?;
if tok.issuer() != ISSUER_GITHUB { return Err(ProgramError::InvalidAccountData); }

let repo = tok.claim_u64("repository_id").ok_or(ProgramError::InvalidArgument)?;   // GitHub sends ids as strings
let sha = tok.claim("sha").ok_or(ProgramError::InvalidArgument)?;                  // the commit the workflow ran on
let runner = tok.claim("runner_environment").ok_or(ProgramError::InvalidArgument)?;
let aud = tok.audience().ok_or(ProgramError::InvalidArgument)?;
if !runner.is("github-hosted") || !aud.is("my-app:release") || sha.len() != 40 {
    return Err(ProgramError::InvalidArgument);
}
```

`tok.claims([b"repository_id", b"sha", b"aud"])` reads several claims in one pass over the payload, which costs
fewer compute units than one `claim` call each.

## What stays your program's job

knos-oidc only says the token is genuine. `Token::read` only adds that the account is knos-oidc's and the token is
fresh. Your program decides the rest:

1. **The issuer.** `issuer()` is `ISSUER_GITHUB` or `ISSUER_GITLAB`; they sign different claims.
2. **The claims that matter.** The repository (`repository_id`), the workflow file and its commit
   (`job_workflow_ref`, `job_workflow_sha`) if the statement depends on what code ran, and `runner_environment`.
3. **The audience.** Give your program its own audience prefix and require it, so a token minted for another
   program cannot be used with yours.
4. **Replay.** A verified token can be read by any program, any number of times, until an hour after its expiry.
   Bind it to one action in the audience and record that the action was done.

## The program

| | |
|---|---|
| address (devnet) | `vpWym9azbPU5f2PH2a6n8c4RfmsyUeW2dMuWr1DSHcE` (`ID`, `ID_STR`) |
| instructions and accounts | [`idl/knos_oidc.json`](../../idl/knos_oidc.json) |
| how a token gets on chain, and the trust root | [`docs/OIDC.md`](../../docs/OIDC.md) |
| a complete consumer | [`examples/oidc_gate`](../../examples/oidc_gate) |

## Tests

`cargo test` reads the bytes of a real verified token account (`tests/fixtures/verified_token.bin`, written by
`scripts/interface_fixture.py` from the test build of knos-oidc) and checks every claim against a second JSON parser.
The claims reader is the same code as the program's own.
