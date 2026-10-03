"""The zero-secret GitHub relay: Knos's public worker (worker.yml) carries the tokens that repositories' own workflows
minted to Solana, and pays the fees. Anyone else can run it too (`knos relay`); a relayer decides nothing. Each relayer
uses a key of its own (a token's account on chain belongs to the key that carried it, so two relayers never touch each
other's; two runs sharing one key do, and the loser of that race tries again on its next pass).

Transport (caller -> worker). A repository that installed Knos and keeps no relay key of its own has NO Knos secret, so
it cannot call the Knos repository or its API with auth. Its workflow posts the GitHub Actions OIDC token it minted as
a comment with its own GITHUB_TOKEN (`post_token` writes that comment, `found` reads it):

    knos-fund: <jwt>      on the issue, after a maintainer's `/knos fund <amount>`; and with it, on a line of its own,
    knos-terms: <json>    the bounty's terms (the token carries only their hash)
    knos-proof: <jwt>     on the pull request, once it is merged and has met the bounty's terms
    knos-bind: <jwt>      on an issue in the claimer's own repository named knos-claim (the pinned claim workflow)
    knos-key: <jwt>       on an issue in drexthealpha/knos-oidc-rotate or drexthealpha/Knos (a key an issuer publishes,
                          named by GitHub's signature)
    knos-verify: <jwt>    any GitHub Actions or GitLab CI token: verified into an account another program can read,
                          and nothing more; 20 a day for one repository
    knos-veto: <jwt>      the first deployment's, as before: a bounty funded there finishes there
    knos-claim: <jwt>

The marker only says where to look. What a token does is fixed by its audience, and so is where it goes: `knos:` to the
first deployment's relay (knos.settle.relay), `knos2:` to the second's (knos.settle.v2.relay), and a key token
(`knos-oidc:key:`) to both.

Posting the token in public is acceptable ONLY because it is not a bearer credential here: its audience names one
action, the escrow accepts it for at most an hour and guards each action against replay, and it is valid only from the
workflow commit the bounty pinned. Whoever relays it first only pays the fees; the money goes where the token says.

Discovery (worker finds the comments). Public reads, no install. The repositories the worker knows (those it found a
token in during the last two days, kept in KNOS_HOME; the ones in KNOS_RELAY_REPOS; the rotate repository; its own) are
read on every pass with conditional requests: GitHub answers 304 while nothing is new, which costs nothing against the
rate limit, so a pass every few seconds is cheap. (Past fifteen known repositories, the ones with the newest tokens are
read on every pass and the others a few a pass, in turn.) New repositories are found at most every 30 seconds: one
issue search for the word every token comment carries (`knosrelay`), and the recently pushed repositories of owners
served before. With no GH_TOKEN GitHub allows 60 requests an hour, which is one pass now and then, not a worker.

Result (worker -> caller). The worker cannot write to other repositories. It appends one line per relayed token to the
open issue labelled `knos-relay` in its own repository (its own GITHUB_TOKEN can do that):

    knos-relay <kind> <owner/repo>#<n> <token id> ok sig=<s1>[,<s2>...] note=<what happened, in words> t=<seconds>
    knos-relay <kind> <owner/repo>#<n> <token id> fail <reason>

`t` is the time from the comment's creation to the token's last transaction. The caller waits for the line that names
its token id (`wait_for`) and comments the verdict with its own token. A line with that id is a verdict on the token
itself, the same whoever posted it. A comment that holds a token it cannot carry (someone's copy under another marker,
or with other terms) is logged with `-` for the id, so it answers nobody who waits for the token.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TOKEN = re.compile(r"knos-(proof|fund|bind|veto|claim|key|verify):\s*(eyJ[\w-]+\.[\w-]+\.[\w-]+)")
TERMS = re.compile(r"^knos-terms:[ \t]*(\{.*\})[ \t\r]*$", re.M)   # a fund token's terms, in the same comment
MARK = "knosrelay"   # one word every token comment carries, so one search finds them all
ROTATE_REPO = "drexthealpha/knos-oidc-rotate"
RELAY_KIND = {"proof": "pay"}   # the comment marker says proof; the relays call that audience pay
LOG_LABEL = "knos-relay"
LOG_BOT = "github-actions[bot]"  # who writes the log: the log repository's own workflow. Nobody else's line counts.
# The repository the relay keeps its public log in, and whose own issues it always scans: the one whose worker.yml runs
# it (worker.yml passes its own name), Knos's by default.
HOME_REPO = os.environ.get("KNOS_RELAY_LOG_REPO") or "drexthealpha/Knos"
MAX_TRIES = 12          # failed passes a token gets when the failure may clear (the cluster dropped it, a twin run)
HORIZON = 70 * 60       # how far back a pass looks: a token is accepted for an hour
SEARCH_EVERY = 30       # seconds between two searches for repositories the worker does not know yet
CRANK_EVERY = 60        # seconds between two rounds of what needs no token (refunds, held payments), in `serve`
KNOWN_FOR = 2 * 86_400  # a repository is known this long after its last token
EVERY_PASS = 15         # known repositories read on every pass: the ones whose tokens are newest
IN_TURN = 5             # of the other known ones, this many a pass, in turn (GitHub allows 900 requests a minute)
VERIFY_PER_DAY = 20     # verify-only tokens carried for one repository in a day (each locks rent for an hour)
API = "https://api.github.com/"


def claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def token_id(jwt: str) -> str:
    """What the relay log names a token by (never the token itself)."""
    return hashlib.sha256(jwt.strip().encode()).hexdigest()[:16]


def checks_hash(root: Path) -> str:
    """sha256 over the acceptance directory: for each file, sorted by its posix path relative to `root`,
    "<path>\\0<sha256 hex of the bytes>\\n"."""
    h = hashlib.sha256()
    for f in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        h.update(f"{f.relative_to(root).as_posix()}\0{hashlib.sha256(f.read_bytes()).hexdigest()}\n".encode())
    return h.hexdigest()


def _stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _unix(stamp) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


# ---- GitHub (GH_TOKEN is the worker's own GITHUB_TOKEN, used only for rate limits and its own log) -----------------

class Hub:
    """GitHub's REST API as the worker reads it. Every GET is conditional: an answer is kept with its ETag and asked
    for again with If-None-Match, and GitHub's 304 ("what you have is current") costs nothing against the rate limit.
    So reading a repository that has nothing new is one cheap request, however often it is done."""

    def __init__(self, urlopen=None):
        self.kept: dict[str, tuple[str, object]] = {}   # path -> (its ETag, the answer)
        self.fresh = self.same = 0                      # answers that were new, and 304s
        self._open = urlopen or urllib.request.urlopen

    def _request(self, path: str, data: dict | None = None, method: str | None = None, etag: str | None = None):
        head = {"Accept": "application/vnd.github+json", "User-Agent": "knos"}
        tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if tok:
            head["Authorization"] = f"Bearer {tok}"
        if etag:
            head["If-None-Match"] = etag
        if data is not None:
            head["Content-Type"] = "application/json"
        return urllib.request.Request(API + path, data=None if data is None else json.dumps(data).encode(), headers=head, method=method)

    def get(self, path: str):
        kept = self.kept.get(path)
        try:
            with self._open(self._request(path, etag=kept[0] if kept else None), timeout=10) as resp:
                got, etag = json.loads(resp.read() or b"null"), resp.headers.get("ETag")
        except urllib.error.HTTPError as e:
            if e.code == 304 and kept:
                self.same += 1
                return kept[1]
            raise RuntimeError(f"GitHub answered {e.code} for {path}") from None
        except (OSError, ValueError) as why:
            raise RuntimeError(f"GitHub did not answer for {path}: {why}") from None
        self.fresh += 1
        if etag:
            self.kept[path] = (etag, got)
        return got

    def send(self, path: str, data: dict, method: str = "POST"):
        try:
            with self._open(self._request(path, data, method), timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GitHub answered {e.code} for {method} {path}") from None
        except (OSError, ValueError) as why:
            raise RuntimeError(f"GitHub did not answer for {method} {path}: {why}") from None


_HUB = Hub()


def _api(path: str) -> list | dict:
    return _HUB.get(path)


def discover(since: str, state: dict, getter=_api) -> set[str]:
    """Repositories that may hold a token: the search, the owners served before, and the ones always read."""
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


class Found(tuple):
    """One token a comment carries: (marker kind, issue or pull request number, jwt, comment author). It also knows
    `terms`, the JSON a `knos-terms:` line of the same comment gave (a fund token's travel with it), and `created`,
    when the comment was posted (unix time; None when GitHub did not say)."""
    terms: bytes | None
    created: float | None

    def __new__(cls, kind: str, number: int, jwt: str, who: str, terms: bytes | None = None, created: float | None = None):
        self = super().__new__(cls, (kind, number, jwt, who))
        self.terms, self.created = terms, created
        return self


def tokens(comments: list, since: str = "") -> list[Found]:
    """Every token in GitHub's issue comments that were posted at or after `since`."""
    out = []
    for c in comments:
        if str(c.get("created_at") or since) < since:       # GitHub's times are ISO 8601 in UTC: they sort as text
            continue
        body = c.get("body") or ""
        terms = TERMS.search(body)
        for kind, jwt in TOKEN.findall(body):
            out.append(Found(kind, int(c["issue_url"].rsplit("/", 1)[1]), jwt, (c.get("user") or {}).get("login", ""),
                             terms.group(1).encode() if terms and kind == "fund" else None, _unix(c.get("created_at"))))
    return out


def found(repo: str, since: str, getter=_api) -> list[Found]:
    """Every token in `repo`'s comments since `since`. The request is the same on every pass (the newest hundred
    comments), so a pass with nothing new is a 304; an older page is read only when a whole page is newer than `since`."""
    out: list[Found] = []
    for page in range(1, 6):
        got = getter(f"repos/{repo}/issues/comments?sort=created&direction=desc&per_page=100" + (f"&page={page}" if page > 1 else ""))
        out += tokens(got, since)
        if len(got) < 100 or str(got[-1].get("created_at") or "") < since:
            break
    return out


def token_comment(kind: str, jwt: str, terms: bytes | str | None = None) -> str:
    """The comment a workflow posts for a relayer to find: the marker and the token, a fund token's terms on a line of
    their own, and the word the search finds. `found` reads exactly this."""
    lines = [f"knos-{kind}: {jwt}"] + ([f"knos-terms: {terms.decode() if isinstance(terms, bytes) else terms}"] if terms else [])
    return "\n".join(lines) + f"\n\n<sub>{MARK}: a GitHub-signed token. Anyone can carry it to Solana, and it can only do what it says.</sub>"


def post_token(repo: str, number: int, kind: str, jwt: str, terms: bytes | str | None = None, github=None) -> str:
    """Posts a token for a relayer to find: `token_comment` on issue or pull request `number` of `repo`, with the
    caller's own GitHub token (`github(path, data)` posts to GitHub's API; default: this module's). Returns the id
    the relay log will name the token by, which is what `wait_for` takes."""
    (github or _HUB.send)(f"repos/{repo}/issues/{number}/comments", {"body": token_comment(kind, jwt, terms)})
    return token_id(jwt)


# ---- the relay -----------------------------------------------------------------------------------------------------

def audience(jwt: str) -> str:
    """A token's audience, as it says it (unverified); "" for what is not a token."""
    try:
        aud = claims(jwt)["aud"]
        return aud if isinstance(aud, str) else str(aud[0])
    except Exception:  # noqa: BLE001 - not a token: the relay says so
        return ""


def carry(ledger, payer, jwt: str, terms: bytes | None = None) -> dict:
    """One token to the deployment its audience names: `knos:` to the first relay, `knos2:` and key tokens to the
    second (which hands a key token to the first as well). `terms`: a fund token's terms JSON. The relay's result."""
    from ..settle import relay as first
    from ..settle.v2 import relay as second
    return first.submit(ledger, payer, jwt) if audience(jwt).startswith("knos:") else second.submit(ledger, payer, jwt, terms)


def misposted(kind: str, jwt: str, terms: bytes | None = None) -> str | None:
    """Why a comment cannot carry the token it holds, or None: its marker does not fit the token's audience, or (a
    fund token of the second deployment) its `knos-terms:` line is not the terms the audience names. Anyone can copy
    a token into a comment of their own, so such a comment says nothing about the token: it is passed over, and the
    token is still carried from the comment that posts it rightly."""
    from ..settle import relay as first
    from ..settle.v2 import relay as second
    aud = audience(jwt)
    named = (first if aud.startswith("knos:") else second).kind_of(aud)
    if kind == "verify":
        return f"it is a Knos {named} token, which is carried under its own marker" if named else None
    if named is not None and named != RELAY_KIND.get(kind, kind):
        return f"posted as knos-{kind}, but its audience is a {named} token's"
    if named == "fund" and aud.startswith("knos2:") and (terms is None or hashlib.sha256(terms).hexdigest() != (aud.split(":") + [""] * 6)[5]):
        return "its `knos-terms:` line is missing, or is not the terms the token names"
    return None


def relay_one(ledger, payer, kind: str, jwt: str, submit=None, terms: bytes | None = None) -> dict:
    """Send one token a comment carried under the marker `kind` to the chain (`carry`); under the `verify` marker it
    is only verified. A comment that cannot carry its token (`misposted`) is refused before anything is sent. Returns
    the relay's result, plus "note": what happened in words. `submit(ledger, payer, jwt)`: a relay to use instead."""
    if submit is not None:
        r = submit(ledger, payer, jwt)
    else:
        wrong = misposted(kind, jwt, terms)
        if wrong:
            return {"ok": False, "kind": None, "why": wrong}
        if kind == "verify":
            from ..settle.v2 import relay as second
            r = second.verify_only(ledger, payer, jwt)
        else:
            r = carry(ledger, payer, jwt, terms)
    if r.get("ok") and r.get("kind") != RELAY_KIND.get(kind, kind):
        return {"ok": False, "why": f"marker {kind} but audience {r.get('kind')}"}
    if r.get("ok"):
        r["note"] = note(r)
    return r


def _usdc(units: int) -> str:
    return f"{units / 1_000_000:.2f}"


def _day(t: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


def _note2(r: dict) -> str | None:
    """What a result of the second deployment's relay means, in words; None for a result that is not one of its."""
    k = r["kind"]
    if k == "fund" and "faucet" in r:
        what = ("the pull request that closes this issue is merged and meets the bounty's terms" if r["mode"] == 0
                else "the issue's acceptance checks pass on a pull request that meets the bounty's terms")
        money = "test USDC" if r["faucet"] else f"from balance {r['balance']}"
        return (f"{_usdc(r['amount'])} {money} is in escrow for issue #{r['issue']}, paid when {what}. "
                f"Unpaid by {_day(r['deadline'])}, it goes back to its funder. Job {r['job']}.")
    if k == "pay" and "payee_id" in r:
        from ..settle.v2 import pay
        sent, held = [p for p in r["paid"] if p["to"]], [p for p in r["paid"] if not p["to"]]
        test = {str(pay.faucet_mint()), str(pay.USDC_DEVNET)}       # the two mints that are test USDC; any other is named by nothing here
        net = lambda ps: _usdc(sum(p["amount"] - p["fee"] for p in ps)) + (" test USDC" if all(p["mint"] in test for p in ps) else "")  # noqa: E731
        out = [f"{net(sent)} was paid to {sent[0]['to']} for issue #{r['issue']} (GitHub user id {r['payee_id']})."] if sent else []
        if held:
            out.append(f"{net(held)} is held for GitHub user id {r['payee_id']} for issue #{r['issue']} until {_day(held[0]['held_until'])}. "
                       "It is sent once they name a wallet: `knos claim <their Solana address>`, or "
                       "https://drexthealpha.github.io/Knos/#claim. After that date it goes back to its funder.")
        return " ".join(out)
    if k == "bind":
        got = r["settled"]
        more = f" {len(got)} held payment{'s' if len(got) != 1 else ''}, {_usdc(sum(s['amount'] - s['fee'] for s in got))} in all, went there." if got else ""
        return f"GitHub user id {r['user_id']} is now paid at {r['wallet']}.{more}"
    if k == "key" and "refreshed" in r:
        did = ("registered with the second verifier: it verifies after a day's wait, once the guardian has approved it" if r["added"]
               else "refreshed: the second verifier keeps it 30 days from now" if r["refreshed"]
               else f"not taken by the second verifier ({r['why']})" if r.get("why") else "already known to the second verifier")
        return f"Key {r['key']} {did}." + (" The first deployment added it." if (r.get("first") or {}).get("added") else "")
    if k == "verify":
        return (f"Verified. Account {r['account']} (payer {r['payer']}) holds the token's claims for a program to read "
                f"until {_day(r['exp'] + 3600)}.")
    return None


def note(r: dict) -> str:
    """One sentence for the verdict comment."""
    k = r["kind"]
    words = _note2(r)
    if words is not None:
        return words
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


def log_line(kind: str, repo: str, n: int, jwt: str, r: dict, t: int | None = None) -> str:
    """The public log's line for one token. `t`: seconds from its comment's creation to its last transaction."""
    head = f"knos-relay {kind} {repo}#{n} {token_id(jwt)}"
    if not r["ok"]:
        return f"{head} fail {' '.join(str(r['why']).split())}"
    first = " (another relayer carried it first)" if r.get("already") else ""
    return f"{head} ok sig={','.join(r['sigs'][-3:]) or 'none'} note={r['note']}{first}" + (f" t={t}" if t is not None else "")


_LOG: dict[str, int] = {}


def _log_issue(repo: str = "", get=None) -> int | None:
    """The open issue labelled knos-relay in a repository: where its relay log is. None when it has none."""
    repo = repo or HOME_REPO
    if repo not in _LOG:
        res = (get or _api)(f"repos/{repo}/issues?labels={LOG_LABEL}&state=open&per_page=1")
        if not res:
            return None
        _LOG[repo] = int(res[0]["number"])
    return _LOG[repo]


def post_log(lines: list[str]) -> None:
    """Append lines to this relay's public log, as one comment. The log issue is made on first use."""
    if not lines:
        return
    n = _log_issue()
    if n is None:
        try:
            _HUB.send(f"repos/{HOME_REPO}/labels", {"name": LOG_LABEL, "color": "ededed"})
        except RuntimeError:
            pass        # the label is there already
        n = _LOG[HOME_REPO] = int(_HUB.send(f"repos/{HOME_REPO}/issues", {
            "title": "Knos relay log", "labels": [LOG_LABEL],
            "body": "One line per token the always-on worker relayed (see src/knos/proof/ghrelay.py)."})["number"])
    while lines:                # a comment holds 65,536 characters
        take = max(1, next((i for i in range(1, len(lines) + 1) if sum(len(ln) + 1 for ln in lines[:i]) > 60_000), len(lines) + 1) - 1)
        _HUB.send(f"repos/{HOME_REPO}/issues/{n}/comments", {"body": "\n".join(lines[:take])})
        lines = lines[take:]


def wait_for(token_id: str, log_repo: str, timeout: float, every: float = 3.0, get=None) -> str | None:
    """The worker's log line for a token: `knos-relay <kind> <owner/repo>#<n> <token id> ok sig=... note=... t=...`
    or `... fail <reason>`. Reads the public log of `log_repo` every `every` seconds for up to `timeout` seconds; None
    when no relayer has reported on the token by then. `get(path)` reads GitHub's API (default: this module's
    conditional reader, for which a log with nothing new costs nothing). Only lines the log repository's own workflow
    wrote count: anyone can comment on a public issue."""
    get = get or _api
    since, end = _stamp(time.time() - 300), time.monotonic() + timeout     # this clock and GitHub's may differ a little
    while True:
        try:
            n = _log_issue(log_repo, get)
            for page in range(1, 11) if n is not None else ():
                got = get(f"repos/{log_repo}/issues/{n}/comments?since={since}&per_page=100" + (f"&page={page}" if page > 1 else ""))
                for c in got:
                    if (c.get("user") or {}).get("login") != LOG_BOT:
                        continue
                    for line in (c.get("body") or "").splitlines():
                        if line.startswith("knos-relay ") and f" {token_id} " in line:
                            return line
                if len(got) < 100:
                    break
        except Exception:  # noqa: BLE001, S110 - GitHub did not answer: ask again
            pass
        left = end - time.monotonic()
        if left <= 0:
            return None
        time.sleep(min(every, left))


def _state_path() -> Path:
    from .. import paths
    return paths.home() / "ghrelay.json"


def _origin(jwt: str, repo: str) -> str:
    """Where a verify-only token was issued: GitHub's repository id (GitLab's project id) as the token itself says,
    else the repository its comment is in."""
    try:
        c = claims(jwt)
    except Exception:  # noqa: BLE001 - not a token: the relay says so
        return repo
    return str(c["repository_id"]) if c.get("repository_id") else f"gitlab:{c['project_id']}" if c.get("project_id") else repo


def _post(lines: list[str], state: dict) -> None:
    """Log lines now; the ones GitHub would not take are kept for the next pass, so that no verdict is lost."""
    try:
        post_log(lines)
    except Exception as why:  # noqa: BLE001 - GitHub said no, or answered something else: the lines are kept
        print(f"relay log: {why}", file=sys.stderr)
        state["unposted"] = (state.get("unposted", []) + lines)[-200:]


def _cranks(ledger, payer) -> list[str]:
    """What needs no token, on both deployments: a proven payment whose wait is over, a bounty nobody proved in time,
    a held payment whose payee has bound a wallet since, and the rent of this relayer's stale token accounts."""
    from ..settle import relay as first
    from ..settle.v2 import relay as second
    now = int(ledger.now())
    rounds = (("settle", lambda: first.settle_due(ledger, payer, now), "a proven bounty's review window passed; paid"),
              ("refund", lambda: first.refund_due(ledger, payer, now), "a bounty passed its deadline unproven; refunded"),
              ("refund", lambda: second.refund_due(ledger, payer, now), "a bounty nobody could be paid from any more went back to its funder"),
              ("settle", lambda: second.settle_held(ledger, payer), "a held payment went to the wallet its payee has bound"),
              ("sweep", lambda: second.sweep(ledger, payer, now), None))
    lines = []
    for kind, send, words in rounds:
        try:
            lines += [f"knos-relay {kind} - - ok sig={sig} note={words}" for sig in send() if words]
        except Exception as why:  # noqa: BLE001 - best effort; the next round tries again
            print(f"{kind}: {why}", file=sys.stderr)
    return lines


def once(ledger=None, payer=None, now: float | None = None, crank: bool = True) -> list[str]:
    """One pass: read what the repositories posted, carry each new token, send what needs no token (`crank`), and log
    the verdicts. Returns this pass's log lines."""
    sp = _state_path()
    try:
        state = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    now = now or time.time()
    since = _stamp(now - HORIZON)
    order = list(state.get("seen", []))                 # what was done already (each token as it was posted), oldest first
    seen = set(order)
    tries = dict(state.get("tries", {}))
    hold = {t: until for t, until in dict(state.get("hold", {})).items() if until > now}      # tokens not to be tried again before a time
    known = {r: t for r, t in dict(state.get("repos", {})).items() if now - t <= KNOWN_FOR}
    day = _stamp(now)[:10]
    verified = dict(state.get("verify", {}).get("n", {})) if state.get("verify", {}).get("day") == day else {}
    if ledger is None:
        from .. import chain
        ledger, payer = chain.ledger(), chain.key()
    if state.get("unposted"):
        _post(state.pop("unposted"), state)
    newest = sorted(known, key=lambda r: (-known[r], r))
    rest = sorted(newest[EVERY_PASS:])
    turn = int(state.get("turn", 0)) % max(len(rest), 1)
    state["turn"] = turn + IN_TURN
    repos = {ROTATE_REPO, HOME_REPO} | {r.strip() for r in os.environ.get("KNOS_RELAY_REPOS", "").split(",") if "/" in r}
    repos |= set(newest[:EVERY_PASS]) | set((rest + rest)[turn:turn + IN_TURN])
    if now - state.get("searched", 0) >= SEARCH_EVERY:
        repos |= set(discover(since, state))
        state["searched"] = now

    def read(repo: str):
        try:
            return repo, found(repo, since)
        except Exception:  # noqa: BLE001 - GitHub did not answer for this one: the next pass asks again
            return repo, []
    with ThreadPoolExecutor(max_workers=8) as pool:
        listed = list(pool.map(read, sorted(repos)))

    def issued(item) -> int:
        try:
            return int(claims(item[2]).get("iat", 0))
        except Exception:  # noqa: BLE001 - not a token: relay_one reports it
            return 0
    lines, later = [], []
    # oldest first, whatever repository it is in: a balance takes its fund tokens in the order GitHub issued them
    for repo, item in sorted(((repo, item) for repo, items in listed for item in items), key=lambda found: issued(found[1])):
        kind, n, jwt, _who = item
        terms, created = getattr(item, "terms", None), getattr(item, "created", None)
        # a token is done once as it was posted: under this marker, with these terms. A copy posted another way is
        # another matter, so nobody's copy can use up, or speak for, the token of the comment that posts it rightly
        tid = hashlib.sha256(f"{kind}\n{jwt}\n".encode() + (terms or b"")).hexdigest()[:16]
        known[repo] = now
        if tid in seen or tid in hold:
            continue
        seen.add(tid)
        order.append(tid)
        wrong = misposted(kind, jwt, terms)
        if wrong:                   # logged without the token's id: whoever waits for that token is not answered by this
            lines.append(f"knos-relay {kind} {repo}#{n} - fail this comment cannot carry its token ({token_id(jwt)[:8]}...): {wrong}")
            later.append(lines[-1])
            continue
        origin = _origin(jwt, repo)
        if kind == "verify" and verified.get(origin, 0) >= VERIFY_PER_DAY:
            r = {"ok": False, "kind": "verify", "why": f"{VERIFY_PER_DAY} tokens a day are verified for one repository, and this one has had "
                                                       "them; post it again after midnight UTC, or relay it yourself (anyone can)"}
        else:
            r = relay_one(ledger, payer, kind, jwt, **({"terms": terms} if terms else {}))
        if r.get("retry"):          # the faucet's minute, or the cluster dropped it: a later pass tries again
            if r.get("transient"):  # only a failure counts toward giving up; waiting out the limit does not
                tries[tid] = tries.get(tid, 0) + 1
            if tries.get(tid, 0) < MAX_TRIES:
                seen.discard(tid)
                # the relay says how long to wait, when it knows; a failure that keeps coming is given more room each
                # time, so that passes a few seconds apart do not give a token up within the same minute of trouble
                wait = r.get("wait") or (min(60, 10 * (tries[tid] - 2)) if r.get("transient") else 0)
                if wait > 0:
                    hold[tid] = now + wait
                continue
            r["why"] = f"{r.get('why')} (gave up after {MAX_TRIES} passes; run the workflow again for a fresh token)"
        tries.pop(tid, None)
        if r.get("ok") and not r.get("sigs") and not r.get("already"):
            continue                # nothing had to be done (a key the chain already has): no log line
        if kind == "verify" and r.get("ok") and r.get("sigs"):
            verified[origin] = verified.get(origin, 0) + 1
        took = max(0, round(time.time() - created)) if r.get("ok") and created is not None else None
        line = log_line(kind, repo, n, jwt, r, took)
        lines.append(line)
        if r["ok"]:
            _post([line], state)    # at once: someone is waiting for it
        else:
            later.append(line)
        state.setdefault("owners", [])
        if r["ok"] and repo.split("/")[0] not in state["owners"]:
            state["owners"].append(repo.split("/")[0])
    if crank:
        try:
            more = _cranks(ledger, payer)
        except Exception as why:  # noqa: BLE001 - the chain's clock could not be read; the next pass tries again
            more = []
            print(f"settle/refund: {why}", file=sys.stderr)
        lines += more
        later += more
    if later:
        _post(later, state)
    state.update(seen=list(dict.fromkeys(t for t in order if t in seen))[-2000:], tries=dict(list(tries.items())[-500:]), repos=known,
                 hold=dict(list(hold.items())[-500:]), verify={"day": day, "n": verified})
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state), encoding="utf-8")
    for ln in lines:
        print(ln)
    return lines


def serve(seconds: float, every: float = 3.0, ledger=None, payer=None, clock=time.monotonic, sleep=time.sleep) -> int:
    """Passes for `seconds`, each starting `every` seconds after the one before (at once, when that one took longer):
    what worker.yml runs. The repositories' ETags are kept from pass to pass, so a pass that finds nothing new costs
    a 304 for each repository it knows. What needs no token is sent once a minute. Returns the number of lines logged."""
    if ledger is None:
        from .. import chain
        ledger, payer = chain.ledger(), chain.key()
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        print("relay: no GH_TOKEN, so GitHub allows 60 requests an hour: most passes will read nothing. Set GH_TOKEN (any token; public reads).", file=sys.stderr)
    end, cranked, logged = clock() + seconds, None, 0
    while clock() < end:
        began = clock()
        crank = cranked is None or began - cranked >= CRANK_EVERY
        try:
            logged += len(once(ledger, payer, crank=crank))
        except Exception as why:  # noqa: BLE001 - one bad pass never stops the worker
            print(f"relay pass: {type(why).__name__}: {why}", file=sys.stderr)
        cranked = began if crank else cranked
        sleep(max(0.0, min(every - (clock() - began), end - clock())))
    return logged


if __name__ == "__main__":
    once()
