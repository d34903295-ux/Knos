"""The knos command line.

    knos init                 the free Stop hook: before your coding agent stops, Knos checks what it says is done against the repository's own checks
    knos mcp                  paid bounties and claim checks for a coding agent, over MCP (knos init registers it)
    knos proof ...            the same checks by hand; and the judge GitHub runs on a bounty's pull request
    knos command | settle | review | check    what a repository's workflow runs: one command per job (knos.flow)
    knos bounty owner/repo#7  what is in escrow for an issue, and its state
    knos due <github login>   where a GitHub account is paid, what is held for it, and its record
    knos claim <address>      name the Solana address your GitHub account is paid at, in one command (uses your gh login)
    knos relay                carry GitHub-signed tokens to Solana (what the always-on worker runs; anyone can)
    knos keys                 the signing keys the verifier holds; exit 1 when one of the issuers' is missing or expiring
    knos balance ...          money a wallet sets aside for one GitHub owner's repositories: show, open, deposit, set, withdraw
    knos fund-wallet o/r#7 20 put a bounty on an issue straight from a wallet
    knos mainnet-check        every gate that must hold before mainnet, with its evidence
    knos status               is the second deployment running as designed? what to do when a line fails

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
                  help="Bounties that pay when the pull request is merged with the checks you named passing. Attested by a GitHub-signed workflow run, verified on Solana.")

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
    """Install the Stop hook for Claude Code and Codex: it compares what the agent says is done with what the repository's checks show. And register `knos mcp` with them,
    Cursor and Gemini CLI, so an agent can find paid bounties. Free; nothing leaves this machine except the public
    GitHub, PyPI and Solana lookups a claim or a tool needs."""
    from . import init as setup
    picked = [h.strip() for h in hosts.split(",") if h.strip()] if hosts else None
    rep = setup.undo(picked) if undo else setup.install(picked)
    for what in rep["removed"]:
        out.print(f"  removed {what} (an older Knos's, or one whose command is gone)", markup=False)
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
                  "pull request's claims agree with GitHub's record (knos_check_pr).")
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


def _register_flow() -> None:
    """The four commands a repository's workflow runs, one per job: knos command, settle, review, check. knos.flow holds
    what they do; nothing here but their options."""
    event_opt = typer.Option(..., "--event", help="the event GitHub handed the workflow (GITHUB_EVENT_PATH)")
    repo_opt = typer.Option(..., "--repo", help="owner/name (GITHUB_REPOSITORY)")

    def run(name: str, event: Path, repo: str, **more) -> None:
        from . import flow
        try:
            payload = json.loads(event.read_text(encoding="utf-8"))
        except (OSError, ValueError) as why:
            raise Stop(f"{event} is not the event GitHub handed the workflow: {why}") from None
        if repo.count("/") != 1:
            raise Stop("Name the repository as owner/name.")
        raise typer.Exit(getattr(flow, name)(flow.Run(repo, payload), **more))

    @app.command("command")
    def command(event: Path = event_opt, repo: str = repo_opt) -> None:
        """A comment, or a new issue: act on its `/knos` line (fund, tip, take, release, the rest) and reply."""
        run("command", event, repo)

    @app.command("settle")
    def settle(event: Path = event_opt, repo: str = repo_opt,
               tests: bool = typer.Option(False, "--tests", help="the job after the sandboxed judge passed: sign for the bounty "
                                                                 "paid by acceptance checks, for --pull at --head"),
               pull: int = typer.Option(0, "--pull", help="with --tests: the pull request the judge passed"),
               head: str = typer.Option("", "--head", help="with --tests: the commit it passed at; nothing is signed unless the "
                                                           "pull request is still at it"),
               issue: int = typer.Option(0, "--issue", help="with --tests: the issue whose acceptance checks were judged (default: "
                                                            "the one `knos review` named)")) -> None:
        """A push to the default branch, a `/knos settle` or `/knos tip` comment, or a workflow_dispatch with a pull
        request's number: pay what each merged pull request earned, and say so on it."""
        if not tests and (pull or head or issue):
            raise Stop("--pull, --head and --issue go with --tests: the job that follows the sandboxed judge.")
        run("settle", event, repo, **(dict(tests=True, pull=pull or None, head=head, issue=issue or None) if tests else {}))

    @app.command("review")
    def review(event: Path = event_opt, repo: str = repo_opt) -> None:
        """The "knos check" workflow finished for a pull request: one comment on it, kept up to date, saying whether
        it takes a bounty, what is missing and who would be paid."""
        run("review", event, repo)

    @app.command("check")
    def check(event: Path = event_opt, repo: str = repo_opt) -> None:
        """A pull request, read-only: its description's claims, the repository's rules, a funded issue's terms so
        far. The answer is the job's summary; exit 1 only for a false claim or a broken rule."""
        run("check", event, repo)


_register_flow()



# ---- money on Solana: relay, keys, balance, fund-wallet, bounty, due, claim ------------------------------------------
# One block. knos.settle.v2 is the second deployment, where new bounties go; knos.settle is the first, whose bounties
# finish where they were funded. Each command is thin: the relays, the worker and the claim hold the logic.

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


def _when(t: int) -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


def _ledger():
    from . import chain
    try:
        return chain.ledger()
    except chain.Refused as why:
        raise Stop(str(why)) from None


def _wallet(keypair: Path | None):
    from . import chain
    try:
        return chain.wallet(keypair)
    except chain.Refused as why:
        raise Stop(str(why)) from None


def _owner(who: str) -> int:
    """A GitHub owner's id: the number itself, or the id of the user or organisation with that login."""
    return int(who) if who.isdigit() else int(_github(f"users/{who}")["id"])


def _issue(where: str) -> tuple[str, int]:
    name, _, n = where.partition("#")
    if "/" not in name or not n.isdigit():
        raise Stop("Name the issue as owner/repo#number, e.g. octo/widgets#7.")
    return name, int(n)


def _mint(ledger, given: str | None):
    """(mint, its token program, its decimals): the mint named, or test USDC (Circle's devnet mint)."""
    from solders.pubkey import Pubkey

    from .settle.v2 import pay
    try:
        mint = Pubkey.from_string(given) if given else pay.USDC_DEVNET
    except ValueError:
        raise Stop(f"{given!r} is not a mint address.") from None
    info = ledger.infos([mint])[0]
    if info is None or info[0] not in (pay.TOKEN, pay.TOKEN_2022) or len(info[1]) < 82:
        raise Stop(f"{mint} is not a token mint on this cluster.")
    return mint, info[0], info[1][44]


def _units(text: str, decimals: int) -> int:
    import re
    m = re.fullmatch(r"([0-9]{1,12})(?:\.([0-9]+))?", text or "")
    if not m or len(m.group(2) or "") > decimals:
        raise Stop(f"An amount is digits with at most {decimals} decimals, like 20 or 12.5; {text!r} is not one.")
    return int(m.group(1)) * 10 ** decimals + int((m.group(2) or "").ljust(decimals, "0") or 0)


def _money(units: int, mint, decimals: int = 6) -> str:
    """An amount as people write it. On devnet every mint is test money; the two test USDC mints are called that."""
    from .settle.v2 import pay
    return f"{units / 10 ** decimals:,.2f} " + ("test USDC" if mint in (pay.USDC_DEVNET, pay.faucet_mint()) else f"of mint {mint}")


def _send(ledger, ixs, wallet) -> str:
    """One transaction, signed and paid for by the wallet. A refusal is said in the escrow's own words."""
    from .settle.v2 import relay
    try:
        return ledger.send(ixs, wallet)
    except Exception as why:  # noqa: BLE001 - whatever the cluster said, in one line
        words = relay.why_failed(why)
        if "no SOL" in words:
            words = f"this wallet ({wallet.pubkey()}) has no SOL for the transaction fee. On devnet: solana airdrop 1 {wallet.pubkey()} --url devnet"
        raise Stop(f"Solana refused it: {words}") from None


def _tx(sig: str) -> str:
    return f"  https://explorer.solana.com/tx/{sig}?cluster=devnet"


@app.command()
def relay(token_file: Path = typer.Option(None, "--token-file", "--token", help="relay this one token now and print the result as JSON"),
          terms_file: Path = typer.Option(None, "--terms-file", help="a fund token's terms JSON (as its `knos-terms:` line gave them)"),
          serve: float = typer.Option(None, "--serve", metavar="SECONDS", help="keep making passes for this long"),
          every: float = typer.Option(3.0, "--every", help="seconds from one pass to the next, with --serve")) -> None:
    """Carry GitHub-signed tokens to Solana and pay the transaction fees (KNOS_RELAY_KEY). A relayer decides nothing:
    the money goes where the token says. With no option: one pass over the tokens repositories posted, which is what
    the public worker does all day with --serve."""
    from . import chain
    from .proof import ghrelay
    if token_file is None:
        if serve:
            ghrelay.serve(serve, every)
        else:
            ghrelay.once()
        return
    text = token_file.read_text(encoding="utf-8")
    found, said = ghrelay.TOKEN.search(text), ghrelay.TERMS.search(text)      # the token alone, or the comment that carried it
    terms = terms_file.read_bytes().strip() if terms_file else said.group(1).encode() if said else None
    r = ghrelay.carry(_ledger(), chain.key(), found.group(2) if found else text.strip(), terms)
    if r.get("ok"):
        r["note"] = ghrelay.note(r)
    typer.echo(json.dumps(r))
    raise typer.Exit(0 if r.get("ok") else 1)


@app.command()
def keys() -> None:
    """Every signing key the second verifier holds, and whether each key GitHub and GitLab publish today verifies
    there. Exit 1 when one is missing, cannot be used, or expires within 7 days."""
    from .settle import relay as first
    from .settle.v2 import oidc
    from .settle.v2 import relay as second
    ledger = _ledger()
    now = ledger.now()
    names = {oidc.GITHUB: "GitHub", oidc.GITLAB: "GitLab"}
    published, wrong = {}, []
    for issuer, name in names.items():
        try:
            published.update({oidc.key_pda(issuer, n): (name, kid) for kid, n in oidc.jwks_keys(first.fetch_jwks(issuer))})
        except Exception as why:  # noqa: BLE001 - the issuer did not answer: its keys cannot be vouched for
            wrong.append(f"{name}'s key set could not be read ({type(why).__name__}: {why}), so its keys were not checked.")
    held = {addr: k for addr, k, _n in second.keys(ledger)}
    for addr, k in held.items():
        state = "ready" if k.state == 1 else "registered, its parameters not sent yet"
        today = f"published today as {published[addr][1]}" if addr in published else "not in the issuer's key set today"
        out.print(f"{names.get(k.issuer, k.issuer)}  {k.bits} bits  {state}  active from {_when(k.active_at)}  expires {_when(k.expires_at)}  "
                  f"{'approved' if k.approved else 'not approved'}  {'REVOKED' if k.revoked else 'not revoked'}  {today}  {addr}", markup=False)
    if not held:
        out.print("The second verifier holds no key on this cluster.")
    for addr, (name, kid) in published.items():
        k = held.get(addr)
        usable, why = oidc.key_usable(k, now)
        if not usable:
            wrong.append(f"{name}'s key {kid}: {why}")
        elif k.expires_at - now < 7 * 86_400:
            wrong.append(f"{name}'s key {kid} expires {_when(k.expires_at)}, in less than 7 days. Run the rotate workflow, so that Refresh is sent for it.")
    for line in wrong:
        out.print(line, markup=False)
    if wrong:
        raise typer.Exit(1)
    out.print(f"Every key the issuers publish today ({len(published)}) verifies on chain.")


balance_app = typer.Typer(add_completion=False, help="Money a wallet sets aside for bounties in one GitHub owner's repositories, spent by "
                                                     "GitHub-signed comments (/knos fund). The wallet is a Solana keypair file: --keypair, or KNOS_WALLET_KEY.")
app.add_typer(balance_app, name="balance")
_KEYPAIR = typer.Option(None, "--keypair", help="the wallet: a Solana keypair file (default: KNOS_WALLET_KEY)")
_MINT = typer.Option(None, "--mint", help="the token's mint (default: test USDC, Circle's devnet mint)")
_OWNER = typer.Argument(..., help="the GitHub user or organisation whose repositories spend it: a login, or its numeric id")


def _balance(ledger, wallet, owner: str, mint):
    """(the Balance's address, what is there) for this wallet, owner and mint. Refuses when there is none."""
    from .settle.v2 import pay
    at = pay.balance_pda(_owner(owner), wallet.pubkey(), mint)
    b = pay.read_balance(ledger.account(at))
    if b is None:
        raise Stop(f"This wallet has no balance for {owner} in that mint.", f"Open one: knos balance open {owner}")
    return at, b


@balance_app.command("show")
def balance_show(owner: str = _OWNER) -> None:
    """Every balance set aside for one GitHub owner's repositories: what it holds, who opened it, who may spend it."""
    from .settle.v2 import pay
    from .settle.v2 import relay as second
    ledger = _ledger()
    oid = _owner(owner)
    got = second.balances_for(ledger, oid)
    if not got:
        out.print(f"No balance is set aside for GitHub owner id {oid}. A wallet opens one: knos balance open {owner} --keypair FILE", markup=False)
        return
    mints = dict(zip([b.mint for _a, b, _h in got], ledger.accounts([b.mint for _a, b, _h in got])))
    for addr, b, holds in got:
        decimals = mints[b.mint][44] if mints.get(b.mint) else 6
        who = "the devnet faucet (any commenter in the owner's repositories spends it; nobody withdraws it)" if b.faucet else f"wallet {b.authority}"
        cap = f"at most {_money(b.cap_per_job, b.mint, decimals)} for one bounty" if b.cap_per_job else "no cap per bounty"
        out.print(f"{_money(holds, b.mint, decimals)}  balance {addr}  opened by {who}", markup=False)
        out.print(f"  {cap}; spenders besides the owner (GitHub ids): {', '.join(map(str, b.spenders)) or 'none'}; "
                  f"{_money(b.spent, b.mint, decimals)} put into bounties so far; add money by sending that token to {pay.baltok_pda(addr)}", markup=False)


@balance_app.command("open")
def balance_open(owner: str = _OWNER, cap: str = typer.Option(None, "--cap", help="the most one bounty may take (default: no cap)"),
                 spender: list[str] = typer.Option(None, "--spender", help="a GitHub login or id that may spend it by comment, besides the owner (up to 4)"),
                 mint: str = _MINT, keypair: Path = _KEYPAIR) -> None:
    """Open a balance for the repositories of one GitHub owner. It starts empty; `knos balance deposit` fills it."""
    from .settle.v2 import pay
    wallet, ledger = _wallet(keypair), _ledger()
    m, program, decimals = _mint(ledger, mint)
    oid, ids = _owner(owner), [_owner(s) for s in spender or []]
    at = pay.balance_pda(oid, wallet.pubkey(), m)
    if ledger.account(at) is not None:
        raise Stop(f"This wallet already has a balance for {owner} in that mint: {at}.", "Change its limits with: knos balance set")
    if len(ids) > 4:
        raise Stop("A balance has at most 4 spenders besides its owner.")
    sig = _send(ledger, [pay.open_balance_ix(wallet.pubkey(), oid, m, _units(cap, decimals) if cap else 0, ids, program)], wallet)
    out.print(f"Opened balance {at} for the repositories of GitHub owner id {oid}. It is empty.", markup=False)
    out.print(f"Fill it: knos balance deposit {owner} <amount>   (or send that token to {pay.baltok_pda(at)})", markup=False)
    out.print(_tx(sig), markup=False)


@balance_app.command("deposit")
def balance_deposit(owner: str = _OWNER, amount: str = typer.Argument(..., help="how much, like 100 or 12.5"), mint: str = _MINT, keypair: Path = _KEYPAIR) -> None:
    """Move money from the wallet's token account into its balance for that owner."""
    from solders.instruction import AccountMeta, Instruction

    from .settle.v2 import pay
    wallet, ledger = _wallet(keypair), _ledger()
    m, program, decimals = _mint(ledger, mint)
    at, _b = _balance(ledger, wallet, owner, m)
    units, source = _units(amount, decimals), pay.ata(wallet.pubkey(), m, program)
    held = ledger.account(source)
    if held is None or int.from_bytes(held[64:72], "little") < units:
        raise Stop(f"The wallet's token account ({source}) holds less than {_money(units, m, decimals)}.")
    transfer = Instruction(program, bytes([12]) + units.to_bytes(8, "little") + bytes([decimals]),      # TransferChecked
                           [AccountMeta(source, False, True), AccountMeta(m, False, False), AccountMeta(pay.baltok_pda(at), False, True),
                            AccountMeta(wallet.pubkey(), True, False)])
    sig = _send(ledger, [transfer], wallet)
    out.print(f"{_money(units, m, decimals)} added to balance {at}.", markup=False)
    out.print(_tx(sig), markup=False)


@balance_app.command("set")
def balance_set(owner: str = _OWNER, cap: str = typer.Option(None, "--cap", help="the most one bounty may take; 0 for no cap"),
                spender: list[str] = typer.Option(None, "--spender", help="who may spend it besides the owner: these replace the list (up to 4)"),
                no_spenders: bool = typer.Option(False, "--no-spenders", help="empty the list: only the owner spends it"),
                mint: str = _MINT, keypair: Path = _KEYPAIR) -> None:
    """Change a balance's cap per bounty and who may spend it. What is not named stays as it is."""
    from .settle.v2 import pay
    wallet, ledger = _wallet(keypair), _ledger()
    m, _program, decimals = _mint(ledger, mint)
    at, b = _balance(ledger, wallet, owner, m)
    if cap is None and not spender and not no_spenders:
        raise Stop("Say what to change: --cap AMOUNT (0 for none), --spender LOGIN (once for each), or --no-spenders.")
    ids = [] if no_spenders else [_owner(s) for s in spender] if spender else list(b.spenders)
    if len(ids) > 4:
        raise Stop("A balance has at most 4 spenders besides its owner.")
    units = _units(cap, decimals) if cap is not None else b.cap_per_job
    sig = _send(ledger, [pay.set_balance_ix(wallet.pubkey(), at, units, ids)], wallet)
    out.print(f"Balance {at}: {'at most ' + _money(units, m, decimals) + ' for one bounty' if units else 'no cap per bounty'}; "
              f"spenders besides the owner (GitHub ids): {', '.join(map(str, ids)) or 'none'}.", markup=False)
    out.print(_tx(sig), markup=False)


@balance_app.command("withdraw")
def balance_withdraw(owner: str = _OWNER, amount: str = typer.Argument(None, help="how much (default: everything unspent)"), mint: str = _MINT,
                     keypair: Path = _KEYPAIR) -> None:
    """Take unspent money back to the wallet that opened the balance. Money already in a bounty comes back to the
    balance when the bounty is refunded."""
    from .settle.v2 import pay
    wallet, ledger = _wallet(keypair), _ledger()
    m, program, decimals = _mint(ledger, mint)
    at, _b = _balance(ledger, wallet, owner, m)
    held = ledger.account(pay.baltok_pda(at))
    holds = int.from_bytes(held[64:72], "little") if held else 0
    units = _units(amount, decimals) if amount else holds
    if not units or units > holds:
        raise Stop(f"The balance holds {_money(holds, m, decimals)}." + ("" if holds else " There is nothing to withdraw."))
    me = wallet.pubkey()
    sig = _send(ledger, [pay.create_ata_ix(me, me, m, program), pay.withdraw_ix(me, at, m, units, token_program=program)], wallet)
    out.print(f"{_money(units, m, decimals)} back in the wallet's token account {pay.ata(me, m, program)}.", markup=False)
    out.print(_tx(sig), markup=False)


def _pinned(name: str, branch: str, given: str | None) -> tuple[str, str]:
    """(the repository that holds the workflows a job pins, their commit): as given (owner/name@commit), else what the
    repository's own .github/workflows/knos.yml calls prove.yml from."""
    import base64
    import re
    if given:
        m = re.fullmatch(r"([\w.-]+/[\w.-]+)@([0-9a-f]{40})", given)
        if not m:
            raise Stop("--workflow is owner/name@<40-character commit>: the repository and commit of the prove.yml whose signed run may pay this bounty.")
        return m.group(1), m.group(2)
    try:
        text = base64.b64decode(_github(f"repos/{name}/contents/.github/workflows/knos.yml?ref={branch}").get("content", "")).decode("utf-8", "replace")
    except Stop:
        text = ""
    m = re.search(r"uses:\s*([\w.-]+/[\w.-]+)/\.github/workflows/prove\.yml@([0-9a-f]{40})\b", text)
    if not m:
        raise Stop(f"{name} has no .github/workflows/knos.yml that calls prove.yml at a pinned commit, so no run there could pay this bounty.",
                   "Install Knos in the repository first, or name the workflows yourself: --workflow owner/name@<commit>")
    return m.group(1), m.group(2)


@app.command("fund-wallet")
def fund_wallet(where: str = typer.Argument(..., metavar="OWNER/REPO#ISSUE"), amount: str = typer.Argument(..., help="how much, like 20 or 12.5"),
                checks: str = typer.Option(None, "--checks", help="the checks that must pass, comma-separated; `none` for none (default: the repository's own)"),
                paths: str = typer.Option(None, "--paths", help="globs the pull request's files must match, comma-separated"),
                days: int = typer.Option(14, "--days", help="days until an unpaid bounty goes back to the wallet (1 to 90)"),
                reserve: int = typer.Option(7, "--reserve", help="days a `/knos take` reservation lasts"),
                workflow: str = typer.Option(None, "--workflow", metavar="OWNER/NAME@COMMIT", help="the workflows whose signed runs may pay it (default: the ones the repository installed)"),
                mint: str = _MINT, keypair: Path = _KEYPAIR) -> None:
    """Put a bounty on an issue straight from a wallet: anyone can add their own to any issue. It is paid when the
    pull request that closes the issue is merged and meets the terms shown; unpaid, it goes back to the wallet."""
    from . import commands, judge, terms
    from .settle.v2 import pay
    name, n = _issue(where)
    if not 1 <= days <= commands.MAX_DAYS:
        raise Stop(f"--days is from 1 to {commands.MAX_DAYS}.")
    wallet, ledger = _wallet(keypair), _ledger()
    m, program, decimals = _mint(ledger, mint)
    units = _units(amount, decimals)
    if not pay.MIN_AMOUNT <= units <= pay.MAX_AMOUNT:
        raise Stop(f"A bounty is from {_money(pay.MIN_AMOUNT, m, decimals)} to {_money(pay.MAX_AMOUNT, m, decimals)}.")
    repo = _github(f"repos/{name}")
    branch = str(repo["default_branch"])
    wf_repo, wf_sha = _pinned(name, branch, workflow)
    named = None if checks is None else () if checks.strip().lower() == "none" else tuple(c.strip() for c in checks.split(",") if c.strip())
    fund = commands.Fund(units, named, tuple(p.strip() for p in (paths or "").split(",") if p.strip()), days, reserve)
    try:
        head = judge.github(f"repos/{name}/branches/{branch}")["commit"]["sha"] if named is None or named else ""
        runs, statuses = terms.head_checks(name, head, judge.github, events=True) if head else ([], [])
        built = terms.build(fund, terms.required_checks(name, branch, judge.github) if head else [], runs, statuses)
        raw = terms.canonical(built.terms)
    except terms.Refused as why:
        raise Stop(str(why)) from None
    except (OSError, KeyError, TypeError) as why:
        raise Stop(f"GitHub did not answer for {name}'s checks ({why}), so the bounty's terms could not be fixed. Try again.") from None
    me = wallet.pubkey()
    job, source = pay.job_pda(int(repo["id"]), n, me), pay.ata(me, m, program)
    if ledger.account(job) is not None:
        raise Stop(f"This wallet already has a bounty on {where}: job {job}.")
    held = ledger.account(source)
    if held is None or int.from_bytes(held[64:72], "little") < units:
        raise Stop(f"The wallet's token account ({source}) holds less than {_money(units, m, decimals)}.")
    sig = _send(ledger, [pay.fund_wallet_ix(me, source, m, int(repo["id"]), n, units, wf_repo, wf_sha, raw, pay.MERGE, days * 86_400, program)], wallet)
    out.print(f"{_money(units, m, decimals)} is in escrow for {where}. Job {job}.", markup=False)
    for line in [*terms.describe(built.terms, built.source), *built.notes]:
        out.print(f"  {line}", markup=False)
    out.print(f"  Unpaid after {days} day{'s' if days != 1 else ''}, it goes back to this wallet. Only a signed run of prove.yml of {wf_repo} at {wf_sha[:12]} can pay it.", markup=False)
    out.print(_tx(sig), markup=False)


@app.command()
def bounty(where: str = typer.Argument(..., metavar="OWNER/REPO#ISSUE")) -> None:
    """What is in escrow for an issue, read from Solana devnet."""
    from .settle import relay as first
    from .settle.v2 import pay
    from .settle.v2 import relay as second
    name, n = _issue(where)
    repo_id = int(_github(f"repos/{name}")["id"])
    ledger = _ledger()
    now = ledger.now()
    jobs, old = second.jobs_for(ledger, repo_id, n), first.jobs_for(ledger, repo_id, n)
    if not jobs and not old:
        out.print(f"No bounty is in escrow for {where}. A maintainer funds one by commenting on the issue: /knos fund 20", markup=False)
        return
    for addr, j in jobs:
        net = j.amount - pay.fee_of(j.amount)
        state = (f"open: paid when the pull request that closes it is merged and meets its terms; goes back to its funder {_when(j.deadline)}" if j.state == "open"
                 else f"held for GitHub user id {j.payee_id}, who has named no wallet yet: {_money(net, j.mint)} waits for `knos claim <address>` until {_when(j.hold_until)}")
        out.print(f"{_money(j.amount, j.mint)}  {state}  job {addr}", markup=False)
    for addr, j in old:
        how = "paid when a maintainer merges the pull request that closes it" if j.mode == 0 else \
              "paid when its acceptance checks pass, after the review window"
        state = "open" if j.state == "open" else f"proven for GitHub user {j.author_id}; released in {max(0, j.pay_after - now)} s unless vetoed"
        out.print(f"{_usdc(j.amount)} test USDC  {state}  ({how}; refundable in {max(0, j.deadline - now) // 3600} h)  job {addr}  (first deployment)", markup=False)


@app.command()
def due(login: str = typer.Argument(..., help="a GitHub login")) -> None:
    """Where a GitHub account is paid, what is held for it, its record, and what the first deployment still owes it."""
    from .settle import pay as pay1
    from .settle import relay as first
    from .settle.v2 import pay
    from .settle.v2 import relay as second
    user = _github(f"users/{login}")
    uid = int(user["id"])
    ledger = _ledger()
    bound, rep = pay.read_bind(ledger.account(pay.bind_pda(uid))), pay.read_rep(ledger.account(pay.rep_pda(uid)))
    out.print(f"{login} (GitHub user id {uid}) is paid at {bound.wallet}." if bound else
              f"{login} (GitHub user id {uid}) has named no wallet: a payment whose pull request gives no address is held for them.", markup=False)
    for addr, j in second.held_for(ledger, uid):
        out.print(f"{_money(j.amount - pay.fee_of(j.amount), j.mint)} is held for issue #{j.issue} of repository id {j.repo_id} until {_when(j.hold_until)}  job {addr}", markup=False)
    s = lambda n: "" if n == 1 else "s"  # noqa: E731
    if rep.paid:
        out.print(f"Its record: paid for {rep.paid} pull request{s(rep.paid)} by {rep.funders} funder{s(rep.funders)}, {_usdc(rep.total)} in all, "
                  f"from {_when(rep.first)} to {_when(rep.last)}.", markup=False)
    if rep.test_paid:
        out.print(f"{rep.test_paid} payment{s(rep.test_paid)} in the faucet's test USDC, {_usdc(rep.test_total)} in all: test money, kept apart from the record.", markup=False)
    if rep.self_paid:
        out.print(f"{rep.self_paid} payment{s(rep.self_paid)} from bounties this account funded itself: kept apart from the record.", markup=False)
    owed = [(m, a) for m, a in first.dues_for(ledger, uid) if a]
    for m, a in owed:
        out.print(f"The first deployment holds {_usdc(a)} of {m} for {login}.", markup=False)
    old = pay1.read_rep(ledger.account(pay1.rep_pda(uid)))
    if old.paid_jobs:
        out.print(f"On the first deployment: paid for {old.paid_jobs} pull request(s) in {old.repositories} repositor{'y' if old.repositories == 1 else 'ies'}, "
                  f"{_usdc(old.total_paid)} in all.", markup=False)
    if not bound:
        out.print("Name a wallet, and what is held is sent there: knos claim <address>   (or in the browser: https://drexthealpha.github.io/Knos/#claim)", markup=False)
    if owed:
        out.print("Send what the first deployment holds to any address: knos claim --v1 <address>", markup=False)


@app.command()
def claim(address: str = typer.Argument(..., help="the Solana address your GitHub account is paid at"),
          v1: bool = typer.Option(False, "--v1", help="send what the first deployment holds for your account to this address instead"),
          repo: str = typer.Option(None, "--repo", help="with --v1: a repository you own to run the claim in (default: <you>/knos-claim)"),
          wait: int = typer.Option(600, "--wait", help="seconds to wait for a relayer")) -> None:
    """Name the Solana address your GitHub account is paid at. Uses your `gh` login: GitHub signs the claim in your own
    repository named knos-claim (made from a template with one workflow file if you have none), a relayer carries it
    to Solana, and what was held for you is sent to the address."""
    from . import claim as claiming
    say = lambda s: out.print(s, markup=False)  # noqa: E731
    try:
        if v1:
            claiming.claim(address, repo, wait, say=say)
        elif not claiming.bind(address, wait, say=say)["bound"]:
            raise typer.Exit(1)
    except claiming.Cannot as why:
        raise Stop(str(why)) from None


# ---- end of the money block ---------------------------------------------------------------------------------------


@app.command("mainnet-check")
def mainnet_check_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """Every gate that must hold before Knos moves real money, each with the evidence it was judged on. Exit 1
    while any gate fails."""
    from . import mainnet_check
    raise typer.Exit(mainnet_check.main(lambda s: out.print(s, markup=False), as_json=as_json))


# ---- knos status: the second deployment's health (one block, so it merges cleanly) ----------------

@app.command("status")
def status_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """Is the second deployment running as designed? Reads Solana (KNOS_RPC, default devnet) and GitHub's key list, and
    says for each of eight things whether it holds and, when it does not, what to do. Exit 1 while any fails."""
    from . import mainnet_check
    raise typer.Exit(mainnet_check.status_main(lambda s: out.print(s, markup=False), as_json=as_json))


# ---- end of knos status ------------------------------------------------------------------------


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
