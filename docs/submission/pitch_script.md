# Pitch (under three minutes)

Narration, about 450 words. Every number is in `docs/facts.json` with its source and is checked by
`python scripts/claims_check.py`.

## The problem (0:00)

Coding agents open pull requests by the million, and they all say the same thing: "all tests pass."

We took 2,431 agent pull requests that say tests or CI pass, and looked at what GitHub's CI recorded at
that commit. In 660 of them, 27.2%, a check had failed.

So nobody can pay for agent work on the agent's word. One open-source bounty board went from 1,470 payouts in 2025
to 175 this year.

## The insight (0:30)

A merchant ships goods a buyer cannot see. Trade solved it with the letter of credit: the
bank pays against a document a third party signed, never against the seller's word.

For code, the third party exists. GitHub signs a statement about every workflow run. What was missing is a bank that
can read that signature by itself, and that nobody controls.

## The product (0:55)

Knos is that bank, on Solana.

A maintainer funds an issue with one comment: slash knos bounty 20. Anyone opens a pull request. No wallet, no
sign-up. A maintainer merges it. GitHub signs that it was merged, a Solana program checks GitHub's RSA signature on
chain, and the bounty goes to the author's GitHub account.

Nobody sits between the proof and the payment. The program has no admin and no upgrade authority. GitHub's keys are
fixed inside it, and a new key can enter only on GitHub's own signature.

## Why it holds (1:30)

It can never be patched, so we tested it that way. 517 Wycheproof vectors against the RSA
code. 10,000 random steps against the escrow, no money lost. And 21 cheating pull requests: plain CI was fooled by
17, our black-box check by none.

## The market (1:50)

Bounties are the wedge. The market is every payment for agent work. Coding agents sell about 5 billion dollars a
year of attempts, billed by the token whatever comes out. And 75 million agent payments a month already settle on
chain with no proof of delivery. We take 2.5%, only when someone is paid, and the program does not let us raise it.

The verifier itself is free: any Solana program can now require a fact that GitHub signed.

## How it spreads (2:15)

You paste a pull request and see whether its claim is true. You fund with a comment on
GitHub. And the person you pay finds money waiting under their GitHub name before they have heard of us.

## Who (2:30)

I am drexthealpha, and I work alone. Knos won the Sibyl Labs hackathon in September as a memory tool. In this
hackathon I measured the problem, tried several products around it, and kept the one that fixes it. It runs on
devnet today. An audit comes before mainnet.

AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed.
