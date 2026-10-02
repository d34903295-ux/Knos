"""`knos proof ...`: the engine the Stop hook runs, by hand; and the judge prove.yml runs on GitHub."""

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
        except Exception:  # noqa: BLE001 - no store: still prove, just without memory
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

    @proof.command("run")
    def run(toml: Path = typer.Option(None, "--toml", help="default: .knos/proof.toml in the repo"),
            in_: str = typer.Option(None, "--in", help="the repo the checks run in (default: this one)")) -> None:
        """Run every [[check]] in .knos/proof.toml, whatever the claim. Exit 1 if any fails or none is defined."""
        from . import checks, engine
        repo = repo_of(in_)
        cfg = engine.load(toml) if toml else engine.config(repo)
        specs = [c for c in cfg.get("check", []) or [] if c.get("name") and c.get("run")]
        if not specs:
            raise Stop(cfg.get("_error") or "No [[check]] with a name and a run command: nothing to prove.")
        results = [checks.custom(repo, c["name"], c["run"]) for c in specs]
        for r in results:
            out.print(f"{'ok ' if r.ok else 'NO '} {r.name}: {r.detail}", markup=False, emoji=False)
        if not all(r.ok for r in results):
            out.print("[red]not proven[/red]")
            raise typer.Exit(1)
        out.print("[green]proven[/green]")

    @proof.command("checks-hash")
    def checks_hash(dir_: Path = typer.Option(..., "--dir", help="e.g. .knos/acceptance/<issue>")) -> None:
        """Print the sha256 of an acceptance bundle (sorted "path\\0sha256(content)\\n" lines), as fixed on chain."""
        from .. import judge
        try:
            out.print(judge.checks_hash(dir_), markup=False)
        except ValueError as why:
            raise Stop(str(why)) from None

    def _say(v: dict, evidence: Path | None) -> None:
        if evidence:
            evidence.write_text(json.dumps(v, indent=1, default=str), encoding="utf-8")
        for r in v["evidence"].get("required_by_history", []):
            out.print(f"REQUIRED by this repo's history: {r}", markup=False, emoji=False)
        who = v["evidence"].get("payee") or {}
        if who.get("id"):
            out.print(f"paid to @{who.get('login')} (GitHub user id {who['id']}): {who['why']}", markup=False, emoji=False)
        for r in v["reasons"]:
            out.print(f"NO  {r}", markup=False, emoji=False)
        if v["checks_hash"]:
            out.print(f"checks_hash {v['checks_hash']}", markup=False)
        if not v["passed"]:
            out.print("[red]not proven[/red]")
            raise typer.Exit(1)
        out.print("[green]proven[/green]")

    def _inputs(store, diff, body_file, checks_file, repo_name, head, wait):
        from .. import judge
        from . import history
        st = history.SibylStore.local(store) if store else history.NullStore()
        diff_text = diff.read_text(encoding="utf-8", errors="replace") if diff else None
        body = body_file.read_text(encoding="utf-8", errors="replace") if body_file else ""
        runs = None
        if checks_file:
            got = json.loads(checks_file.read_text(encoding="utf-8"))
            runs = got.get("check_runs", []) if isinstance(got, dict) else got
        elif head and repo_name:
            runs = judge.check_runs(repo_name, head, wait=wait, body=body)
        return st, diff_text, body, runs

    def _who(event, repo_name: str, issue: str, issue_file):
        """(the pull request, the issue, who is paid) from the pull_request event GitHub hands the workflow; three
        Nones without one. The issue is read from GitHub for its assignees; when GitHub does not answer, no assignment
        is held against the pull request."""
        from .. import judge
        if not event:
            return None, None, None
        pull = json.loads(event.read_text(encoding="utf-8")).get("pull_request") or {}
        message = ""
        if (pull.get("user") or {}).get("type") == "Bot" and repo_name and (pull.get("head") or {}).get("sha"):
            try:
                message = judge.github(f"repos/{repo_name}/commits/{pull['head']['sha']}")["commit"]["message"]
            except Exception:  # noqa: BLE001 - the description or the assignee may still name the person
                message = ""
        issue_data = None
        if issue_file:
            issue_data = json.loads(issue_file.read_text(encoding="utf-8"))
        elif repo_name and str(issue).isdigit():
            try:
                issue_data = judge.github(f"repos/{repo_name}/issues/{issue}")
            except Exception:  # noqa: BLE001
                issue_data = None
        return pull, issue_data, judge.payee(pull, message)

    common = dict(
        event=typer.Option(None, "--event", help="the pull_request event (GITHUB_EVENT_PATH): who is paid, and the issue's assignment"),
        issue_file=typer.Option(None, "--issue-file", help="the issue as GitHub's API gives it (else fetched)"),
        diff=typer.Option(None, "--diff", help="the pull request's unified diff from the base"),
        evidence=typer.Option(None, "--evidence", help="write the evidence JSON here"),
        store=typer.Option(None, "--store", help="a directory the judge remembers in (Sibyl's local store: <dir>/sibyl.db)"),
        repo_name=typer.Option("", "--repo", help="owner/name"),
        agent=typer.Option("", "--agent", help="the pull request's author"),
        body_file=typer.Option(None, "--body-file", help="the pull request's description"),
        checks_file=typer.Option(None, "--checks-file", help="the head commit's check runs, as JSON (else fetched)"),
        head=typer.Option("", "--head", help="the head commit, to fetch its check runs from GitHub"),
        wait=typer.Option(0, "--wait", help="seconds to wait for the commit's other checks to finish"),
    )

    @proof.command("gate")
    def gate(base: Path = typer.Option(..., "--base", help="the base branch checkout"),
             issue: str = typer.Option("", "--issue"),
             diff: Path = common["diff"], evidence: Path = common["evidence"], store: Path = common["store"],
             repo_name: str = common["repo_name"], agent: str = common["agent"], body_file: Path = common["body_file"],
             checks_file: Path = common["checks_file"], head: str = common["head"], wait: int = common["wait"],
             event: Path = common["event"], issue_file: Path = common["issue_file"],
             strict: bool = typer.Option(False, "--strict", help="at the merged commit: a claim that cannot be checked is refused")) -> None:
        """A pull request, running none of its code: the repo's rules, what its history requires, and whether the
        description's "tests pass" is true at the head commit; for a bounty's pull request also who is paid and the
        issue's assignment. Exit 1 unless it passes."""
        from .. import judge
        st, diff_text, body, runs = _inputs(store, diff, body_file, checks_file, repo_name, head, wait)
        pull, issue_data, paid = _who(event, repo_name, issue, issue_file)
        _say(judge.gate(base, diff_text, st, repo_name or None, agent or None, body, runs, issue, pull, issue_data, paid,
                        strict), evidence)

    @proof.command("payee")
    def payee(event: Path = typer.Option(..., "--event", help="the pull_request event (GITHUB_EVENT_PATH)"),
              repo_name: str = common["repo_name"]) -> None:
        """Print the GitHub user id a pull request's bounty is paid to: its author, or the person who ran the bot
        that opened it. Exit 1, saying why, when nobody can be named."""
        _pull, _issue, paid = _who(event, repo_name, "", None)
        if not paid or not paid.get("id"):
            raise Stop((paid or {}).get("why") or "not a pull request event")
        print(paid["id"])

    @proof.command("judge")
    def judge_cmd(base: Path = typer.Option(..., "--base", help="the base branch checkout"),
                  pr: Path = typer.Option(..., "--pr", help="the pull request head source"),
                  issue: str = typer.Option(..., "--issue", help="the acceptance bundle: .knos/acceptance/<issue>/"),
                  changed: Path = typer.Option(None, "--changed", help="file listing the PR's changed paths"),
                  setup: str = typer.Option("", "--setup", help="shell command that installs a tree's dependencies"),
                  sandbox: str = typer.Option("auto", "--sandbox", help="auto, require or off"),
                  diff: Path = common["diff"], evidence: Path = common["evidence"], store: Path = common["store"],
                  repo_name: str = common["repo_name"], agent: str = common["agent"],
                  body_file: Path = common["body_file"], checks_file: Path = common["checks_file"],
                  head: str = common["head"], wait: int = common["wait"],
                  event: Path = common["event"], issue_file: Path = common["issue_file"]) -> None:
        """Tests mode: the gate, then the funder's acceptance checks in a sandbox (fail on the base, pass on the pull
        request). Exit 1 unless it passes."""
        from .. import judge
        from . import engine
        cfg = dict(engine.config(base))
        cfg["issue"] = issue
        names = None
        if changed:
            names = [x.strip() for x in changed.read_text(encoding="utf-8").splitlines() if x.strip()]
        st, diff_text, body, runs = _inputs(store, diff, body_file, checks_file, repo_name, head, wait)
        pull, issue_data, paid = _who(event, repo_name, issue, issue_file)
        _say(judge.judge_with_rules(base, pr, cfg, names, diff_text, st, repo_name or None, agent or None, body, runs,
                                    setup or None, sandbox, pull, issue_data, paid), evidence)

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
