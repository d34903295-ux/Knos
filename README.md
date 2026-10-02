# Knos

**AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed.**

A maintainer funds an issue with one comment: `/knos bounty 20`. Whoever's pull request is merged for that issue is
paid, to their **GitHub account**: no wallet, no sign-up and no address in the pull request. The money sits in a
program on Solana, not with Knos. The program releases it only on a token GitHub itself signed ("this pull request,
by this account, was merged in this repository"), and it checks GitHub's RSA signature on chain. Nobody can change
that program, Knos included: it has no admin and no upgrade authority.

Try it: [drexthealpha.github.io/Knos](https://drexthealpha.github.io/Knos/). Paste any agent pull request to see
whether its "tests pass" is true; protect a repo in two clicks; fund an issue with one comment. Everything is on
**Solana devnet** and the money is test USDC.

## Why

Coding agents open pull requests by the million, and a pull request's own description is not evidence.

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

When nobody can tell good agent work from bad, paying for it stops: one bounty platform went from 1,470 payouts in
2025 to 175 in 2026, and curl closed its bug bounty ([docs/WHY.md](docs/WHY.md) has the sources and the argument).
Trade between strangers solved this long ago with the letter of credit: the bank pays against a document a third
party signed, never against the seller's word. Knos is that for agent work. The document is GitHub's signature; the
bank is a program nobody controls.

## How it works

1. **Protect a repo.** One workflow file ([examples/knos-workflow.yml](examples/knos-workflow.yml)); the site
   prefills it. No secret, wallet or app in the repo.
2. **Fund an issue.** A maintainer comments `/knos bounty 20` (or writes that line in a new issue). GitHub signs a
   token that says so; anyone can carry it to Solana, and Knos's public worker does; the escrow opens.
3. **Every pull request for that issue gets a Knos check**, on GitHub, running none of its code: the repo's own
   CONTRIBUTING rules, what the repo's history made required, and whether a "tests pass" in the description is true
   at the head commit.
4. **A maintainer merges the one they want.** GitHub signs that; Solana verifies the signature; an hour later the
   bounty belongs to the author's GitHub account (until then `/knos veto` takes it back, because the pull request
   itself says which issue it closes). 2.5% fee (at least 0.05 USDC), only when someone is paid.
5. **The author claims it** whenever they like, to any Solana address, by running one workflow in a repository they
   own. Nobody else can get GitHub to sign that for their account.

No merge needed? Put acceptance checks in `.knos/acceptance/<issue>/` before funding. They run in a sandbox against
each pull request (Python, Node, Go, Rust, any command, or a black-box check that cannot be forged from inside); a
pass is paid after 24 hours unless vetoed. With no proof by the deadline (14 days), the funder gets everything back.

## What is in this repository

| | |
|---|---|
| [`programs/knos_oidc`](programs/knos_oidc) | **OIDC on Solana.** Verifies a GitHub Actions or GitLab CI token (RS256, 2048- and 4096-bit) on chain. Any program can then require a fact GitHub signed. No admin; new issuer keys enter only on GitHub's own signature. |
| [`programs/knos_pay`](programs/knos_pay) | **Pay on proof.** The escrow: funds per issue, pays a GitHub account, refunds on deadline. No admin. |
| [`examples/oidc_gate`](examples/oidc_gate) | A second program built on knos-oidc: it records the last commit GitHub signed for a repository. |
| [`.github/workflows`](.github/workflows) | `fund.yml`, `prove.yml`, `relay.yml`: the reusable workflows a protected repo calls, pinned by commit. |
| [`src/knos`](src/knos) | The judge (`knos proof gate`, `knos proof judge`), the relay anyone can run (`knos relay`), and the Stop hook. |
| [`sdk/settle`](sdk/settle) | A JavaScript client with no dependency, checked byte for byte against the Python client. |
| [`web`](web) | The site. It reads GitHub and Solana in the browser; there is no Knos server. |

## Free, for your own agent

```
pipx install knos        # or: uv tool install knos
knos init                # a Stop hook for Claude Code and Codex; undo with: knos init --undo
```

When your agent says tests pass, CI is green, it shipped or it is done, Knos runs that check itself before the agent
may stop, and remembers each repo's past false "done" in [Sibyl](https://sibyllabs.org) as a check it now requires.
A hook is a local aid and a person can remove it. The same check at the merge, on GitHub, is what a repo can require.

## What can go wrong, and what it costs

- [docs/SECURITY.md](docs/SECURITY.md): who has to be trusted for what, what a proof proves and what it does not.
- [docs/TAMPER.md](docs/TAMPER.md): 21 cheating pull requests against three judges.
- [docs/BENCH.md](docs/BENCH.md): every measured number, and how to re-run it.
- [docs/MARKET.md](docs/MARKET.md): the market, bottom up, with every assumption labelled; the fee and what a payout costs.
- [docs/COMPARE.md](docs/COMPARE.md): MergePay, GH Bounty, Algora, Octasol and CodeRabbit, from their own code and pages.
- [docs/OIDC.md](docs/OIDC.md): build on knos-oidc.
- `knos mainnet-check` prints every gate that must hold before real money, with its evidence. One fails today on
  purpose: there has been no outside audit. Mainnet stays locked until there is.

## History

Knos 0.1 (1–7 Sep 2026, before this hackathon) was shared memory for coding agents and won the Sibyl Labs hackathon.
0.2 and 0.3.0–0.3.9 (29 Sep – 2 Oct) were built during it and tried coordination, budgets and a jobs market before
the measurement above showed where the real problem was. 0.3.10 is one product. The earlier ones are in
[drexthealpha/knos-labs](https://github.com/drexthealpha/knos-labs). [docs/DISCLOSURE.md](docs/DISCLOSURE.md) says
what was built when, and what came from elsewhere.

MIT, all of it. Built by drexthealpha. Its memory engine is [Sibyl](https://sibyllabs.org).
