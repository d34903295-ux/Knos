"""The knos command line.

    knos init                 the free Stop hook: your coding agent cannot say done while Knos cannot prove it
    knos mcp                  paid bounties and claim checks for a coding agent, over MCP (knos init registers it)
    knos proof ...            the same checks by hand; and the judge GitHub runs on a bounty's pull request
    knos bounty owner/repo#7  what is in escrow for an issue, and its state
    knos due <github login>   what is waiting for a GitHub account to claim
    knos claim <address>      send it to a Solana address, in one command (uses your gh login)
    knos relay                carry GitHub-signed tokens to Solana (what the always-on worker runs; anyone can)
    knos mainnet-check        every gate that must hold before mainnet, with its evidence

Every failure is one line that says what happened.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import typer
from rich.console import Console

from . import version

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False,
                  help="AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed.")

for _stream in (sys.stdout, sys.stderr):   # a Windows console's code page cannot encode everything a PR says
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

out = Console(highlight=False, soft_wrap=True)


class Stop(Exception):
    """A refusal, in words: what happened, and (optionally) the command that fixes it."""

    def __init__(self, said: str, fix: str = ""):
        super().__init__(said)
        self.said, self.fix = said, fix


def _repo(given: str | None = None) -> Path:
    here = Path(given).resolve() if given else Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return d
    raise Stop(f"{here} is not inside a git repository.", "Run this inside the repository, or pass --in PATH.")


def _version(show: bool) -> None:
    if show:
        out.print(f"knos {version()}", markup=False)
        raise typer.Exit()


@app.callback()
def _main(_v: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="print the version")) -> None:
    pass


@app.command()
def init(undo: bool = typer.Option(False, "--undo", help="remove what knos init added"),
         hosts: str = typer.Option(None, "--hosts",
                                   help="claude,codex,cursor,gemini (default: every one installed here)")) -> None:
    """Install the Stop hook for Claude Code and Codex: no "done" Knos cannot prove. And register `knos mcp` with them,
    Cursor and Gemini CLI, so an agent can find paid bounties. Free; nothing leaves this machine except the public
    GitHub, PyPI and Solana lookups a claim or a tool needs."""
    from . import init as setup
    picked = [h.strip() for h in hosts.split(",") if h.strip()] if hosts else None
    rep = setup.undo(picked) if undo else setup.install(picked)
    for what in rep["removed"]:
        out.print(f"  removed {what} (it moved to knos-labs in 0.3.10)", markup=False)
    for host, where in rep["done"]:
        out.print(f"  {'removed from' if undo else 'Stop hook for'} {host}: {where}", markup=False)
    for host, where in rep["mcp"]:
        out.print(f"  {'removed from' if undo else 'MCP server for'} {host}: {where}", markup=False)
    for host, why in rep["skipped"]:
        out.print(f"  skipped {host}: {why}", markup=False)
    if undo:
        out.print("Knos is out of your agents' settings." if rep["done"] or rep["mcp"] or rep["removed"]
                  else "Nothing of Knos's was installed.")
        return
    if not rep["done"] and not rep["mcp"]:
        raise Stop("No coding agent Knos knows (Claude Code, Codex, Cursor, Gemini CLI) is installed here.",
                   "Install one, or name it: knos init --hosts claude")
    if rep["done"]:
        out.print("From the next session, when your agent says tests pass, CI is green, it shipped or it is done, Knos "
                  "runs that check itself before the agent may stop.")
    if rep["mcp"]:
        out.print("From the next session your agent can also ask Knos for paid bounties (knos_bounties) and whether a "
                  "pull request's claims are true (knos_check_pr).")
    out.print("Undo: knos init --undo")


@app.command("hook", hidden=True, context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def hook_cmd(ctx: typer.Context, which: str = typer.Argument(...)) -> None:
    """What the installed hook calls. A hook name from an older version does nothing, quietly."""
    if which == "proof":
        from .proof import hook
        raise typer.Exit(hook.main_proof(list(ctx.args)))
    raise typer.Exit(0)


@app.command()
def mcp() -> None:
    """Paid bounties and claim checks for a coding agent, over MCP on stdio. Read-only: no key, no wallet. An agent
    host starts this; `knos init` registers it."""
    from . import mcp as server
    raise typer.Exit(server.main())


def _register_proof() -> None:
    from .proof.cli import register
    register(app, out, Stop, _repo)


_register_proof()


def _github(path: str) -> dict:
    import os
    req = urllib.request.Request(f"https://api.github.com/{path}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "knos"})
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - api.github.com
            return json.loads(resp.read())
    except OSError as why:
        raise Stop(f"GitHub did not answer for {path}: {why}") from None


def _usdc(units: int) -> str:
    return f"{units / 1_000_000:,.2f}"


@app.command()
def bounty(where: str = typer.Argument(..., metavar="OWNER/REPO#ISSUE")) -> None:
    """What is in escrow for an issue, read from Solana devnet."""
    from . import chain
    from .settle import relay
    if "#" not in where or "/" not in where:
        raise Stop("Name the issue as owner/repo#number, e.g. octo/widgets#7.")
    name, _, n = where.partition("#")
    if not n.isdigit():
        raise Stop("Name the issue as owner/repo#number, e.g. octo/widgets#7.")
    repo_id = int(_github(f"repos/{name}")["id"])
    ledger = chain.ledger()
    jobs = relay.jobs_for(ledger, repo_id, int(n))
    if not jobs:
        out.print(f"No bounty is in escrow for {where}. A maintainer funds one by commenting on the issue: "
                  f"/knos bounty 50", markup=False)
        return
    now = ledger.now()
    for addr, j in jobs:
        how = "paid when a maintainer merges the pull request that closes it" if j.mode == 0 else \
              "paid when its acceptance checks pass, after the review window"
        state = "open" if j.state == "open" else f"proven for GitHub user {j.author_id}; released in {max(0, j.pay_after - now)} s unless vetoed"
        left = max(0, j.deadline - now)
        out.print(f"{_usdc(j.amount)} USDC  {state}  ({how}; refundable in {left // 3600} h)  job {addr}", markup=False)


@app.command()
def due(login: str = typer.Argument(..., help="a GitHub login")) -> None:
    """What is waiting for a GitHub account, and how to claim it."""
    from . import chain
    from .settle import pay, relay
    user = _github(f"users/{login}")
    ledger = chain.ledger()
    got = [(m, a) for m, a in relay.dues_for(ledger, int(user["id"])) if a]
    rep = pay.read_rep(ledger.account(pay.rep_pda(int(user["id"]))))
    if not got:
        out.print(f"Nothing is waiting for {login} (GitHub user id {user['id']}).", markup=False)
    for mint, amount in got:
        out.print(f"{_usdc(amount)} of {mint} is waiting for {login} (GitHub user id {user['id']}).", markup=False)
    if rep.paid_jobs:
        out.print(f"Paid for {rep.paid_jobs} pull request(s) in {rep.repositories} repositor{'y' if rep.repositories == 1 else 'ies'}: "
                  f"{_usdc(rep.total_paid)} in all.", markup=False)
    if got:
        out.print("Claim it to any Solana address: knos claim <address>   (or in the browser: "
                  "https://drexthealpha.github.io/Knos/#claim)", markup=False)


@app.command()
def claim(address: str = typer.Argument(..., help="the Solana address that should receive it"),
          repo: str = typer.Option(None, "--repo", help="a repository you own to run the claim in (default: <you>/knos-claim, created if missing)"),
          wait: int = typer.Option(600, "--wait", help="seconds to wait for the money to arrive")) -> None:
    """Send everything waiting under your GitHub account to a Solana address. Uses your `gh` login: GitHub signs the
    claim in a repository you own (yours/knos-claim, created public with one workflow file if you have none)."""
    from . import claim as claiming
    try:
        claiming.claim(address, repo, wait, say=lambda s: out.print(s, markup=False))
    except claiming.Cannot as why:
        raise Stop(str(why)) from None


@app.command()
def relay(token: Path = typer.Option(None, "--token", help="relay this one token file instead of scanning GitHub")) -> None:
    """Carry GitHub-signed tokens to Solana and pay the transaction fees. A relayer decides nothing: the money goes
    where the token says. With no --token: one pass over the tokens repositories posted (the always-on worker)."""
    from . import chain
    from .proof import ghrelay
    if token is None:
        ghrelay.once()
        return
    from .settle import relay as settle
    r = settle.submit(chain.ledger(), chain.key(), token.read_text(encoding="utf-8").strip())
    if not r.get("ok"):
        raise Stop(f"Refused: {r.get('why')}")
    out.print(ghrelay.note(r), markup=False)
    for s in r.get("sigs", [])[-3:]:
        out.print(f"  https://explorer.solana.com/tx/{s}?cluster=devnet", markup=False)


@app.command("mainnet-check")
def mainnet_check_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """Every gate that must hold before Knos moves real money, each with the evidence it was judged on. Exit 1
    while any gate fails."""
    from . import mainnet_check
    raise typer.Exit(mainnet_check.main(lambda s: out.print(s, markup=False), as_json=as_json))


def main(argv: list[str] | None = None) -> int:
    """The console script. Errors are one line, never a traceback."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        rc = app(args=args, standalone_mode=False, prog_name="knos")
        return int(rc) if isinstance(rc, int) else 0
    except Stop as why:
        out.print(why.said, markup=False)
        if why.fix:
            out.print(why.fix, markup=False)
        return 1
    except typer.Exit as e:
        return int(e.exit_code or 0)
    except typer.Abort:
        out.print("Stopped.")
        return 1
    except KeyboardInterrupt:
        out.print("Stopped.")
        return 130
    except Exception as e:  # usage errors and anything unforeseen: one line
        if hasattr(e, "format_message") and type(e).__name__.endswith(("UsageError", "BadParameter", "NoSuchOption",
                                                                       "MissingParameter", "BadOptionUsage",
                                                                       "ClickException", "BadArgumentUsage")):
            out.print(e.format_message(), markup=False)
            out.print("See:  knos --help", markup=False)
            return 2
        out.print(f"knos stopped: {type(e).__name__}: {e}", markup=False)
        return 1


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
