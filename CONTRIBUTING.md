# Contributing

Knos is one product: AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed.
Changes that delete something are the most welcome kind.

## Run the tests

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
pytest -q -n auto                          # offline; includes both programs in LiteSVM and the judge in four languages
node sdk/settle/test.mjs                   # the JavaScript client against the Python client's fixtures
python scripts/deadcode.py && vulture src/knos scripts --min-confidence 60     # nothing unused ships
python scripts/claims_check.py --offline   # every number in the README, site and submission has a source
```

The suite refuses every non-loopback connection and gives each test its own home. It runs the real programs, real
git repositories and real test runners rather than mocks. Please keep it that way. The Go and Rust judge tests are
skipped when those toolchains are not installed.

## Change a program

`programs/knos_oidc` and `programs/knos_pay` are deployed once and made immutable, so a change to either is a new
deployment with a new address, not an upgrade.

```bash
bash scripts/build_programs.sh             # needs cargo build-sbf (agave 2.3); refreshes tests/fixtures/*.so
cd programs/knos_oidc && cargo test --release      # the Wycheproof vectors
KNOS_FUZZ_N=10000 pytest -q -s tests/test_pay_chain.py -k random_walk
python scripts/settle_fixtures.py          # if an address, an audience or an instruction changed
```

A change to an instruction's bytes must change `src/knos/settle` (the authority), then `sdk/settle/index.js`, and
the fixtures will tell you whether they still agree.

## Add a language to the judge

`src/knos/judge.py`: a runner is one function that takes a sandbox `Box` and returns a `Run` (test ids and states,
which ids are the acceptance checks, and the sentinel and canary ids). Add its test directories and protected paths
beside the others, and a test in `tests/test_judge_langs.py` with an honest fix, a no-op and a cheat.

## A good pull request

- One thing, with a test that fails before it and passes after.
- If its description says tests pass, they pass: this repository runs its own check on pull requests.
- No number in the README, the site or `docs/submission/` without an entry in `docs/facts.json`.

Some issues carry a bounty (`/knos bounty` by a maintainer). The pull request that is merged for one is paid to its
author's GitHub account, on devnet, in test USDC. See the site's "Get paid" page.

## Licence

MIT. By contributing you agree your contribution is under the same licence.
