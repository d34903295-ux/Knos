"""`knos proof ...`: the same engine the Stop hook runs, by hand."""

from __future__ import annotations

import json
from pathlib import Path

import typer


def register(app: typer.Typer, out, Stop, repo_of) -> None:
    proof = typer.Typer(add_completion=False, help="AI agent work counts only when Knos proves it")
    app.add_typer(proof, name="proof")

    def _store(repo: Path):
        from . import history
        try:
            return history.SibylStore.for_repo(repo)
        except Exception:  # noqa: BLE001
            return history.NullStore()

    @proof.command("check")
    def check(claim: str = typer.Argument(..., help="what the agent says is done"),
              in_: str = typer.Option(None, "--in", help="the repo (default: this one)")) -> None:
        """Run every check this claim needs (and what this repo's history requires). Exit 1 if any fails."""
        from . import engine
        repo = repo_of(in_)
        v = engine.evaluate(repo, claim, _store(repo))
        if not v.results:
            out.print("That claims nothing checkable.")
            return
        out.print(v.explain(), markup=False)
        out.print("[green]proven[/green]" if v.ok else "[red]not proven[/red]")
        if not v.ok:
            raise typer.Exit(1)

    @proof.command("observe")
    def observe(sha: str = typer.Argument(...), check: str = typer.Argument(..., help="e.g. ci"),
                failed: bool = typer.Option(False, "--failed"), detail: str = typer.Option("", "--detail")) -> None:
        """Record later evidence about a commit (e.g. its CI failed after it was called done)."""
        from . import history
        repo = repo_of(None)
        history.observe(_store(repo), sha, check, not failed, detail)
        out.print(f"Recorded: {check} {'failed' if failed else 'passed'} at {sha[:8]}.")

    @proof.command("lint")
    def lint() -> None:
        """Claims this repo's evidence contradicts."""
        from . import history
        got = history.lint(_store(repo_of(None)))
        if not got:
            out.print("No claim here is contradicted by its evidence.")
        for x in got:
            out.print(f"  {x.sha[:8]}: claimed {', '.join(x.claimed) or 'done'}, but {x.failed} failed", markup=False)

    @proof.command("learn")
    def learn() -> None:
        """Turn every past false "done" into a check this repo now requires."""
        from . import history
        rules = history.learn(_store(repo_of(None)))
        for r in rules:
            out.print(f"  a {r['when']} claim now requires {r['require']}  ({r.get('because', '')})", markup=False)
        if not rules:
            out.print("No false done in this repo's history yet.")

    @proof.command("receipt")
    def receipt(claim: str = typer.Argument(...), publish: bool = typer.Option(False, "--publish",
                                                                                 help="attest it on Solana devnet")) -> None:
        """Prove the claim, then print the Merkle root of the evidence (and, with --publish, a devnet receipt)."""
        from . import checks, engine
        from . import receipt as rc
        repo = repo_of(None)
        v = engine.evaluate(repo, claim, _store(repo))
        if not v.ok or not v.results:
            out.print(v.explain(), markup=False)
            raise Stop("Not proven: no receipt for a claim Knos cannot prove.")
        ev = rc.evidence(v.results)
        root = rc.merkle_root(rc.leaves(ev))
        out.print(f"evidence root {root.hex()}")
        (repo / ".knos").mkdir(exist_ok=True)
        (repo / ".knos" / f"receipt-{root.hex()[:16]}.json").write_text(json.dumps(
            {"claim": claim, "commit": checks.head(repo), "evidence": ev, "root": root.hex()}, indent=1), "utf-8")
        if publish:
            from ..jobs import net
            from ..team import rpc
            sig, att = rc.publish(rpc.CLUSTERS["devnet"], net.key(), root, checks.head(repo), claim)
            out.print(f"receipt on devnet: {sig}\n  {rc.page_url(att)}", markup=False)
