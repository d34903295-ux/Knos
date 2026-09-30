# Integrating with Knos

Three ways in. `knos init` wires the first two for Claude Code, Codex, Cursor, OpenCode and Claude Desktop.

## 1. The MCP server (any host that speaks MCP)

Start it over stdio with `knos mcp`. The config entry `knos init` writes:

```json
{"mcpServers": {"knos": {"command": "/absolute/path/to/knos", "args": ["mcp"]}}}
```

It answers only for the repo the host starts it in, and says so plainly outside a git repo.

| tool | arguments | what it does |
|---|---|---|
| `search` | `query`, `limit=8` | answers from past sessions (Claude Code, Codex, Cursor), commits, CLAUDE.md / AGENTS.md and code structure, each with its source; lines starting `[claimed]` say who holds files the answer touches |
| `about` | `thing` | everything known about one file, person, topic or symbol |
| `remember` | `fact`, `about`, `claiming=false`, `paths=[]` | writes a note every later session sees; with `claiming=true` it also claims `paths` (files, folders, globs like `src/parser/**`) for about 30 minutes, refreshed by claiming again |
| `done` | `about=""` | releases your own claims (one, by description or id, or all of yours); never anyone else's |
| `pay` (Pro) | `url`, `method`, `body`, `agent` | fetches an API; if it answers 402 over MPP or x402, pays it from that agent's own wallet, inside its cap |

**Identity.** An agent is (host, session). The host comes from the MCP client's name, and the session from what the
SessionStart hook recorded for the host process. Two sessions of the same host are two agents. A reconnect of the
same session keeps its claims.

## 2. The hooks (edit-time enforcement)

`knos hook guard --client claude|cursor|opencode` reads the host's pre-tool payload on stdin.
- **Exit 2:** refuses an edit to a file that another agent's live claim covers, or any edit once a Pro spend cap is
  reached. The reason is one line, e.g. `knos: src/parser/x.py is claimed by claude/3f9a1c2e since 14:02 (the parser
  rewrite). Ask them, or take other work; the claim lapses in 22 min. A person can release it: knos done --all`.
- **Exit 0:** everything else, including a missing store, an old version, a crash or an unreadable payload. Those also
  write one line to `~/.knos/hook.log`.

The deny output follows each host's hook format:
- Claude Code: `hookSpecificOutput.permissionDecision = "deny"`.
- Cursor: `permission = "deny"`.
- OpenCode: the plugin throws.

`knos hook start --client claude` (SessionStart) records the session and prints what is claimed and recently noted. It
always exits 0.

## 3. The Python library

```python
from knos.core import Claims

with Claims(repo=".", who="my-agent", session="run-42") as claims:
    taken, holder = claims.take("the parser", paths=["src/parser/**"])
    if not taken:
        print(holder["who"], "holds", holder["globs"])
    elif claims.holder("src/parser/lex.py") is None:
        ...  # yours to edit
    claims.release()
```

`take` is the same single transaction the MCP server uses: of many callers reaching for overlapping paths at once,
exactly one wins.

## 4. The team server (Knos Team)

`knos serve` is a small HTTP JSON API that the customer hosts. Each machine joins with
`knos init --remote <url> --token <seat token>`. From then on, that machine's claims are decided on the server by the
same code: one transaction, exactly one winner. They are keyed by the repo's first commit, so two clones of one repo
on two machines share one list. An agent on the wire is its host plus `session@machine`.

| route | what |
|---|---|
| `GET /v1/whoami` | the seat the token belongs to |
| `GET /v1/claims/live?repo=` / `POST /v1/claims/take` / `POST /v1/claims/release` | the claim list |
| `POST /v1/claims/blocked`, `GET /v1/claims/events?repo=` | refusals and history (for `knos worth` / `board`) |
| `POST /v1/notes`, `GET /v1/notes?repo=&q=` | notes shared across the team |
| `POST /v1/budget/report` | a machine's spend for day, week and month; returns the pooled total against the team cap |

How requests are handled:
- **Auth:** every request carries `Authorization: Bearer <seat token>`. Tokens are stored only as SHA-256.
- **Host header:** a request whose Host is not one the server answers to is refused (421).
- **Limits:** bodies over 64 KB get a 413.
- **Errors:** every error comes back as a JSON object.
- **Fail open:** a machine whose server does not answer within 2 s allows the edit and logs one line.

## Data on disk (all under `~/.knos`, never in your repo)

| file | what |
|---|---|
| `<repo>-<hash>/memory.db` | the repo's Sibyl memory store (shared by its worktrees) |
| `<repo>-<hash>/claims.db` | claims, sessions and claim events |
| `<repo>-<hash>/read.db` | what has been read already, so nothing is written twice |
| `backups/` | `knos init` and `knos reset` copies |
| `meter.db`, `budget.json`, `licence.json`, `agents.json`, `agentpay.db`, `wallets/` | Knos Pro (`wallets/` holds keys, owner-only) |
