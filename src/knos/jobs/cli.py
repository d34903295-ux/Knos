"""`knos jobs …` (hire) and `knos work` (be hired), from the command line."""

from __future__ import annotations

import json
import re
from pathlib import Path

import typer

UNITS = 1_000_000
_DUR = re.compile(r"^(\d+)([smhd]?)$")


def _seconds(text: str) -> int:
    m = _DUR.match(text.strip().lower())
    if not m:
        raise typer.BadParameter(f"{text!r}: use 90s, 30m, 2h or 1d")
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def _usdc(units: int) -> str:
    whole = f"{units / UNITS:,.6f}".rstrip("0")
    return (whole + "0" * (2 - len(whole.split(".")[1]))) + " USDC"


def register(app: typer.Typer, out, Stop) -> None:
    jobs = typer.Typer(add_completion=False, help="hire an AI agent and pay only for work you accept")
    app.add_typer(jobs, name="jobs")

    def _ctx():
        from . import net
        try:
            return net.ledger(), net.relay(), net.key()
        except net.Refused as why:
            raise Stop(str(why)) from None

    def _id(text: str) -> bytes:
        from . import net
        try:
            return net.resolve(text)
        except net.Refused as why:
            raise Stop(str(why)) from None

    def _chain(fn, *a):
        try:
            return fn(*a)
        except LookupError as why:
            raise Stop(str(why)) from None
        except RuntimeError as why:
            raise Stop(f"The escrow refused: {why}") from None

    @jobs.command("post")
    def post(title: str = typer.Argument(..., help="what you want done, in a few words"),
             task: str = typer.Option(None, "--task", help="the full brief"),
             task_file: Path = typer.Option(None, "--task-file", help="read the brief from a file"),
             price: float = typer.Option(..., "--price", help="what you pay in USDC, held in escrow until you accept"),
             kind: str = typer.Option("text", "--kind", help="python, csv, json, copy or text"),
             tests: Path = typer.Option(None, "--tests", help="python: a test file that imports `solution`"),
             expect: Path = typer.Option(None, "--expect", help="csv/json: the exact expected output"),
             must_include: list[str] = typer.Option(None, "--must-include"),
             must_not_include: list[str] = typer.Option(None, "--must-not-include"),
             max_words: int = typer.Option(None, "--max-words"),
             work: str = typer.Option("1h", "--work", help="time to deliver, else you are refunded"),
             review: str = typer.Option("24h", "--review", help="time you have to accept or reject"),
             share_memory: bool = typer.Option(False, "--share-memory",
                                               help="attach your saved preferences to this brief (anyone with the "
                                                    "relay can read them)"),
             yes: bool = typer.Option(False, "--yes")) -> None:
        """Post a job: the price goes into escrow, the brief to the relay. One signature."""
        from . import buyer_memory, market, net
        if kind not in market.KINDS:
            raise Stop(f"Unknown kind {kind!r}.", "Use: " + ", ".join(market.KINDS))
        body = task_file.read_text(encoding="utf-8") if task_file else (task or title)
        checks: dict = {}
        if tests:
            checks["tests"] = tests.read_text(encoding="utf-8")
        if expect:
            raw = expect.read_text(encoding="utf-8")
            checks["expected"] = json.loads(raw) if kind == "json" else raw
        if must_include:
            checks["must_include"] = list(must_include)
        if must_not_include:
            checks["must_not_include"] = list(must_not_include)
        if max_words:
            checks["max_words"] = max_words
        units = round(price * UNITS)
        if net.cluster() == "mainnet" and units > net.MAINNET_CAP_UNITS:
            raise Stop("Mainnet jobs are capped at 500 USDC until the escrow's external audit.")
        ledger, relay, key = _ctx()
        mem = buyer_memory.BuyerMemory(str(key.pubkey()))
        learned = mem.learn_from(body)
        prefs = mem.preferences() if share_memory else None
        if share_memory and prefs and not yes:
            out.print(f"These {len(prefs)} saved preferences go into the brief (readable by anyone with the relay):")
            for p in prefs:
                out.print(f"  - {p}", markup=False)
            if not typer.confirm("Attach them?", default=True):
                prefs = None
        brief = market.Brief(title, body, kind=kind, checks=checks or None, preferences=prefs or None)
        from .brief_lint import lint
        for warning in lint(brief, units, mem.preferences()):
            out.print(f"  note: {warning}", markup=False)
        jid = _chain(market.post, ledger, relay, key, brief, units, _seconds(work), _seconds(review))
        net.remember_job(jid, "buyer", title)
        out.print(f"[green]✓[/green] posted {jid.hex()[:10]}  {title}  {_usdc(units)} in escrow")
        if learned:
            out.print(f"  remembered {len(learned)} preference(s) for your next jobs")
        out.print(f"  when it is delivered:  knos jobs get {jid.hex()[:10]}")

    @jobs.command("list")
    def list_(mine: bool = typer.Option(False, "--mine", help="only jobs you posted or took")) -> None:
        """Open jobs on the network, or your own."""
        from . import market, net
        ledger, relay, key = _ctx()
        if mine:
            for h, meta in net.known_jobs().items():
                j = market.job(ledger, bytes.fromhex(h))
                state = j.state if j else "gone"
                amount = _usdc(j.amount) if j else ""
                out.print(f"{h[:10]}  {meta['role']:<6} {state:<9} {amount:>14}  {meta['title']}", markup=False)
            return
        rows = market.open_jobs(ledger, relay)
        if not rows:
            out.print("No open jobs right now.")
        for j, b in rows:
            out.print(f"{b.job_id[:10]}  {_usdc(j.amount):>14}  {b.kind:<6} {b.title}", markup=False)

    @jobs.command("get")
    def get(job: str = typer.Argument(..., help="the job id (or its first characters)"),
            save: Path = typer.Option(None, "--save", help="write the deliverable here")) -> None:
        """Open what the worker delivered (checked against its hash on chain) and run the brief's checks on it."""
        from . import checks, market
        ledger, relay, key = _ctx()
        jid = _id(job)
        data = _chain(market.fetch_delivery, ledger, relay, key, jid)
        j = market.job(ledger, jid)
        brief = market.Brief.decode(relay.get_brief(j.brief.hex()))
        ok, why = checks.run(brief.kind, brief.checks, data.decode("utf-8", "replace"), brief.preferences)
        if save:
            save.write_bytes(data)
            out.print(f"saved to {save}")
        else:
            out.print(data.decode("utf-8", "replace"), markup=False)
        out.print(("[green]✓[/green] " if ok else "[red]✗[/red] ") + why)
        out.print(f"Pay for it:  knos jobs accept {job}     Refuse it:  knos jobs reject {job} --reason \"…\"")

    @jobs.command("accept")
    def accept(job: str = typer.Argument(...)) -> None:
        """Accept the work: the worker is paid now (95%; 5% Knos fee)."""
        from . import market
        ledger, relay, key = _ctx()
        jid = _id(job)
        _chain(market.accept, ledger, key, jid)
        out.print(f"[green]✓[/green] accepted: {_usdc(market.job(ledger, jid).amount * 95 // 100)} paid to the worker")

    @jobs.command("reject")
    def reject(job: str = typer.Argument(...), reason: str = typer.Option("", "--reason")) -> None:
        """Reject the work inside the review window: your money comes back."""
        from . import buyer_memory, market
        ledger, relay, key = _ctx()
        _chain(market.reject, ledger, key, _id(job))
        learned = buyer_memory.BuyerMemory(str(key.pubkey())).learn_from(reason)
        out.print("[green]✓[/green] rejected: refunded in full")
        if learned:
            out.print(f"  remembered for next time: {'; '.join(learned)}", markup=False)

    @jobs.command("release")
    def release(job: str = typer.Argument(...)) -> None:
        """After the review window: anyone can pay the worker for work the buyer never answered."""
        from . import market
        ledger, relay, key = _ctx()
        _chain(market.release, ledger, key, _id(job))
        out.print("[green]✓[/green] released to the worker")

    @jobs.command("refund")
    def refund(job: str = typer.Argument(...)) -> None:
        """After the work deadline with nothing delivered: your money comes back."""
        from . import market
        ledger, relay, key = _ctx()
        _chain(market.refund, ledger, key, _id(job))
        out.print("[green]✓[/green] refunded")

    @jobs.command("prefs")
    def prefs(add: str = typer.Option(None, "--add"), forget: str = typer.Option(None, "--forget")) -> None:
        """Your standing preferences, kept on this machine (Sibyl), shared only per job with --share-memory."""
        from . import buyer_memory, net
        mem = buyer_memory.BuyerMemory(str(net.key().pubkey()))
        if add:
            mem.remember(add)
        if forget:
            mem.forget(forget)
        got = mem.preferences()
        out.print("\n".join(f"- {p}" for p in got) if got else "No saved preferences yet.", markup=False)

    @jobs.command("perks")
    def perks_cmd() -> None:
        """Sibyl Pro paid by Knos: a month for every $12 of fees on your released jobs (checkout simulated)."""
        from . import perks
        ledger, relay, key = _ctx()
        js = ledger.jobs()
        got = perks.claim(js, str(key.pubkey()))
        st = perks.status(js, str(key.pubkey()))
        out.print(f"fees paid {st['fees_paid_usdc']:.2f} USDC · Sibyl Pro months earned {st['months_earned']}, "
                  f"granted {st['months_granted']} · next at {st['next_month_at_usdc']:.2f} USDC", markup=False)
        if got:
            out.print(f"[green]✓[/green] {len(got)} month(s) of Sibyl Pro granted (SIMULATED: Sibyl's partner checkout "
                      "is not live yet; no money moved)")

    @jobs.command("relay")
    def relay_cmd(port: int = typer.Option(8787, "--port"), host: str = typer.Option("127.0.0.1", "--host"),
                  root: Path = typer.Option(None, "--dir")) -> None:
        """Run a relay: it stores briefs and sealed deliverables by their hash, and nothing else."""
        from .. import paths
        from .relay import serve
        srv = serve(root or paths.home() / "jobs" / "relay", host, port)
        out.print(f"relay on http://{host}:{port}  (share it with a tunnel: cloudflared tunnel --url http://{host}:{port})")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.shutdown()

    @jobs.command("serve")
    def serve_cmd(port: int = typer.Option(8788, "--port"), host: str = typer.Option("127.0.0.1", "--host"),
                  web: Path = typer.Option(None, "--web", help="also serve this static web app (default: ./web)"),
                  relay_dir: Path = typer.Option(None, "--relay-dir", help="also be the relay, storing here")) -> None:
        """Serve Solana Actions (Blinks), the network API, and optionally the web app and a relay, on one port."""
        from . import net, stats
        from .actions import Actions, serve
        ledger, relay, _ = _ctx()
        web = web or (Path("web") if Path("web/index.html").exists() else None)
        cap = net.MAINNET_CAP_UNITS if net.cluster() == "mainnet" else None
        srv = serve(Actions(ledger, relay, net.cluster(), cap), host, port, static_dir=web, relay_dir=relay_dir,
                    extra=stats.api(ledger, relay))
        out.print(f"Actions on http://{host}:{port}/actions.json  ({net.cluster()}, finality {ledger.commitment})",
                  markup=False)
        out.print(f"Public link:  cloudflared tunnel --url http://{host}:{port}   then share "
                  f"https://dial.to/?action=solana-action:https://<tunnel>/api/jobs/post", markup=False)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.shutdown()

    @jobs.command("stats")
    def stats_cmd(agents: bool = typer.Option(False, "--agents", help="every agent's record, from the chain")) -> None:
        """The network, from the escrow's accounts on chain: jobs, money paid, agents and their records."""
        from . import stats
        ledger, relay, _ = _ctx()
        js = ledger.jobs()
        if agents:
            for r in stats.agents(js, ledger.now()):
                acc = "—" if r["acceptance"] is None else f"{r['acceptance']:.0%}"
                out.print(f"{r['agent']}  paid {r['paid']}  rejected {r['rejected']}  expired {r['expired']}  "
                          f"acceptance {acc}  earned {r['earned_usdc']:.2f} USDC", markup=False)
            return
        out.print(json.dumps(stats.network(js), indent=1), markup=False)

    @app.command("work")
    def work(once: bool = typer.Option(False, "--once", help="one pass over the open jobs, then stop"),
             model: str = typer.Option(None, "--model", help="provider:model on your own key, e.g. groq:llama-3.3-70b-versatile"),
             kinds: str = typer.Option(",".join(("python", "csv", "json", "copy", "text")), "--kinds"),
             min_price: float = typer.Option(0.0, "--min-price", help="skip jobs paying less (USDC)"),
             every: float = typer.Option(5.0, "--every", help="seconds between polls")) -> None:
        """Be hired: take open jobs, do them with your own model key, deliver; paid when the buyer accepts."""
        from . import models, net
        from .worker import Worker
        try:
            m = models.from_env(model)
        except LookupError as why:
            raise Stop(str(why)) from None
        ledger, relay, key = _ctx()
        w = Worker(ledger, relay, key, m, kinds=tuple(k for k in kinds.split(",") if k),
                   min_price=round(min_price * UNITS), log=lambda s: out.print(s, markup=False),
                   on_delivered=lambda jid, title: net.remember_job(jid, "worker", title))
        out.print(f"working as {key.pubkey()} on {net.cluster()}  (Ctrl-C to stop)", markup=False)
        if once:
            w.once()
            return
        try:
            w.run(every)
        except KeyboardInterrupt:
            out.print("Stopped.")
