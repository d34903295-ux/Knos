"""What a person sees when they do not know what to type. One screen, 80 columns."""

from __future__ import annotations

MAIN = """  knos - one local memory every coding agent on this machine shares,
         and the list of which files each of them is changing right now

  Once:
      knos init                  wire Claude Code, Codex, Cursor, OpenCode
      knos demo                  the whole product on a throwaway repo

  Every day (mostly your agents do this for you):
      knos ask "why did we drop redis?"
      knos claim "the parser" -p src/parser/**    other agents' edits refused
      knos done                  give your claims back
      knos status                what it holds, who is working where
      knos board                 the same, live, in your browser

  More
      knos remember, notes, forget    things you tell your agents
      knos private <path>             keep a path from your agents
      knos point, compact             catch up now; make room in memory
      knos worth, bench               what it has done; measure it
      knos spend, report, budget      Pro: spend, one cap across agents
      knos pay, pro                   Pro: agent wallets; buy or check Pro
      knos serve                      Team: share claims across machines
      knos help <cmd>                 more about one command"""


PER_COMMAND = {
    "init": """\
  knos init                  add knos to every agent found on this machine
  knos init --hosts claude,cursor
  knos init --undo           take out everything it added
  knos init --print          show what it would write, change nothing

  Writes the memory server (MCP) into each agent's config, and for Claude
  Code, Cursor and OpenCode the edit guard and the session notice. Every
  file is copied to ~/.knos/backups first. Then it starts the server once
  to prove it answers.""",
    "ask": """\
  knos ask "why did we drop redis?"
  knos ask "..." --in ~/work/api

  Answers are what was actually said or written - past agent sessions,
  commits, your instruction files, the code - each with its source. If a
  file in the answer is claimed by another agent, it says who has it.""",
    "claim": """\
  knos claim "the parser" -p src/parser/** -p tests/test_parser.py
  knos claim "fix handler_12" --for 60

  Other agents' edits to those files are refused by the edit guard until
  you run knos done or the claim lapses (30 minutes by default). Your own
  edits never are. Without -p, knos looks for exact file and symbol names
  in what you wrote; if none match, the claim is advisory: others are
  told, nothing is blocked.""",
    "done": """\
  knos done                  release all of your claims here
  knos done "the parser"     release one
  knos done --all            release every agent's (asks first)

  Only ever your own, unless you say --all.""",
    "point": """\
  knos point                 read what is new since last time, now

  knos reads a repo by itself the first time you ask, and catches up when
  something changed. This is the same thing on demand. It never deletes;
  to start over, knos reset --yes (which keeps a backup).""",
    "reset": """\
  knos reset --yes

  Starts this repo's memory over. The store is copied to ~/.knos/backups
  first, notes included.""",
    "status": """\
  knos status

  What knos has read, what is claimed and by whom, whether the edit guard
  is wired, and which store it uses.""",
    "remember": """\
  knos remember "we dropped redis because the cache was never the problem"

  Every agent you connect will know it, as your words. If the store has no
  room, it says so and writes nothing.""",
    "notes": "  knos notes\n\n  What has been written down.  Drop one:  knos forget <name>",
    "forget": "  knos forget \"deploy window\"\n\n  Archives a note so agents stop repeating it.",
    "private": """\
  knos private notes/salary.md

  That path stops reaching your agents: not blanked, not counted. You can
  still search it yourself. Already private: .env, keys, certificates,
  .ssh, .aws.""",
    "board": """\
  knos board

  A live page of this repo on 127.0.0.1 with a one-off token: claims,
  agents seen today, recent refusals, and spend when Pro is on.""",
    "bench": """\
  knos bench --out docs/BENCH.md

  Collisions, wrong refusals, recall and speed, against the simplest way
  of doing without knos. Runs in a temporary folder.""",
    "demo": "  knos demo\n\n  The whole product on a throwaway repo. Every line is a real call.",
    "worth": "  knos worth\n\n  Claims taken and released, and edits refused, counted from the record.",
    "spend": """\
  knos spend                 today, per agent and model
  knos spend --days 7

  Knos Pro. Read from Claude Code's and Codex's own logs, priced at API
  list prices. Nothing leaves the machine.""",
    "budget": """\
  knos budget set 20 --per day      one cap: model tokens + API payments
  knos budget set 5 --repo .        count and cap only this repo
  knos budget raise 10
  knos budget clear
  knos budget fund --agent claude 5 --chain tempo    an agent's own wallet
  knos budget agents                each wallet: cap, spent, balance
  knos budget sweep --agent claude --to <your address>

  Knos Pro. Past the cap, the edit guard refuses your agents' edits and
  says why, until a person raises it. An agent wallet holds only what you
  fund it with, so the chain itself stops it at that amount.""",
    "report": """\
  knos report                last 7 days
  knos report --days 30 --out report.md

  Knos Pro. Spend by agent and model, agents' API payments against their
  caps, and in this repo: claims taken and edits refused.""",
    "pay": """\
  knos pay https://api.example.com/paid --agent claude

  Knos Pro. Fetches an API; if it answers 402 Payment Required (MPP on
  Tempo, or x402 on Solana), pays it from that agent's own wallet, inside
  its cap. Agents get the same thing as the `pay` tool.""",
    "pro": """\
  knos pro                   status and plans
  knos pro buy               10 USDC / 30 days, from any Solana wallet
  knos pro buy --year        100 USDC / year
  knos pro buy --chain tempo pay with USDC.e or pathUSD on Tempo
  knos pro buy --network devnet     try it with devnet USDC (testnet: Tempo)
  knos pro activate <code>   or:  knos pro activate --tempo <tx>

  14 days free. Memory, claims and the guard are free forever (MIT).""",
    "serve": """\
  knos serve                          run the team server on 127.0.0.1:8766
  knos serve --host 0.0.0.0 --name knos.lan    on your network
  knos serve seat add alice           a seat and its token (shown once)
  knos serve budget 100 --per day     one cap for the whole team

  Knos Team. On each machine:  knos init --remote http://knos.lan:8766
  --token <seat token>. Claims, notes and spend are then shared: a claim
  on one machine blocks an edit on another. Leave: knos init --leave-team""",
    "compact": """\
  knos compact
  knos compact --older-than 7

  Makes room in Sibyl memory: drops notes forgotten long ago and gives the
  freed space back. Nothing an answer uses is lost. Past Sibyl's free 5 MB,
  Sibyl Pro has no cap:  sibyl upgrade""",
    "export": "  knos export\n\n  Writes .knos/decisions.md: decisions and current claims, to commit.",
    "restore": "  knos restore\n\n  Reads .knos/decisions.md back. Claims are not restored.",
    "who": "  knos who\n\n  Which agents close what they claim, and the hold that has earned them.",
}
PER_COMMAND["connect"] = PER_COMMAND["init"]
PER_COMMAND["guard"] = PER_COMMAND["init"]


def main() -> str:
    return MAIN


def for_command(name: str) -> str:
    return PER_COMMAND.get(name) or f"  No command called {name}.\n\n  See what there is:  knos help"
