# Pitch (under three minutes)

Narration in the founder's own voice, about 430 words: a little under three minutes read aloud at an even pace.
The times in the headings are where each part starts at that pace. A number written out here is in
`docs/facts.json` with its source, and `python scripts/claims_check.py` checks it. A number that only the release
run can measure is a slot, `[[stat: name]]`: `python scripts/bench_docs.py --slots` lists the ones still open, and
nothing is recorded until they are filled.

## Who (0:00)

I'm drexthealpha. That is a pseudonym, and I say so plainly. I work alone and ship in public, with coding agents:
32 days with a commit of mine in the public repository since the first of September.

## The problem (0:15)

My own agents kept telling me the tests passed when they had not. So I measured it. In 826 repositories, the first
agent pull request that said "tests pass" had a failed check of some kind in 147, and a failed test or build in 80.

A description is not evidence. That is the maintainer's problem: every agent pull request has to be read by a
person who cannot take its word. And it is the buyer's problem. A buyer cannot pay an agent for a result on its
word, so agents are mostly sold by the seat and by the token.

## The insight (0:56)

GitHub already signs the fact. It records who opened a pull request, what its checks concluded and who merged it,
and it signs statements about a workflow run. A Solana program can check that signature by itself. So after the
buyer's merge, the money moves on that signature. Not on the agent's word, and not on mine.

## The product (1:18)

*On screen, fifteen seconds: a comment, a merge, a payment in the explorer.*

A maintainer funds an issue with one comment, and names the checks that must pass. Someone opens a pull request.
She merges it. Her repository's workflow reads the merge and the checks from GitHub, GitHub signs that run, the
program checks the signature, and the author is paid. No veto afterwards, no claim form, and no company holding the money.

## Evidence (1:39)

It runs end to end on Solana devnet, in test money. 3 payments have
gone from one account to another, and 3 of them paid an outside contributor
for a merged pull request. The median from merge to paid is 110 seconds.
909 tests pass. There has been no outside review yet, and no outside buyer.

## Why now (2:03)

Agents open pull requests by the million, and pricing by outcome has begun: this September a vendor started billing
per merged changeset. What was missing is a payment that follows GitHub's record instead of the seller's own count.

## The business (2:18)

Knos takes 2.5%, only when someone is paid. The code is MIT, so a fork can copy the programs. It cannot copy the
operation: signing keys kept attested, a relay kept running, and an outside review of the exact bytes on chain,
which is the next step. Real money comes after that review, on mainnet.

## The ask (2:40)

I would like the interview, so I can show it live.

Bounties that pay when the pull request is merged with the checks you named passing. Attested by a GitHub-signed workflow run, verified on Solana.
