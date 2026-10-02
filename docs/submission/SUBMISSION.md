# Knos

**AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed.**

- Site: [drexthealpha.github.io/Knos](https://drexthealpha.github.io/Knos/)
- Code: [github.com/drexthealpha/Knos](https://github.com/drexthealpha/Knos) (MIT)
- Network: Solana devnet. The money is test USDC. Track: Solana.

## What it is

Coding agents are paid for attempts: by the seat, the token or the minute. Knos pays for results. A maintainer funds
a GitHub issue with one comment. An agent, or a person, opens a pull request. Knos checks the pull request's claims
against GitHub's own record without running its code. When a maintainer merges it and that check holds, GitHub signs
the fact, a Solana program verifies GitHub's RSA signature on chain, and the money goes to the GitHub account of the
author, or of whoever ran the agent. The program has no admin and no upgrade authority.

## What the judges ask

**Founder and market.** I am drexthealpha and I work alone, with coding agents, all day; this repository was built
with them ([DISCLOSURE.md](../DISCLOSURE.md)). Knos started as a memory tool for agents and won the Sibyl Labs
hackathon in September 2026. I built the first version of what is here, a hook that refuses an agent's "done" until
the check is run, because my own agents told me tests passed when they had not.

**The insight.** Buyers already pay agents per outcome wherever the seller can define the outcome. For code no
vendor does, because an agent grading its own work is not evidence: in 826 repositories, the first agent pull
request that said "tests pass" had a failing check in 147 (17.8%). But code is the one kind of agent work with a
neutral party that already signs the outcome. GitHub's signature is the document; a program nobody controls is the
bank. That is a letter of credit, and it is why this needs a chain: a company holding the escrow would be one more
party to trust ([WHY.md](../WHY.md)).

**Product and execution.** Working today on devnet, end to end: fund with a comment, a check on every pull request,
paid on merge to a GitHub account, claimed with one command; a free check that needs no money; an MCP server through
which agents find paid work; a sandboxed judge for bounties paid on tests; two immutable programs with reproducible
builds; a crate, IDLs and a JavaScript client for other teams. All of it was built during the hackathon.
The first four days tried several products around the same measurement before this one
([DISCLOSURE.md](../DISCLOSURE.md) is the dated record).

**Market.** Stated plainly: bounties on issues are a wedge and a small one. The money is the spend on coding
agents, billed by usage today; Knos is the meter that lets a buyer pay per merged pull request instead
([MARKET.md](../MARKET.md), every input labelled sourced, measured or assumption).

**Viability.** 2.5% of each payment, at least 0.05 USDC, only when someone is paid, fixed in the program. A payment
costs 105,000 lamports to relay. The verifier and the check are free. What would have to be true for this to be a
business is listed in [MARKET.md](../MARKET.md), section 3, and none of it is true yet.

**Traction.** None from outside, and we do not claim any. Every payment so far is Knos's own account proving the
path; the site's Numbers page counts outside use apart from ours and shows zero. What exists is the evidence for
the problem (the Agent PR Index, public and rebuilt every 6 hours) and a product a stranger can use today without
asking anyone.

## The six criteria

**Functionality.** The whole flow runs on devnet ([demo_script.md](demo_script.md)). The programs are tested
against 517 Wycheproof vectors, differentially against OpenSSL, and with a 10,000-step random walk; the judge
against 21 cheating pull requests ([BENCH.md](../BENCH.md), [TAMPER.md](../TAMPER.md)). Not done: an outside audit,
and mainnet. [SECURITY.md](../SECURITY.md) lists the known limits of this version, including that a trusted key
cannot be revoked, and what the mainnet version changes.

**Potential impact.** A way to pay for agent work by result, and a primitive for the ecosystem: `knos-oidc` lets
any Solana program require a fact GitHub or GitLab signed, for free. One use that needs nothing more from us is a
program upgrade that executes only for the commit CI built ([examples/oidc_gate](../../examples/oidc_gate)).

**Novelty.** Verifying GitHub's signature on a chain was done once before, a week earlier, on an EVM chain, with an
owner who registers the keys ([COMPARE.md](../COMPARE.md), [DISCLOSURE.md](../DISCLOSURE.md)). What is new here:
nobody holds that power (the issuer's keys are constants in an immutable program and rotate only on the issuer's
own signature); a merge pays only when what the pull request says about its tests holds on GitHub's record; and it
is on Solana, where we found no OIDC verifier at all.

**UX.** The funder types one comment on GitHub. The worker needs a GitHub account and nothing else, and claims with
one command. The chain is what makes that possible: money committed up front with no company holding it, payable
to an account id anywhere in the world.

**Open source and composability.** MIT throughout. A dependency-free Rust crate to read a verified token, IDLs for
both programs, a dependency-free JavaScript client checked byte for byte against the Python one, an example
consumer with its test, reproducible builds, and every action pinned to a commit ([OIDC.md](../OIDC.md)).

**Business.** Above, under viability.

## Go to market, demand, distribution

- **First user:** a maintainer who gets agent pull requests. They commit one file and every pull request's "tests
  pass" is checked against GitHub's record. No money, no wallet, no chain.
- **Then:** the same maintainer funds an issue with a comment. Agents find it through `knos mcp`; whoever is merged
  is paid.
- **Then:** teams and agent vendors that want to pay or charge per merged pull request. This needs real money,
  so it follows the audit.
- **Demand evidence:** the measurement above; GitHub shipping limits on outside pull requests in February and June
  2026; projects closing bounties and restricting agent pull requests ([WHY.md](../WHY.md), section 2). No user
  interviews and no outside users yet.
- **Distribution:** inside GitHub (a comment to fund, a pull request to earn), the MCP registry for agents, and
  money waiting under a GitHub name for someone who has never heard of the product.

## Prior work

Knos 0.1 (shared memory for agents, 1 to 7 Sep 2026) predates the hackathon and is not in this submission.
Everything in this repository was built during the hackathon; its history starts on 27 Sep 2026 and the product in
it from 29 Sep ([DISCLOSURE.md](../DISCLOSURE.md)).

## After the hackathon (plans, not shipped)

An outside audit, then the mainnet version described in [SECURITY.md](../SECURITY.md): keys that expire unless
re-attested, funding from a wallet, and an emergency path that cannot move money.
