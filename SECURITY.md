# Security

## Reporting a vulnerability

Please report security issues privately, through GitHub's "Report a vulnerability" on this repository (Security →
Advisories), not in a public issue. Expect a reply within a few days.

## What Knos touches

- **Reads:** your repo, your coding agents' local transcripts (Claude Code, Codex, Cursor), and their spend logs.
- **Writes:** only under `~/.knos`, plus the agent configuration files `knos init` edits. It backs each one up
  first, and `knos init --undo` restores it.
- **Never sent to your agents:** secrets (`.env`, keys, certificates, `.ssh`, `.aws`, and paths added with
  `knos private`).
- **Network:** answering, claiming and guarding never use it. Knos Pro commands make public RPC reads to check
  payments, and send payments that you or an agent's wallet sign. Nothing else leaves the machine.
- **Agent wallet keys:** kept in `~/.knos/wallets`, owner-only, never printed or logged. An agent wallet should hold
  only what you are willing for that agent to spend.
