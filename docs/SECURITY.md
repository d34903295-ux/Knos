# Security model

Report a vulnerability privately through GitHub's security advisories on this repository.

## Keys

| key | where | protection | what it can do |
|---|---|---|---|
| team owner | `~/.knos/team/owner.keystore` | encrypted: scrypt (N = 2^17, r = 8, p = 1) + AES-256-GCM; passphrase typed at a terminal | change the signer list, add and remove members |
| member key (one per person) | `~/.knos/team/member.json`, owner-only | hot: it signs every claim, so it cannot be passphrase-locked; it holds a small SOL float | create, renew and close claims; write records |
| cloud member key | the sandbox's secret `KNOS_MEMBER_KEY` | revocable with `knos team remove` | same as a member key; it cannot change signers or move budgets |
| Tempo budget root | `~/.knos/budget/tempo-root.keystore` | encrypted, as the owner key | authorize and revoke agents' access keys |
| Solana budget vault owner | `~/.knos/budget/solana-root.keystore` | encrypted, as the owner key | approve and revoke agents' delegates |
| agent limited key | `~/.knos/wallets/<agent>-<chain>-limited.json`, owner-only | the chain limits it | spend up to its limit, and nothing else |

Passphrases are read only from an interactive terminal. Knos refuses if stdin is not a TTY, or if `CLAUDECODE`,
`CODEX_*` or `CI` is set, and says "run this in your own terminal". So an agent cannot unlock a root key. The test
escape (`KNOS_TEST_PASSPHRASE_FILE`) works only for keystores under `~/.knos-test-wallets/` on a local or test
cluster (`tests/test_keystore.py`).

## Member trust

Any member key can close any claim in its team. That is how claims left behind by a crashed agent are swept. Every
close is compare-then-close: a Lighthouse assertion on the exact bytes of the attestation runs in the same
transaction, so a claim that was renewed in between is never closed by mistake. The rent goes back to the claim's own
signer, never to the closer. A member who closes others' live claims on purpose is visible in the transaction
history. Remove them with `knos team remove`.

## Each guard's scope

| guard | host | sees | does not see |
|---|---|---|---|
| edit guard | Claude Code (PreToolUse on Edit, Write, MultiEdit, NotebookEdit) | its file edits | shell commands that write files |
| edit guard | Codex (PreToolUse on apply_patch and Bash) | patches, and shell commands with visible write targets (`>`, `tee`, `sed -i`, `cp`, `mv`, `rm`, `git mv`, ...) | a program that writes files itself (`python -c ...`) |
| edit guard | Cursor (preToolUse), OpenCode (tool.execute.before), Copilot cloud agent (`.github/hooks`, template) | their edit tools | shell writes |
| commit guard | git pre-commit and pre-push, any host | every staged or pushed file | writes that are never committed |

Edit-time for hook tools; commit-time for raw shell writes. `git commit --no-verify` skips the commit guard, as it
skips every git hook, and nothing records that it was used.

## Known bypasses, stated

- **Blocking the RPC.** Knos never blocks work on an outage, so a machine that cannot reach the chain edits in
  local-only mode with a warning.
- **Disabling hooks.** A person can remove a host's hooks, or run an agent without them. `knos doctor` lists the
  hosts and machines that are unguarded.
- **Hot member keys.** Member keys sit on disk unencrypted so that agents can claim without a person present. A
  stolen member key can claim and close in that team until removed; it cannot touch budgets.
- **Budgets are chain-enforced; caps are not.** The per-agent Tempo and Solana limits hold even if Knos is bypassed.
  The 0.2 spend cap across agents (`knos budget set 20 --per day`) is enforced by the edit guard, so it is advisory
  against an agent that ignores hooks.

## Sibyl credentials

Read only from your own `~/.sibyl-memory/credentials.json` and handed only to Sibyl's own cap gate when a store
opens; never printed, never sent anywhere by Knos. Knos never routes around Sibyl's tier gate or its free-tier cap.
Sibyl Pro that Knos buys for a paying wallet (knos.sibyl_pro) is simulated on testnets, so Sibyl's cap still applies
there.
