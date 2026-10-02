"""`knos claim <address>`: one command that sends everything waiting under your GitHub account to a Solana address.

GitHub will sign "this user ran this by hand in their own repository and names <address>" only from a workflow in a
repository that user owns, so the claim has to start there. By hand that is five steps (add the workflow file to a
repository, open Actions, run it, type the address, wait). This does them with the `gh` command you are already logged
in with:

    1. reads what is due to your GitHub account on Solana; stops if nothing is
    2. uses the repository you name, or your `<login>/knos-claim` (created public, holding only the claim workflow,
       the first time)
    3. runs the workflow with the address; GitHub signs the token and posts it there
    4. waits for a relayer (Knos's always-on worker, or anyone) to carry it to Solana, and says what arrived

Nothing here holds a key and nothing can redirect the money: the token names the address, and knos-pay pays only it.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

TEMPLATE = Path(__file__).parent / "settle" / "knos-claim.yml"
WORKFLOW = ".github/workflows/knos-claim.yml"
ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


class Cannot(Exception):
    """Why the claim could not be made, in words."""


def _gh(*args: str, inp: str | None = None) -> str:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, input=inp, timeout=120, check=False)
    except FileNotFoundError:
        raise Cannot("`knos claim` uses GitHub's `gh` command, which is not installed here. Install it "
                     "(https://cli.github.com) and run `gh auth login`, or claim in the browser: "
                     "https://drexthealpha.github.io/Knos/#claim") from None
    if r.returncode:
        raise Cannot((r.stderr or r.stdout).strip()[:300] or f"gh {' '.join(args[:2])} failed")
    return r.stdout


def claim(address: str, repo: str | None = None, wait: float = 600, gh: Callable[..., str] = _gh, ledger=None,
          say: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Returns {"login", "repo", "created", "due": [(mint, amount)], "arrived": bool}. Raises Cannot with the reason."""
    from .settle import relay
    if not ADDRESS.fullmatch(address or ""):
        raise Cannot("That is not a Solana address.")
    me = json.loads(gh("api", "user"))
    login, uid = me["login"], int(me["id"])
    if ledger is None:
        from . import chain
        ledger = chain.ledger()
    due = [(str(m), a) for m, a in relay.dues_for(ledger, uid) if a]
    if not due:
        raise Cannot(f"Nothing is waiting for {login} (GitHub user id {uid}).")
    total = sum(a for _m, a in due)
    say(f"{total / 1_000_000:,.2f} USDC is waiting for {login}.")
    repo = repo or f"{login}/knos-claim"
    if repo.split("/")[0].lower() != login.lower():
        raise Cannot(f"The claim must run in a repository {login} owns; {repo} is not one. (GitHub signs the claim only "
                     "for the owner of the repository the workflow runs in.)")
    created = False
    try:
        gh("api", f"repos/{repo}")
    except Cannot:
        gh("repo", "create", repo, "--public", "--description", "Claims Knos payments to my GitHub account (one workflow file).")
        created = True
        say(f"Created {repo} (public; it holds only the claim workflow).")
    try:
        gh("api", f"repos/{repo}/contents/{WORKFLOW}")
    except Cannot:
        body = {"message": "Add the Knos claim workflow", "content": base64.b64encode(TEMPLATE.read_bytes()).decode()}
        gh("api", "-X", "PUT", f"repos/{repo}/contents/{WORKFLOW}", "--input", "-", inp=json.dumps(body))
        say(f"Added {WORKFLOW} to {repo}.")
    last = None
    for _ in range(12):          # GitHub takes a few seconds to see a workflow file it was just given
        try:
            gh("workflow", "run", "knos-claim.yml", "-R", repo, "-f", f"address={address}")
            last = None
            break
        except Cannot as why:
            last = why
            sleep(5)
    if last is not None:
        raise Cannot(f"GitHub would not start the claim workflow in {repo}: {last}")
    say(f"GitHub is signing the claim in {repo}; waiting for a relayer to carry it to Solana (up to {int(wait // 60)} min).")
    end = time.monotonic() + wait
    arrived = False
    while time.monotonic() < end:
        sleep(10)
        if not any(a for _m, a in relay.dues_for(ledger, uid)):
            arrived = True
            break
    if arrived:
        say(f"Sent {total / 1_000_000:,.2f} USDC to {address}.")
    else:
        say(f"Not arrived yet. The run and its verdict are at https://github.com/{repo}/actions; `knos due {login}` shows what is still waiting.")
    return {"login": login, "repo": repo, "created": created, "due": due, "arrived": arrived}
