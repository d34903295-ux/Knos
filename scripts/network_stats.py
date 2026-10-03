"""The public Knos numbers, counted from Solana devnet and written to the site's stats.json.

    python scripts/network_stats.py --out _site/stats.json

Three sources, nothing self-reported:

  1. The escrow's own log lines in the transaction history of both deployments: `knos:` lines of the first
     (programs/), `knos2:` lines of the second (programs-v2/). A line counts only when the escrow itself logged it in
     a transaction that succeeded: a line another program prints in the same transaction is not one.
  2. The public relay log (the issue labelled `knos-relay` in drexthealpha/Knos): one line per token a relay carried,
     with the transaction it ended in. With GitHub's time of the comment or of the merge that the token answers, it
     gives the latency a person saw.
  3. GitHub code search, when a token is in GH_TOKEN or GITHUB_TOKEN: the public repositories whose workflows call
     Knos's. Without a token that number is left out and labelled "not measured".

What is counted, for every job a funding line opened (a job is one bounty on one issue):

    own      the Balance's owner, the commenter who funded, the payee, the funding wallet or the wallet that was paid
             is Knos's own (scripts/own_github_ids.json: GitHub ids, and wallets, which have no GitHub id)
    self     not own, paid, and the funder is the payee (the second escrow's own rule: the Balance's owner or the
             commenter is the payee, or the job is paid to the wallet whose money it was)
    test     not own, not self, and the money came from the devnet faucet (free test USDC)
    outside  everything else: someone who is not Knos put their own tokens in, and did not pay themselves

Each job is in exactly one of the four. `outside` is the headline; the other three are shown apart and never added
to it. A funder is the GitHub owner of the Balance a comment spent, or the wallet that funded. `repeat_funders` are
outside funders who funded another outside job after one of theirs was paid.

Latency, each as { count, median, p90, slowest } in seconds (p90 by nearest rank; null when count is 0):

    comment_to_funded   from the funding comment (GitHub's created_at) to the funding transaction's block time
    merge_to_paid       from the merge (GitHub's merged_at) to the paying transaction's block time
    funded_to_paid      from the funding transaction to the paying one: the time the work and the merge took

The first two are measured over the lines of the public relay log; a job that relays its own tokens with its own
key does not write there and is not in them. A relay line may carry the start time itself as `asked=<unix seconds>`;
without it GitHub is asked.

    relay.fund, relay.pay   the relay's own part, from the relay log's ` t=<seconds>`: the comment that carried the
                            token to the last transaction the relay sent for it. Shorter than what a person waited
                            (that starts at their own comment or at the merge), so it is shown beside the two above
                            and never in place of them.

A payment never counts as outside use when it was funded and received by the same GitHub id, by the same wallet, or
when either end is one of Knos's own ids or wallets: kind_of() is the one place that says so.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from solders.pubkey import Pubkey  # noqa: E402

from knos import chain  # noqa: E402
from knos.settle import oidc, pay  # noqa: E402
from knos.settle.v2 import oidc as oidc2  # noqa: E402
from knos.settle.v2 import pay as pay2  # noqa: E402

# What is Knos's own: GitHub account ids (ids, not logins: a login can be renamed) and wallets. Activity that touches
# any of these is "own", never "outside".
_OWN = json.loads((ROOT / "scripts" / "own_github_ids.json").read_text(encoding="utf-8"))
OWN = frozenset(_OWN["ids"])
OWN_WALLETS = frozenset(_OWN.get("wallets", []))
PROGRAMS = {str(pay.PAY_ID): 1, str(pay2.PAY_ID): 2}      # the escrow of each deployment
PREFIX = {1: "knos", 2: "knos2"}
RELAY_LOG_REPO = "drexthealpha/Knos"
# Workflow files that call Knos's reusable workflows or its Action, as GitHub's code search finds them.
INSTALLED_QUERIES = ('"drexthealpha/knos-workflows/.github/workflows" path:.github/workflows',
                     '"drexthealpha/Knos/.github/workflows" path:.github/workflows',
                     '"uses: drexthealpha/Knos@" path:.github/workflows')
_INVOKE = re.compile(r"Program (\w+) invoke \[(\d+)\]$")
_DONE = re.compile(r"Program (\w+) (?:success|failed)")
_LOG = re.compile(r"Program log: (knos2?):(\w+)(?: (.*))?$")


# ---- the chain's log, as events ---------------------------------------------------------------------------------------
def events_of(tx: dict | None, programs: dict[str, int] | None = None) -> list[dict]:
    """The escrow's log lines in one confirmed transaction, as dicts: "event" (funded, paid, ...), "v" (1 or 2: the
    deployment), "at" (block time), "signer" (the fee payer), "keys" (every account of the transaction), and the
    line's own fields; `knos2:terms` carries its JSON as "json". Only lines the escrow printed itself, in a
    transaction that succeeded."""
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return []
    programs = PROGRAMS if programs is None else programs
    meta, message = tx.get("meta") or {}, tx["transaction"]["message"]
    loaded = meta.get("loadedAddresses") or {}
    keys = [k if isinstance(k, str) else k.get("pubkey") for k in message["accountKeys"]] + list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    out, stack = [], []
    for line in meta.get("logMessages") or []:
        m = _INVOKE.match(line)
        if m:
            del stack[int(m.group(2)) - 1:]
            stack.append(m.group(1))
            continue
        if _DONE.match(line):
            if stack:
                stack.pop()
            continue
        m = _LOG.match(line)
        v = programs.get(stack[-1]) if stack else None
        if not m or v is None or m.group(1) != PREFIX[v]:
            continue
        ev = {"event": m.group(2), "v": v, "at": tx.get("blockTime") or 0, "signer": keys[0], "keys": keys}
        if m.group(2) == "terms":
            ev["json"] = m.group(3) or ""
        else:
            for part in (m.group(3) or "").split():
                k, _, value = part.partition("=")
                ev[k] = int(value) if value.lstrip("-").isdigit() else value
        out.append(ev)
    return out


def history(url: str, program, limit: int = 1000) -> tuple[list[dict], int, bool]:
    """(events oldest first, transactions left unread because the public RPC throttled, whether `limit` cut the
    history short) for one program: its newest `limit` transactions."""
    got = chain.call(url, "getSignaturesForAddress", [str(program), {"limit": limit}], timeout=30) or []
    sigs = [s["signature"] for s in got if s.get("err") is None]
    events, unread = [], 0
    for sig in reversed(sigs):
        try:
            tx = chain.call(url, "getTransaction", [sig, {"encoding": "json", "commitment": "confirmed",
                                                          "maxSupportedTransactionVersion": 0}], timeout=30)
        except Exception:  # noqa: BLE001 - still throttled after the backoff: counted, never guessed
            unread += 1
            continue
        events += [{**ev, "tx": sig} for ev in events_of(tx)]
    return events, unread, len(got) >= limit


# ---- jobs: what was funded and what became of each --------------------------------------------------------------------
def _address(build, *args) -> str | None:
    try:
        return str(build(*args))
    except Exception:  # noqa: BLE001 - not an address (a test's stand-in name): the job is found by repository and issue
        return None


def _key(text) -> Pubkey:
    return Pubkey.from_string(str(text))


def jobs_of(events: list[dict]) -> tuple[list[dict], dict]:
    """(every job the logs show, oldest first; counts of what is not about one job). A job:

        v, repo, issue, amount, faucet, at, tx      the deployment, where, how much, free test money or not, when
        by, owner, source, funder                   the commenter's GitHub id (0: a wallet), the Balance's owner id,
                                                    the Balance or the wallet, and who the record counts as funder
        state                                       open, held, proven (first deployment), paid, refunded
        payee, to, net, fee, paid_at, paid_tx       once paid (payee also once held or proven)
        terms                                       the terms JSON the funding logged (second deployment)

    An event is matched to its job by the job's address among the transaction's accounts; when the address cannot be
    told, by repository and issue, oldest open job first."""
    jobs: list[dict] = []
    live: dict[int, dict] = {}       # id(job) -> job, while it is open, held or proven
    balances: dict[str, dict] = {}   # a Balance's address -> its owner id and authority (second deployment)
    other = {"refunded": 0, "unmatched_paid": 0, "claimed": 0, "claimed_amount": 0, "vetoed": 0, "bound": 0, "balances": 0, "withdrawn": 0, "paused": 0}
    faucet_mint = {1: str(pay.faucet_mint()), 2: str(pay2.faucet_mint())}
    faucet_authority = str(pay2.auth_pda())

    def which(ev: dict) -> dict | None:
        mine = [j for j in live.values() if j["v"] == ev["v"]]
        named = [j for j in mine if j["address"] and j["address"] in ev["keys"]]
        if "repo" in ev:
            named = [j for j in named if (j["repo"], j["issue"]) == (ev["repo"], ev["issue"])]
            mine = [j for j in mine if (j["repo"], j["issue"]) == (ev["repo"], ev["issue"])]
        elif not named:
            return None
        if "amount" in ev and len(named or mine) > 1:      # two jobs on one issue: the one whose money this is
            whole = ev["amount"] + ev.get("fee", 0)
            exact = [j for j in (named or mine) if j["amount"] == whole]
            return (exact or named or mine)[0]
        return (named or mine or [None])[0]

    for ev in events:
        v, name = ev["v"], ev["event"]
        if name == "balance":
            address = _address(lambda e=ev: pay2.balance_pda(e["owner"], _key(e["authority"]), _key(e["mint"])))
            balances[address] = {"owner": ev["owner"], "authority": str(ev["authority"])}        # the wallet whose money it is
            other["balances"] += str(ev["authority"]) != faucet_authority
        elif name == "funded":
            job = {"v": v, "repo": ev.get("repo"), "issue": ev.get("issue"), "amount": ev.get("amount", 0), "at": ev["at"], "tx": ev.get("tx"),
                   "state": "open", "payee": 0, "terms": None}
            if v == 1:      # the repository's own bounty (a comment, faucet money), or a wallet's
                wallet = None if "by" in ev else ev["signer"]
                job.update(by=ev.get("by", 0), owner=0, source=wallet or "", funder=f"gh:{ev['by']}" if "by" in ev else f"wallet:{wallet}",
                           faucet="by" in ev or faucet_mint[1] in ev["keys"],
                           address=_address(lambda e=ev, w=wallet: pay.job_pda(e["repo"], e["issue"], _key(w) if w else None)))
            else:
                source, known = str(ev.get("source", "")), balances.get(str(ev.get("source", "")))
                from_balance = known is not None or bool(ev.get("by"))
                owner = known["owner"] if known else 0
                job.update(by=ev.get("by", 0), owner=owner, source=source, faucet=bool(ev.get("faucet")),
                           funder=f"gh:{owner or ev.get('by', 0)}" if from_balance else f"wallet:{source}",
                           wallet=(known or {}).get("authority") if from_balance else source, from_balance=from_balance,
                           address=_address(lambda e=ev, s=source: pay2.job_pda(e["repo"], e["issue"], _key(s))))
            jobs.append(job)
            live[id(job)] = job
        elif name == "terms":
            if jobs and jobs[-1]["tx"] == ev.get("tx") and jobs[-1]["terms"] is None:
                jobs[-1]["terms"] = ev["json"]
        elif name in ("paid", "held", "proven", "refunded", "vetoed"):
            job = which(ev)
            if name in ("vetoed", "refunded"):
                other[name] += 1
            if job is None:      # its funding is older than the history that was read: counted, and in no one's column
                other["unmatched_paid"] += name == "paid"
                continue
            if name == "paid":
                payee = ev.get("payee", ev.get("author", 0))
                job.update(state="paid", payee=payee, to=str(ev.get("to", "")), net=ev.get("amount", 0), fee=ev.get("fee", 0),
                           paid_at=ev["at"], paid_tx=ev.get("tx"))
                del live[id(job)]
            elif name == "refunded":
                job.update(state="refunded")
                del live[id(job)]
            elif name == "vetoed":
                job.update(state="open", payee=0)
            else:
                job.update(state=name, payee=ev.get("payee", ev.get("author", 0)))
        elif name == "claimed":
            other["claimed"] += 1
            other["claimed_amount"] += ev.get("amount", 0)
        elif name in ("bound", "withdrawn", "paused"):
            other[name] += 1
    return jobs, other


def kind_of(job: dict, own: frozenset = OWN, own_wallets: frozenset = OWN_WALLETS) -> str:
    """own, self, test or outside: which of the four a job is counted under (the module's docstring has the rules).
    The wallet behind a job is the one that funded it, or the one that opened the Balance a comment spent. A GitHub id
    of 0 means "a wallet, no account": two zeros are not the same person."""
    wallet = job.get("wallet") or (job["source"] if job["v"] == 1 and not job["by"] else None)
    if {job["by"], job["owner"], job["payee"]} & own or {wallet, job.get("to")} & own_wallets:
        return "own"
    back = bool(wallet) and job.get("to") == wallet      # paid to the wallet whose money it was (a Balance's or a wallet's own)
    if job["state"] == "paid" and (back or (job["payee"] != 0 and job["payee"] in (job["owner"], job["by"]))):
        return "self"
    return "test" if job["faucet"] else "outside"


def _spread(seconds: list) -> dict:
    """{count, median, p90, slowest} of a list of seconds; the three are None when there is nothing to measure."""
    took = sorted(int(s) for s in seconds if s is not None and s >= 0)
    if not took:
        return {"count": 0, "median": None, "p90": None, "slowest": None}
    return {"count": len(took), "median": int(statistics.median(took)), "p90": took[math.ceil(0.9 * len(took)) - 1], "slowest": took[-1]}


def summarize(events: list[dict], own: frozenset = OWN, own_wallets: frozenset = OWN_WALLETS) -> dict:
    """The counts of stats.json that the chain alone gives: outside, apart, totals, by_deployment, the funnel's last
    three stages, funded_to_paid and the newest payments."""
    jobs, other = jobs_of(events)
    for j in jobs:
        j["kind"] = kind_of(j, own, own_wallets)

    def side(kind: str) -> dict:
        mine = [j for j in jobs if j["kind"] == kind]
        paid = [j for j in mine if j["state"] == "paid"]
        again = set()
        for j in mine:      # a funder who funded again after one of their jobs was paid
            if any(p["funder"] == j["funder"] and p is not j and p["paid_at"] < j["at"] for p in paid):
                again.add(j["funder"])
        return {"funded": len(mine), "completed": len(paid), "funders": len({j["funder"] for j in mine}), "payees": len({j["payee"] for j in paid}),
                "repeat_funders": len(again), "repositories": len({j["repo"] for j in mine}),
                "funded_amount": sum(j["amount"] for j in mine), "paid_amount": sum(j["net"] for j in paid), "fees": sum(j["fee"] for j in paid)}

    sides = {kind: side(kind) for kind in ("outside", "own", "self", "test")}
    paid = [j for j in jobs if j["state"] == "paid"]
    state = lambda name: sum(1 for j in jobs if j["state"] == name)  # noqa: E731
    return {
        "outside": sides["outside"],
        "apart": {"own": sides["own"], "self": sides["self"], "test": sides["test"]},
        "totals": {"funded": len(jobs), "completed": len(paid) + other["unmatched_paid"], "open": state("open") + state("proven"),
                   "held": state("held"), "funded_amount": sum(j["amount"] for j in jobs), "paid_amount": sum(j["net"] for j in paid), **other},
        "by_deployment": {name: {"funded": sum(1 for j in jobs if j["v"] == v), "completed": sum(1 for j in paid if j["v"] == v)}
                          for v, name in ((1, "first"), (2, "second"))},
        # stage 1 (installed) is GitHub's to say: see installed()
        "funnel": {"installed": None, "installed_note": "not measured",
                   "funded": len({j["repo"] for j in jobs if j["kind"] != "own"}),
                   "completed": sides["outside"]["completed"], "funded_again": sides["outside"]["repeat_funders"]},
        "latency": {"funded_to_paid": _spread([j["paid_at"] - j["at"] for j in paid if j["at"] and j["paid_at"]])},
        "recent": [{"deployment": j["v"], "repo": j["repo"], "issue": j["issue"], "payee": j["payee"], "funder": j["funder"], "amount": j["net"],
                    "fee": j["fee"], "kind": j["kind"], "tx": j["paid_tx"], "at": j["paid_at"], "seconds": j["paid_at"] - j["at"] if j["at"] else None}
                   for j in sorted(paid, key=lambda j: j["paid_at"])[-20:][::-1]],
    }


# ---- the relay log, and the latency a person saw ---------------------------------------------------------------------
_RELAY = re.compile(r"^knos-relay (\w+) (\S+?)#(\d+) ([0-9a-f]{16}) ok (.*)$")


def _unix(stamp) -> int | None:
    """GitHub's timestamp (2026-10-02T12:00:00Z), or seconds already, as unix seconds."""
    if isinstance(stamp, (int, float)) or str(stamp).isdigit():
        return int(stamp)
    try:
        return int(datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def relay_lines(comments: list[dict]) -> list[dict]:
    """The successful lines of the relay log, oldest first. `comments` are the log issue's comments as GitHub returns
    them (each "body" holds one line per token, "created_at" is when the relay posted them). A line:

        knos-relay <kind> <owner/repo>#<n> <token id> ok [asked=<unix>] sig=<s1>[,<s2>...] note=<words> [t=<seconds>]

    gives {"kind", "repo", "number", "token", "sigs", "posted" (the comment's time), "asked" (None when the line does
    not carry it), "t" (the relay's own seconds: None when the line does not end in ` t=<seconds>`)}. The note is free
    text, so `t=` is read from the end of the line, never from inside the note. Lines that failed, and the crank's own
    lines (no repository), are left out."""
    out = []
    for c in comments:
        posted = _unix(c.get("created_at"))
        for line in (c.get("body") or "").splitlines():
            m = _RELAY.match(line.strip())
            if not m:
                continue
            fields = dict(part.split("=", 1) for part in re.split(r"(?:^| )note=", m.group(5), maxsplit=1)[0].split() if "=" in part)
            sigs = [s for s in fields.get("sig", "").split(",") if s and s != "none"]
            took = re.search(r"\st=(\d+(?:\.\d+)?)$", " " + m.group(5))
            out.append({"kind": m.group(1), "repo": m.group(2), "number": int(m.group(3)), "token": m.group(4), "sigs": sigs,
                        "posted": posted, "asked": _unix(fields["asked"]) if "asked" in fields else None,
                        "t": float(took.group(1)) if took else None})
    return sorted(out, key=lambda r: r["posted"] or 0)


_FUND = re.compile(r"^\s{0,3}/knos\s+(?:fund|bounty)\b", re.I | re.M)


def asked_at(line: dict, done: int, get) -> int | None:
    """When the person acted: the newest `/knos fund` comment on the issue before the funding landed (or the issue
    itself, when its description carries the command), or the pull request's merge. `get(path)` reads GitHub's API and
    raises when it cannot. None when GitHub does not say."""
    if line["asked"] is not None:
        return line["asked"]
    try:
        if line["kind"] == "fund":
            issue = get(f"repos/{line['repo']}/issues/{line['number']}")
            made = [issue] if _FUND.search(issue.get("body") or "") else []
            made += [c for c in get(f"repos/{line['repo']}/issues/{line['number']}/comments?per_page=100") if _FUND.search(c.get("body") or "")]
            times = [t for t in (_unix(c.get("created_at")) for c in made) if t is not None and t <= done]
            return max(times) if times else None
        if line["kind"] in ("proof", "pay"):
            return _unix(get(f"repos/{line['repo']}/pulls/{line['number']}").get("merged_at"))
    except Exception:  # noqa: BLE001 - GitHub said no, or did not answer: not measured
        return None
    return None


def latency(lines: list[dict], events: list[dict], get=None, most: int = 200) -> dict:
    """comment_to_funded and merge_to_paid over the relay log's lines (the newest `most` of each kind). The end of
    each is the block time of the line's transaction that funded or paid; a line whose transaction the chain's
    history does not show, or whose start GitHub does not give, is not measured."""
    landed = {}
    for ev in events:
        if ev["event"] in ("funded", "paid") and ev.get("tx"):
            landed.setdefault((ev["tx"], "fund" if ev["event"] == "funded" else "pay"), ev["at"])
    out = {}
    for name, kinds, what in (("comment_to_funded", ("fund",), "fund"), ("merge_to_paid", ("proof", "pay"), "pay")):
        took = []
        for line in [r for r in lines if r["kind"] in kinds][-most:]:
            done = next((landed[(s, what)] for s in line["sigs"] if (s, what) in landed), None)
            start = asked_at(line, done, get) if done is not None and (get is not None or line["asked"] is not None) else None
            if start is not None and done >= start:
                took.append(done - start)
        out[name] = _spread(took)
    return out


def relay_seconds(lines: list[dict], most: int = 200) -> dict:
    """{fund, pay}: the relay's own part of the wait, as {count, median, p90, slowest}, from the ` t=<seconds>` of the
    relay log's lines (the newest `most` of each kind that sent a transaction and wrote one). It starts at the comment
    that carried the token, not at the person's own comment or at the merge, so it is shorter than latency()'s two."""
    return {name: _spread([r["t"] for r in [r for r in lines if r["kind"] in kinds and r["sigs"] and r.get("t") is not None][-most:]])
            for name, kinds in (("fund", ("fund",)), ("pay", ("proof", "pay")))}


# ---- GitHub ----------------------------------------------------------------------------------------------------------
def github(path: str, token: str | None = None):
    """GET api.github.com/<path>, as JSON. Raises when GitHub refuses or does not answer."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "knos-network-stats"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request("https://api.github.com/" + path, headers=headers), timeout=30) as r:  # noqa: S310 - a fixed https host
        return json.load(r)


def relay_log(get, repo: str = RELAY_LOG_REPO, pages: int = 20) -> list[dict]:
    """Every comment of the relay's log issues in `repo` (open or closed), oldest first."""
    out = []
    for issue in get(f"repos/{repo}/issues?labels=knos-relay&state=all&per_page=100"):
        for page in range(1, pages + 1):
            batch = get(f"repos/{repo}/issues/{issue['number']}/comments?per_page=100&page={page}")
            out += batch
            if len(batch) < 100:
                break
    return out


def installed(get, own: frozenset = OWN, queries=INSTALLED_QUERIES) -> dict:
    """{"repositories": how many public repositories that are not Knos's own have a workflow file that calls Knos's,
    "ids": their GitHub ids, "incomplete": whether GitHub said the search was cut short}. Code search needs a token."""
    found, incomplete = {}, False
    for q in queries:
        for page in range(1, 11):       # GitHub serves the first 1,000 results of a search
            got = get(f"search/code?q={urllib.parse.quote(q)}&per_page=100&page={page}")
            incomplete = incomplete or bool(got.get("incomplete_results"))
            items = got.get("items") or []
            for it in items:
                repo = it.get("repository") or {}
                if str(it.get("path", "")).startswith(".github/workflows/") and not repo.get("private") and not repo.get("fork") \
                        and (repo.get("owner") or {}).get("id") not in own:
                    found[repo.get("id")] = repo.get("full_name")
            if len(items) < 100:
                break
    return {"repositories": len(found), "ids": sorted(i for i in found if i is not None), "incomplete": incomplete}


# ---- what the chain holds now ----------------------------------------------------------------------------------------
def live(ledger) -> dict:
    """What is in escrow now, from the programs' accounts."""
    first = [pay.read_job(d) for _, d in ledger.program_accounts(pay.PAY_ID, 256)]
    dues = [pay.read_due(d) for _, d in ledger.program_accounts(pay.PAY_ID, 48)]
    second = [pay2.read_job(d) for _, d in ledger.program_accounts(pay2.PAY_ID, pay2.JOB_LEN)]
    count = lambda jobs, state: sum(1 for j in jobs if j and j.state == state)                # noqa: E731
    amount = lambda jobs, state: sum(j.amount for j in jobs if j and j.state == state)        # noqa: E731
    return {"first": {"open": count(first, "open"), "open_amount": amount(first, "open"), "proven_waiting": count(first, "proven"),
                      "unclaimed_accounts": sum(1 for d in dues if d), "unclaimed_amount": sum(dues)},
            "second": {"open": count(second, "open"), "open_amount": amount(second, "open"), "held": count(second, "held"),
                       "held_amount": amount(second, "held")}}


def collect(url: str, get=None, token: str | None = None, limit: int = 1000, now: float | None = None) -> dict:
    """stats.json. `get(path)` reads GitHub (None: do not ask it). Whatever could not be read is said in "error" or in
    a note beside the number; a number that could not be measured is null, never a guess."""
    data: dict = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)), "cluster": "devnet",
                  "programs": {"first": {"knos_oidc": str(oidc.OIDC_ID), "knos_pay": str(pay.PAY_ID)},
                               "second": {"knos_oidc": str(oidc2.OIDC_ID), "knos_pay": str(pay2.PAY_ID)}}}
    events: list[dict] = []
    try:
        problems = []
        for program in (pay.PAY_ID, pay2.PAY_ID):
            got, unread, cut = history(url, program, limit)
            events += got
            if unread:
                problems.append(f"{unread} transactions unread (the public RPC throttled); counts are a lower bound")
            if cut:
                problems.append(f"only the newest {limit} transactions of {program} were read")
        events.sort(key=lambda ev: ev["at"])        # stable: one transaction's lines stay in order
        data.update(summarize(events))
        data["live"] = live(chain.Ledger(url))
        if problems:
            data["error"] = "; ".join(problems)
    except Exception as e:  # noqa: BLE001 - the site still ships, and says the numbers could not be read
        data["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return data
    note = "over the lines of the public relay log"
    if get is None:
        data["latency"].update(latency([], events), relay=relay_seconds([]), note="not measured: GitHub was not asked")
    else:
        try:
            lines = relay_lines(relay_log(get))
            data["latency"].update(latency(lines, events, get), relay=relay_seconds(lines), note=note)
        except Exception as e:  # noqa: BLE001
            data["latency"].update(latency([], events), relay=relay_seconds([]), note=f"not measured: the relay log could not be read ({type(e).__name__})")
        if token:
            try:
                found = installed(get)
                funded = {j["repo"] for j in jobs_of(events)[0]}
                data["funnel"].update(installed=found["repositories"], funded_of_installed=len(funded & set(found["ids"])),
                                      installed_note="GitHub code search: public repositories whose workflow files call Knos's"
                                      + (" (GitHub cut the search short: a lower bound)" if found["incomplete"] else ""))
            except Exception as e:  # noqa: BLE001
                data["funnel"]["installed_note"] = f"not measured: GitHub's code search did not answer ({type(e).__name__})"
        else:
            data["funnel"]["installed_note"] = "not measured: GitHub's code search needs a token, and this build had none"
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site/stats.json")
    ap.add_argument("--limit", type=int, default=1000, help="the newest transactions of each program to read")
    ap.add_argument("--no-github", action="store_true", help="count from the chain only")
    a = ap.parse_args()
    url = os.environ.get("KNOS_RPC") or chain.CLUSTERS["devnet"]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None
    data = collect(url, None if a.no_github else (lambda path: github(path, token)), token, a.limit)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(json.dumps(data, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
