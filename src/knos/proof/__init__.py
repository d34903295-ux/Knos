"""Proof: compares what a change claims with what the repository and its checks show.

`claims` reads what an agent's last message asserts, `checks` runs the evidence itself (never the agent's word),
`engine` decides, `history` keeps every verdict in the repo's Sibyl store and learns required checks from past false
"done"s, `hook` is the Claude Code / Codex Stop hook, and `receipt` turns a "done" the checks agreed with into a
Merkle root anyone can check.
"""
