# Pitch (under three minutes)

Narration, about 470 words. Every number is in `docs/facts.json` with its source and is checked by
`python scripts/claims_check.py`.

## The thesis (0:00)

AI coding agents are paid for attempts. Knos pays them for results, on a signature nobody can fake.

## The problem (0:10)

Agents open pull requests by the million, and they all say "all tests pass." We checked that against GitHub's own
record. In 826 repositories, the first agent pull request that said so had a failing check in 147. That is 17.8%.

So nobody pays an agent on its word. Every coding agent is billed by the seat or the token, whatever comes out.

## The insight (0:35)

Where a seller can define the outcome, buyers already pay per outcome. One support agent charges 0.99 dollars per
resolved conversation and is near 100 million dollars a year. But the seller decides what "resolved" means.

Code is different. A neutral party already signs the outcome: GitHub records who opened a pull request, what its
checks said, and who merged it. Trade has worked this way for centuries: a letter of credit pays against a document
a third party signed, never against the seller's word. What was missing is a bank that reads GitHub's signature by
itself, and that nobody controls.

## The product (1:05)

Knos is that bank, on Solana.

A maintainer funds an issue with one comment. An agent opens a pull request. Knos checks its claims against GitHub's
record without running its code. A maintainer merges it. GitHub signs that, a Solana program verifies the signature
on chain, and the money goes to the GitHub account of whoever ran the agent. No wallet to do the work. One command
to claim.

The program has no admin and no upgrade authority. Nobody sits between the proof and the payment, including me.

## Why it holds (1:40)

It cannot be patched, so it was tested that way: 517 Wycheproof vectors against the RSA code, 10,000 random steps
against the escrow with no money lost, and 21 cheating pull requests. Plain CI was fooled by 17, our black-box
check by none.

## The market (2:00)

Bounties are the wedge, and a small one: about 64 thousand dollars is open across every board today. The market is
the 7 billion dollars a year paid for coding agents by usage. Knos is the meter that lets a buyer pay per merged
pull request instead. We take 2.5%, only when someone is paid.

## How it spreads (2:20)

The check is free: one file, and every pull request's claims are tested. Agents find paid work through an MCP
server. And the person you pay finds money waiting under their GitHub name before they have heard of us.

## Who (2:40)

I am drexthealpha. I work alone, with coding agents, all day. I built this because my own agents told me the tests
passed when they had not. It runs on devnet today. Nobody outside has used it yet. An audit comes before real money.

AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed.
