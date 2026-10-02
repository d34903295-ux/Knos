"""The zero-secret GitHub relay: the always-on worker (worker.yml) carries the tokens that repositories' own workflows
minted to Solana, and pays the gas. Anyone else can run it too; a relayer decides nothing. Each relayer uses a key of
its own (a token's account on chain belongs to the key that carried it, so two relayers never touch each other's; two
runs sharing one key do, and the loser of that race tries again on its next pass).

Transport (caller -> worker). A repo that installed Knos (examples/knos-workflow.yml) has NO Knos secret, so it cannot
call the Knos repo or its API with auth. Its workflow posts the GitHub Actions OIDC token it minted as a comment with
its own GITHUB_TOKEN:

    knos-fund: <jwt>      on the issue, after a maintainer's `/knos bounty <amount>`
    knos-veto: <jwt>      on the issue, after a maintainer's `/knos veto`
    knos-proof: <jwt>     on the pull request, when it is merged (or, in tests mode, when the acceptance checks pass)
    knos-claim: <jwt>     on an issue in a repository the claimer owns (examples/knos-claim.yml)
    knos-key: <jwt>       on an issue in drexthealpha/knos-oidc-rotate (a new issuer key, attested by GitHub)

Posting the token in public is acceptable ONLY because it is not a bearer credential here: its audience names one
action (see knos.settle.pay's audiences), knos-pay accepts it for at most an hour and has a replay guard per action,
and it is valid only from the workflow commit the bounty pinned. Whoever relays it first only pays the gas; the money
goes where the audience says.

Discovery (worker finds the comments). Public reads, no install: one GitHub issue search for the word every token
comment carries (`knosrelay`), plus the repos in KNOS_RELAY_REPOS, plus every recently pushed repo of an owner the relay has served before (kept in KNOS_HOME),
plus the rotate repository. For each repo it reads issue comments since the last scan (REST, public).

Result (worker -> caller). The worker cannot write to other repos. It appends one line per relayed token to the open
issue labelled `knos-relay` in its own repository (its own GITHUB_TOKEN can do that):

    knos-relay <kind> <owner/repo>#<n> <token id> ok sig=<s1>[,<s2>...] note=<what happened, in words>
    knos-relay <kind> <owner/repo>#<n> <token id> fail <reason>

The caller's last job polls that issue (public) for its token id and comments the verdict with its own token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TOKEN = re.compile(r"knos-(proof|fund|veto|claim|key):\s*(eyJ[\w-]+\.[\w-]+\.[\w-]+)")
MARK = "knosrelay"   # one word every token comment carries, so one search finds them all
ROTATE_REPO = "drexthealpha/knos-oidc-rotate"
RELAY_KIND = {"proof": "pay"}   # the comment marker says proof; knos.settle.relay calls that audience pay
LOG_LABEL = "knos-relay"
# The repository the relay keeps its public log in, and whose own issues it always scans: the one whose worker.yml runs
# it (worker.yml passes its own name), Knos's by default. relay.yml polls the log of the repository it was called from.
HOME_REPO = os.environ.get("KNOS_RELAY_LOG_REPO") or "drexthealpha/Knos"
MAX_TRIES = 12   # failed passes a token gets when the failure may clear (the cluster dropped it, a twin run)


def claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def token_id(jwt: str) -> str:
    """What the relay log names a token by (never the token itself)."""
    return hashlib.sha256(jwt.strip().encode()).hexdigest()[:16]


def checks_hash(root: Path) -> str:
    """sha256 over the acceptance directory: for each file, sorted by its posix path relative to `root`,
    "<path>\\0<sha256 hex of the bytes>\\n". fund.yml computes the same with the identical inline Python."""
    h = hashlib.sha256()
    for f in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        h.update(f"{f.relative_to(root).as_posix()}\0{hashlib.sha256(f.read_bytes()).hexdigest()}\n".encode())
    return h.hexdigest()


# ---- GitHub (gh CLI; GH_TOKEN is the worker's own GITHUB_TOKEN, used only for rate limits and its own log) --------

def gh(*args: str, inp: str | None = None) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=inp, timeout=60, check=False)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def _api(path: str) -> list | dict:
    return json.loads(gh("api", "-X", "GET", path) or "null")


def discover(since: str, state: dict, getter=_api) -> set[str]:
    repos = {r.strip() for r in os.environ.get("KNOS_RELAY_REPOS", "").split(",") if "/" in r}
    repos.add(ROTATE_REPO)
    try:
        res = getter(f"search/issues?q=%22{MARK}%22+in:comments+updated:%3E={since[:10]}&sort=updated&order=desc&per_page=50")
        for it in res.get("items", []):
            repos.add("/".join(it["repository_url"].split("/")[-2:]))
    except Exception:  # noqa: BLE001, S110 - search is one source of several
        pass
    for owner in set(state.get("owners", [])) | {HOME_REPO.split("/")[0]}:
        try:
            for r in getter(f"users/{owner}/repos?sort=pushed&per_page=30"):
                if r.get("pushed_at", "") >= since[:10]:
                    repos.add(r["full_name"])
        except Exception:  # noqa: BLE001, S110
            pass
    repos.add(HOME_REPO)     # Knos funds its own issues the same way, and calls the rotate workflow from here
    return repos


def found(repo: str, since: str, getter=_api) -> list[tuple[str, int, str, str]]:
    """(kind, issue/PR number, jwt, comment author) for every marker comment in `repo` since `since`."""
    out = []
    for c in getter(f"repos/{repo}/issues/comments?since={since}&per_page=100&sort=created&direction=desc"):
        for kind, jwt in TOKEN.findall(c.get("body") or ""):
            out.append((kind, int(c["issue_url"].rsplit("/", 1)[1]), jwt, c["user"]["login"]))
    return out


# ---- the relay -----------------------------------------------------------------------------------------------------

def relay_one(ledger, payer, kind: str, jwt: str, submit=None) -> dict:
    """Send one token to the chain (knos.settle.relay.submit). Returns its result, plus "note": what happened in words."""
    if submit is None:
        from ..settle.relay import submit
    r = submit(ledger, payer, jwt)
    want = RELAY_KIND.get(kind, kind)
    if r.get("ok") and r.get("kind") != want:
        return {"ok": False, "why": f"marker {kind} but audience {r.get('kind')}"}
    if r.get("ok"):
        r["note"] = note(r)
    return r


def _usdc(units: int) -> str:
    return f"{units / 1_000_000:.2f}"


def note(r: dict) -> str:
    """One sentence for the verdict comment."""
    k = r["kind"]
    wait = lambda s: "at once" if not s else f"{s // 3600} h after" if s >= 3600 else f"{s} s after"  # noqa: E731
    if k == "fund":
        what = "a maintainer merges the pull request that closes this issue" if r["mode"] == 0 else "the acceptance checks pass"
        held = f" The payment is released {wait(r.get('review', 0))} that; until then /knos veto takes it back." if r.get("review") else ""
        return f"{_usdc(r['amount'])} test USDC is in escrow for issue #{r['issue']}, paid when {what}.{held} Job {r['job']}."
    if k == "pay":
        net = sum(p["amount"] - p["fee"] for p in r["paid"])
        hold = max(p["waits"] for p in r["paid"])
        when = "is now waiting under" if not hold else f"will be released in {wait(hold).replace(' after', '')} (a maintainer's /knos veto on the issue takes it back) to"
        return (f"{_usdc(net)} {when} GitHub user id {r['author_id']} for issue #{r['issue']}. "
                f"Claim it to any address: https://drexthealpha.github.io/Knos/#claim")
    if k == "veto":
        return f"{len(r['vetoed'])} payment(s) taken back."
    if k == "claim":
        return "Sent " + ", ".join(f"{_usdc(c['amount'])} of {c['mint'][:4]}…" for c in r["claimed"]) + f" to {r['address']}."
    return f"Key {r['key']} {'added' if r.get('added') else 'already known'}."


def log_line(kind: str, repo: str, n: int, jwt: str, r: dict) -> str:
    head = f"knos-relay {kind} {repo}#{n} {token_id(jwt)}"
    if not r["ok"]:
        return f"{head} fail {r['why']}"
    first = " (another relayer carried it first)" if r.get("already") else ""
    return f"{head} ok sig={','.join(r['sigs'][-3:]) or 'none'} note={r['note']}{first}"


def _log_issue() -> int:
    res = _api(f"repos/{HOME_REPO}/issues?labels={LOG_LABEL}&state=open&per_page=1")
    if res:
        return res[0]["number"]
    try:
        gh("label", "create", LOG_LABEL, "--repo", HOME_REPO, "--force")
    except RuntimeError:
        pass
    url = gh("issue", "create", "--repo", HOME_REPO, "--title", "Knos relay log", "--label", LOG_LABEL,
             "--body", "One line per token the always-on worker relayed (see src/knos/proof/ghrelay.py).")
    return int(url.strip().rsplit("/", 1)[1])


def post_log(lines: list[str]) -> None:
    if lines:
        gh("issue", "comment", str(_log_issue()), "--repo", HOME_REPO, "--body-file", "-", inp="\n".join(lines))


def _state_path() -> Path:
    from .. import paths
    return paths.home() / "ghrelay.json"


def once(ledger=None, payer=None, now: float | None = None) -> list[str]:
    sp = _state_path()
    try:
        state = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    now = now or time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 70 * 60))   # a token is accepted for an hour
    seen = set(state.get("seen", []))
    tries = dict(state.get("tries", {}))
    if ledger is None:
        from .. import chain
        ledger, payer = chain.ledger(), chain.key()
    lines = []
    for repo in sorted(discover(since, state)):
        try:
            items = found(repo, since)
        except Exception:  # noqa: BLE001, S112
            continue
        def issued(item) -> int:
            try:
                return int(claims(item[2]).get("iat", 0))
            except Exception:  # noqa: BLE001 - not a token: relay_one reports it
                return 0
        # oldest first: a repository's fund tokens must reach the chain in the order GitHub issued them
        for kind, n, jwt, _who in sorted(items, key=issued):
            tid = token_id(jwt)
            if tid in seen:
                continue
            seen.add(tid)
            r = relay_one(ledger, payer, kind, jwt)
            if r.get("retry"):          # the per-minute limit, or the cluster dropped it: the next pass tries again
                if r.get("transient"):  # only a failure counts toward giving up; waiting out the limit does not
                    tries[tid] = tries.get(tid, 0) + 1
                if tries.get(tid, 0) < MAX_TRIES:
                    seen.discard(tid)
                    continue
                r["why"] = f"{r.get('why')} (gave up after {MAX_TRIES} passes; run the workflow again for a fresh token)"
            tries.pop(tid, None)
            if r.get("ok") and not r.get("sigs") and not r.get("already"):
                continue                # nothing had to be done (a key the chain already has): no log line
            lines.append(log_line(kind, repo, n, jwt, r))
            state.setdefault("owners", [])
            if r["ok"] and repo.split("/")[0] not in state["owners"]:
                state["owners"].append(repo.split("/")[0])
    # tests-mode payments whose review window has passed, and bounties past their deadline
    try:
        from ..settle import relay as settle
        chain_now = int(ledger.now())
        for sig in settle.settle_due(ledger, payer, chain_now):
            lines.append(f"knos-relay settle - - ok sig={sig} note=a proven bounty's review window passed; paid")
        for sig in settle.refund_due(ledger, payer, chain_now):
            lines.append(f"knos-relay refund - - ok sig={sig} note=a bounty passed its deadline unproven; refunded")
    except Exception as why:  # noqa: BLE001 - the crank is best effort; the next pass tries again
        print(f"settle/refund: {why}", file=sys.stderr)
    state["seen"] = sorted(seen)[-2000:]
    state["tries"] = dict(list(tries.items())[-500:])
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state), encoding="utf-8")
    try:
        post_log(lines)
    except RuntimeError as why:
        print(f"relay log: {why}", file=sys.stderr)
    for ln in lines:
        print(ln)
    return lines


if __name__ == "__main__":
    once()
