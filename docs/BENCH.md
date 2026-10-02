# Measurements

Every number Knos states about itself, with the command that reproduces it. Nothing here is modelled.

## How often agents say "tests pass" when CI failed

<!-- bench:market -->
**Agent PR Index, 2026-10-02:** 2,431 PRs by AI coding agents claiming tests or CI pass (created 2026-06-04 – 2026-10-01); 9,207 PRs on repos owned by the PR's author or the human who assigned the agent were excluded. Of the 2,431 whose CI had finished at the head commit, **660 (27.2%) had a failing check** (95% Wilson interval 25.4%–28.9%). Published as `index.json` on the Pages site; built every 6 hours by `.github/workflows/index.yml`.

| agent | claiming PRs with finished CI | CI failed | 95% interval |
|---|---|---|---|
| GitHub Copilot coding agent | 787 | 194 (24.6%) | 21.8%–27.8% |
| Devin | 520 | 270 (51.9%) | 47.6%–56.2% |
| Claude GitHub app | 716 | 68 (9.5%) | 7.6%–11.9% |
| Claude Code | 206 | 17 (8.2%) | 5.2%–12.8% |
| OpenAI Codex | 202 | 111 (54.9%) | 48.1%–61.7% |
| **all** | **2,431** | **660 (27.2%)** | **25.4%–28.9%** |
<!-- /bench:market -->

Method: `scripts/agent_pr_ci.py` (the claim patterns and the CI verdict) and `scripts/agent_pr_index.py` (the scan and
the index). A pull request counts only if its description claims tests or CI pass and its CI had finished at the head
commit. Pull requests on repositories owned by the author, or by the person who assigned the agent, are left out: a
person's own repository is not a market observation. The agent's own session check runs are not counted as CI. The
published list has a Merkle root; `python scripts/agent_pr_index.py check --out index.json` recomputes it. The first,
smaller sample (303 pull requests, 55 failing, 18.2%, collected 1 Oct 2026) is kept in `docs/agent_pr_ci.json`.

What this does not show: that the agents lied. A description can be written before CI finishes. It shows that the
description is not evidence.

## The verifier on chain (knos-oidc)

Measured in LiteSVM against the built program: `pytest -q -s tests/test_oidc_chain.py tests/test_oidc_gate.py`.

| | transactions | compute units, each |
|---|---|---|
| verify a GitHub token (RSA-2048), a typical token | 2 | 764,395 and 832,314 |
| the longest token GitHub's claims allow (4,092 bytes) | 2 | 780,992 and 953,332 |
| the longest token the program accepts (8,192 bytes) | 2 | 809,355 and 1,188,020 |
| verify a GitLab token (RSA-4096) | 6 | 909,088 to 1,159,360 |
| another program reads a verified token (`examples/oidc_gate`) | part of its own | 22,515 |

The limit is 1,400,000 per transaction. A real GitHub Actions token today is 2,086 to 2,276 bytes (the ones we
captured), so writing it takes 3 transactions of 880 bytes each.

## Correctness of the RSA arithmetic

- **Wycheproof** (Google's test vectors for RSA PKCS#1 v1.5 signatures, SHA-256), run against the program's own
  arithmetic by `cd programs/knos_oidc && cargo test --release`: 517 vectors, for 2048- and 4096-bit keys. All 14
  valid signatures verify; all 499 invalid ones are refused; the 2 marked "acceptable" are refused; 2 use a public
  exponent other than 65537, which the program does not support, and are skipped.
- **Differential test against OpenSSL** (`tests/test_oidc_chain.py`): 60 random tokens, each also with one random
  bit flipped in its payload or signature. The program and OpenSSL (through `cryptography`) agree on all 120.
- **Forgeries** (`test_every_forgery_is_refused`): claims or signature changed after signing; signed by another key;
  `alg` set to HS256 or none; another issuer or a look-alike issuer; `iss` given twice; `exp` missing or a string;
  a payload that is not one JSON object; a fourth part; a short signature; a signature of 0, 1, the modulus or more.
  Each is refused with the error it should get.
This is testing, not an audit. There has been no outside audit; `knos mainnet-check` fails on that line on purpose.

## The escrow (knos-pay)

`pytest -q -s tests/test_pay_chain.py`.

| | compute units |
|---|---|
| Pay (verify the token's claims against the job, credit the author, take the fee, close the job) | 54,725 |
| Claim | 42,740 |

**Random walk** (`KNOS_FUZZ_N=10000 pytest -q -s tests/test_pay_chain.py -k random_walk`; 600 steps in the default
suite, 10,000 in `program.yml` on every change and nightly): random funds, proofs, wrong proofs, settles, vetoes,
refunds, claims and clock jumps. After every step, for each mint: vault = funded − claimed − fees − refunded, and
never negative. Last 10,000-step run: 0 violations.

## What a relayer pays

Measured with a 2,100-byte token (`tests/test_settle_relay.py` drives the same path):

| step | transactions | fees (lamports) | rent the relayer puts up |
|---|---|---|---|
| fund (a maintainer's comment) | 7 | 35,000 | the job account, 2,672,640, returned when the job is paid or refunded; once per repository, 1,002,240 for its rate account; once per mint ever, 2,039,280 for the vault |
| pay (a merge) | 7 | 35,000 | once per payee, 1,113,600 for their public record; 1,224,960 for their balance account, returned at their claim |
| claim | 7 | 35,000 | once per address, 2,039,280 for the claimer's token account |

So a bounty to someone who has been paid before, in a repository that has funded before, costs a relayer 105,000
lamports (0.000105 SOL) end to end. A first payout to a new person costs about 3,150,000 more, once. A token that is
refused costs nothing when a read can tell (the usual case: `precheck` in `src/knos/settle/relay.py`), and fees only
when it takes the signature check to tell; the token account's rent always comes back.

## The judge

- **Tamper benchmark**: [TAMPER.md](TAMPER.md), regenerated by `python scripts/tamper_bench.py`.
- **Languages**: `tests/test_judge_langs.py` judges an honest fix, a no-op and a cheat in Python, Node, Go and Rust
  repositories, with a plain command, and with the black-box check.
- **Sandbox**: the same file runs a pull request that tries to write the judge's output file, read CI's environment
  and open a network connection; all three fail, and an honest fix still passes.

## The suite

`pytest -q` (offline; the tests refuse every non-loopback connection). `node sdk/settle/test.mjs` checks the
JavaScript client against the Python client byte for byte. `node tests/web/site.mjs <site>` drives the built site in
headless Chromium against a mocked GitHub and Solana.
