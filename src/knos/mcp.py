"""`knos mcp`: paid work and claim checks for a coding agent, over the Model Context Protocol on stdio.

    knos_bounties   the open bounties in escrow, largest first: work an agent can take
    knos_bounty     what is in escrow for one issue: each job's state and its terms in words
    knos_check_pr   whether a pull request's "tests pass" is true at its head commit, on GitHub's record
    knos_due        where a GitHub account is paid, what is held for it, its record, and the balances set aside for
                    its repositories

The money tools read both deployments. New funding goes to the second (programs-v2: knos.settle.v2), so that is
where open work is; a job funded on the first (knos.settle) finishes there, and its rows say `"deployment": 1`.
Every tool only reads public data (Solana and GitHub): no key, no wallet, nothing written. `knos init` registers
this server with the agents on the machine.

The protocol is written out here, with no SDK: one JSON-RPC 2.0 message per line on stdin and stdout, and nothing but
those messages on stdout. Two eras of clients are answered. One opens with `initialize` and is told the protocol
version it asked for; the other (revision 2026-07-28) keeps no session and names its version in every request's
`_meta`. Starting the server opens no connection: the chain and GitHub are asked only when a tool needs them.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

from . import version

PROTOCOL = "2026-07-28"
SESSION_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")   # what an `initialize` may ask for
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_KEY = "io.modelcontextprotocol/serverInfo"
LIST_TTL_MS = 300_000   # the tool list only changes with a new release

INSTRUCTIONS = (
    "Knos pays coding agents for merged pull requests: a maintainer funds a GitHub issue, the money waits in a "
    "program on Solana devnet, and the author of the pull request that meets the bounty's terms is paid: at the "
    "wallet bound to their GitHub account, else at the address they gave on the pull request, else it is held for "
    "their account. knos_bounties lists the paid work you can take now; knos_bounty says what one bounty asks for. "
    "A \"tests pass\" or \"CI is green\" in a pull request description is checked against GitHub's own record of the "
    "head commit, so say it only when it is true.")

CLAIM = "`knos claim <address>` or at https://drexthealpha.github.io/Knos/#claim"
HOW = ("Open a pull request whose description says `Fixes #<issue>`. No wallet is needed to start: the payment goes "
       "to the wallet bound to the pull request author's GitHub account, else to the address in their "
       "`/knos address <Solana address>` comment on the pull request, and with neither it is held for that account "
       f"for 180 days. A wallet is bound with {CLAIM}.")
PAID_WHEN = {0: "a maintainer merges the pull request that closes the issue",
             1: "the funder's acceptance checks pass on a pull request"}
SAID = {"tests": "tests pass", "ci": "CI is green"}   # the claims GitHub's record can settle

# An owner is a GitHub login; a repository name is letters, digits, "_", "-" and ".", and never "." or "..". So a
# value a model sends can only ever name repos/<owner>/<name>: it cannot climb to another path of GitHub's API.
_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.{1,2}(?![\w.-]))[\w.-]{1,100}"
_ISSUE = re.compile(r"(" + _NAME + r")#(\d{1,10})")
_PULL = re.compile(r"https?://github\.com/(" + _NAME + r")/pull/(\d{1,10})(?:[/?#].*)?")
_REPO = re.compile(_NAME)
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(\[bot\])?")


def _tool(name: str, title: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "title": title, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "openWorldHint": True}}


TOOLS = [
    _tool("knos_bounties", "Open bounties",
          "Paid work you can take: the open bounties in escrow on Solana devnet, largest first. Each is one GitHub "
          "issue, with its title, labels and whether it is assigned; the author of the pull request that closes it is "
          "paid to their GitHub account. Test USDC.",
          {"repo": {"type": "string", "description": "only this repository, as owner/name"},
           "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20,
                     "description": "how many to return"}}, []),
    _tool("knos_bounty", "Bounty on one issue",
          "What is in escrow for one GitHub issue: each bounty's amount, its state (open, or held for its "
          "payee, who has bound no wallet yet), its deadline, and its terms in plain sentences: which checks must pass, which files a pull request "
          "may change, and whether the issue can be reserved.",
          {"issue": {"type": "string", "description": "the issue, as owner/repo#number"}}, ["issue"]),
    _tool("knos_check_pr", "Check a pull request's claims",
          "Whether a pull request's description agrees with GitHub's record of its tests: what it claims (tests pass, CI is "
          "green) against the finished checks GitHub recorded at its head commit.",
          {"pr": {"type": "string", "description": "owner/repo#number, or the pull request's github.com URL"}},
          ["pr"]),
    _tool("knos_due", "Waiting for a GitHub account",
          "Where a GitHub account is paid (the wallet bound to it), what is held for it until it binds one, its "
          "public record of payments, the balances set aside for bounties in its repositories, what the first "
          "deployment still holds for it, and how to claim.",
          {"login": {"type": "string", "description": "a GitHub login"}}, ["login"]),
]


class Failed(Exception):
    """A tool could not answer. Its text is the one sentence the agent is told; never a traceback."""


class Missing(Failed):
    """GitHub answered: there is no such thing (404)."""


class Refused(Exception):
    """A request the protocol does not allow: answered with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _github(path: str):
    """GET api.github.com/<path>. Public data; GH_TOKEN or GITHUB_TOKEN only lifts the rate limit."""
    req = urllib.request.Request(f"https://api.github.com/{path}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "knos"})
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - api.github.com
        return json.loads(resp.read())


def _line(why: BaseException) -> str:
    """An error as a few words: its first line only, so a sentence built on it stays one sentence."""
    first = next((x for x in str(why).splitlines() if x.strip()), "")
    return " ".join(first.split())[:200].rstrip(".") or type(why).__name__


def _usdc(units: int) -> str:
    """Six-decimal token units as a decimal string, exact: 20000000 -> "20.00", 1234567 -> "1.234567"."""
    whole, part = divmod(units, 1_000_000)
    return f"{whole}.{f'{part:06d}'.rstrip('0').ljust(2, '0')}"


def _iso(t: int) -> str | None:
    try:
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _arguments(tool: dict, given) -> dict:
    """The call's arguments, held to the tool's own schema: no unknown names, required ones present, types and
    ranges right, defaults filled in."""
    name, schema = tool["name"], tool["inputSchema"]
    if given is None:
        given = {}
    if not isinstance(given, dict):
        raise Failed(f"{name} takes its arguments as an object.")
    unknown = sorted(set(given) - set(schema["properties"]))
    if unknown:
        raise Failed(f"{name} has no argument named {unknown[0]}.")
    out = {}
    for key, spec in schema["properties"].items():
        if key not in given:
            if key in schema["required"]:
                raise Failed(f"{name} needs {key}: {spec['description']}.")
            if "default" in spec:
                out[key] = spec["default"]
            continue
        value = given[key]
        if spec["type"] == "string":
            if not isinstance(value, str) or not value.strip():
                raise Failed(f"{name}: {key} must be text ({spec['description']}).")
            value = value.strip()
        elif not isinstance(value, int) or isinstance(value, bool) or not spec["minimum"] <= value <= spec["maximum"]:
            raise Failed(f"{name}: {key} must be a whole number from {spec['minimum']} to {spec['maximum']}.")
        out[key] = value
    return out


class Server:
    """Answers one message at a time. `ledger` and `github` are given by tests; otherwise the cluster's RPC endpoint
    (made on first use) and api.github.com."""

    def __init__(self, ledger=None, github=None):
        self._ledger = ledger
        self._get = github or _github
        self._names: dict[int, str] = {}   # repository id -> owner/name, for as long as the server runs
        self._methods = {"initialize": self._initialize, "ping": lambda _p: {}, "server/discover": self._discover,
                         "tools/list": self._list, "tools/call": self._call}
        self._tools = {"knos_bounties": self._bounties, "knos_bounty": self._bounty,
                       "knos_check_pr": self._check_pr, "knos_due": self._due}

    # -- the protocol -------------------------------------------------------------------------------------------
    def line(self, text: str):
        """The reply to one line from the client: a message, a list of them (a batch), or None for no reply."""
        try:
            msg = json.loads(text)
        except (ValueError, RecursionError):     # not JSON, or nested deep enough to exhaust the parser
            return _error(None, -32700, "Parse error")
        if isinstance(msg, list):   # clients before 2025-06-18 may send several at once
            if not msg:
                return _error(None, -32600, "Invalid Request")
            return [r for r in map(self.answer, msg) if r is not None] or None
        return self.answer(msg)

    def answer(self, msg) -> dict | None:
        if not isinstance(msg, dict):
            return _error(None, -32600, "Invalid Request")
        method, rid = msg.get("method"), msg.get("id")
        if not isinstance(method, str):   # a reply to something this server never asked, or not a request at all
            return None if "result" in msg or "error" in msg else _error(rid, -32600, "Invalid Request")
        if "id" not in msg:
            return None                   # a notification (initialized, cancelled, ...) is never answered
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return _error(rid, -32602, "params must be an object")
        meta = params.get("_meta")
        if isinstance(meta, dict) and VERSION_KEY in meta and meta[VERSION_KEY] != PROTOCOL:
            return _error(rid, -32022, "Unsupported protocol version",
                          {"supported": [PROTOCOL], "requested": meta[VERSION_KEY]})
        handler = self._methods.get(method)
        if handler is None:
            return _error(rid, -32601, f"Method not found: {method}")
        try:
            return {"jsonrpc": "2.0", "id": rid, "result": handler(params)}
        except Refused as no:
            return _error(rid, no.code, no.message, no.data)
        except Exception as why:  # noqa: BLE001 - a fault here must not end the agent's session
            print(f"knos mcp: {method}: {type(why).__name__}: {_line(why)}", file=sys.stderr)
            return _error(rid, -32603, "Internal error")

    def _initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in SESSION_PROTOCOLS else SESSION_PROTOCOLS[-1],
                "capabilities": {"tools": {}}, "serverInfo": _server_info(), "instructions": INSTRUCTIONS}

    def _discover(self, _params: dict) -> dict:
        return {"resultType": "complete", "supportedVersions": [PROTOCOL], "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS, "_meta": {SERVER_KEY: _server_info()}}

    def _list(self, _params: dict) -> dict:
        return {"resultType": "complete", "tools": TOOLS, "ttlMs": LIST_TTL_MS, "cacheScope": "public"}

    def _call(self, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or name not in self._tools:
            raise Refused(-32602, f"Unknown tool: {name}" if isinstance(name, str) else "tools/call needs a tool name")
        try:
            tool = next(t for t in TOOLS if t["name"] == name)
            got = self._tools[name](_arguments(tool, params.get("arguments")))
        except Failed as why:
            return {"resultType": "complete", "content": [{"type": "text", "text": str(why)}], "isError": True}
        except Exception as why:  # noqa: BLE001 - the agent gets a sentence, the person running it gets the cause
            print(f"knos mcp: {name}: {type(why).__name__}: {_line(why)}", file=sys.stderr)
            return {"resultType": "complete", "isError": True,
                    "content": [{"type": "text", "text": f"{name} could not answer ({type(why).__name__})."}]}
        return {"resultType": "complete", "content": [{"type": "text", "text": json.dumps(got, indent=1)}],
                "structuredContent": got, "isError": False}

    # -- what the tools read ------------------------------------------------------------------------------------
    def _ask(self, path: str):
        try:
            return self._get(path)
        except Exception as why:  # noqa: BLE001 - whatever went wrong, the agent is told in a sentence
            code = getattr(why, "code", None)
            if code == 404:
                raise Missing(f"GitHub has nothing at {path}.") from None
            if code in (403, 429):   # almost always the rate limit: 60 requests an hour without a token
                raise Failed(f"GitHub refused {path} (HTTP {code}); set GH_TOKEN to lift its rate limit.") from None
            raise Failed(f"GitHub did not answer for {path}: {_line(why)}.") from None

    def _field(self, path: str, *keys: str):
        """One value out of GitHub's answer, e.g. the id of repos/owner/name."""
        got = self._ask(path)
        try:
            for k in keys:
                got = got[k]
        except (KeyError, TypeError, IndexError):
            raise Failed(f"GitHub's answer for {path} has no {'.'.join(keys)}.") from None
        return got

    def _chain(self, read):
        """`read(ledger)`, with the ledger made on first use. A cluster that does not answer is a sentence."""
        try:
            if self._ledger is None:
                from . import chain
                self._ledger = chain.ledger()
            return read(self._ledger)
        except Exception as why:  # noqa: BLE001 - RPC errors, timeouts, a refused cluster
            raise Failed(f"Solana {_cluster()} did not answer: {_line(why)}.") from None

    def _row(self, address, job, repo: str | None) -> dict:
        """A job of the first deployment."""
        from .settle import pay
        return {"repo": repo, "issue": job.issue,
                "url": f"https://github.com/{repo}/issues/{job.issue}" if repo else None,
                "amount_usdc": _usdc(job.amount), "net_usdc": _usdc(job.amount - pay.fee_of(job.amount)),
                "paid_when": PAID_WHEN.get(job.mode, "unknown"), "refunded_after": _iso(job.deadline),
                "job": str(address), "deployment": 1}

    def _row2(self, address, job, repo: str | None) -> dict:
        """A job of the second deployment. Its money is named: a job can be funded in any mint, and only the devnet
        faucet's and Circle's devnet USDC are "test USDC" (amount_usdc is null for any other)."""
        from .settle.v2 import pay
        usdc = _is_usdc(job.mint)
        return {"repo": repo, "issue": job.issue,
                "url": f"https://github.com/{repo}/issues/{job.issue}" if repo else None,
                "amount_usdc": _usdc(job.amount) if usdc else None,
                "net_usdc": _usdc(job.amount - pay.fee_of(job.amount)) if usdc else None,
                "paid_when": PAID_WHEN.get(job.mode, "unknown"), "refunded_after": _iso(job.deadline),
                "job": str(address), "deployment": 2, "money": _money(job.mint), "amount_units": job.amount,
                "funded_from": "a balance, by a comment" if job.from_balance else "a wallet"}

    def _name(self, repo_id: int, asking: list) -> str | None:
        """owner/name of a repository id, asked of GitHub once. `asking` ([True]) is cleared when GitHub does not
        answer, so one list waits for it once and not once per row."""
        if repo_id not in self._names and asking[0]:
            try:
                self._names[repo_id] = str(self._field(f"repositories/{repo_id}", "full_name"))
            except Missing:
                pass                 # a repository that is gone; the others are still asked
            except Failed:
                asking[0] = False    # GitHub is not answering: one wait, not one per row
        return self._names.get(repo_id)

    def _terms(self, address, job) -> list[str] | None:
        """A second-deployment job's terms in plain sentences (knos.terms.describe), from the JSON its funding
        transaction logged, held to the hash the job stores. None when the chain does not give them."""
        from . import flow, terms
        from .settle.v2 import pay
        def logged(ledger):     # the cluster's ledger takes only what knos-pay itself logged; a plainer one gives its line
            return ledger.terms_of(address) if hasattr(ledger, "terms_of") else flow._logged(flow.Run("", {}, ledger=ledger), address, bytes(job.terms))
        try:
            raw = self._chain(logged)
            if not raw or pay.terms_hash(bytes(raw)) != bytes(job.terms):
                return None
            return terms.describe(terms.parse(bytes(raw)))
        except (Failed, terms.Refused):
            return None

    def _about(self, repo: str | None, issue: int) -> dict:
        """What the issue asks for: its title, its label names and whether anyone is assigned (an assigned issue pays
        only its assignee). One request, none when the repository did not resolve; all three are null when GitHub
        did not answer, so the bounty is still listed."""
        about = {"title": None, "labels": None, "assigned": None}
        if repo is None:
            return about
        try:
            got = self._ask(f"repos/{repo}/issues/{issue}")
            title, names = got["title"], [x["name"] for x in got["labels"]]
            if not isinstance(title, str) or not all(isinstance(n, str) for n in names):
                raise TypeError("title and label names are text")   # null is "GitHub did not say", never the word None
            about = {"title": title, "labels": names, "assigned": bool(got.get("assignees") or got.get("assignee"))}
        except (Failed, KeyError, TypeError):
            pass
        return about

    # -- the tools ----------------------------------------------------------------------------------------------
    def _bounties(self, args: dict) -> dict:
        from .settle import pay as pay1
        from .settle.v2 import pay
        only = None
        if "repo" in args:
            if not _REPO.fullmatch(args["repo"]):
                raise Failed("knos_bounties: repo must be owner/name, e.g. octo/widgets.")
            only = int(self._field(f"repos/{args['repo']}", "id"))
            self._names[only] = args["repo"]
        second = self._chain(lambda ledger: ledger.program_accounts(pay.PAY_ID, pay.JOB_LEN, {0: bytes([1])}))
        first = self._chain(lambda ledger: ledger.program_accounts(pay1.PAY_ID, 256, {0: bytes([1])}))
        now = self._chain(lambda ledger: ledger.now())
        jobs = [(2, addr, j) for addr, j in ((addr, pay.read_job(data)) for addr, data in second) if j and j.state == "open"]
        jobs += [(1, addr, j) for addr, j in ((addr, pay1.read_job(data)) for addr, data in first) if j]
        jobs = [x for x in jobs if x[2].deadline > now and (only is None or x[2].repo_id == only)]   # past its deadline: being refunded
        # test USDC first, largest first: anyone can fund a job in a token of their own making, and its number says nothing
        jobs.sort(key=lambda x: (not (x[0] == 1 or _is_usdc(x[2].mint)), -x[2].amount, x[2].deadline, str(x[1])))
        rows, asking = [], [True]
        for which, addr, j in jobs[:args["limit"]]:
            repo = self._name(j.repo_id, asking)
            row = self._row2(addr, j, repo) if which == 2 else self._row(addr, j, repo)
            row.update(self._about(row["repo"], j.issue))
            if row["repo"] is None:
                row["repo_id"] = j.repo_id
            rows.append(row)
        return {"bounties": rows, "open": len(jobs), "how": HOW, "cluster": _cluster(), "note": _note()}

    def _bounty(self, args: dict) -> dict:
        from .settle import relay as relay1
        from .settle.v2 import relay
        m = _ISSUE.fullmatch(args["issue"])
        if not m:
            raise Failed("knos_bounty: name the issue as owner/repo#number, e.g. octo/widgets#7.")
        repo, issue = m.group(1), int(m.group(2))
        repo_id = int(self._field(f"repos/{repo}", "id"))
        jobs = self._chain(lambda ledger: relay.jobs_for(ledger, repo_id, issue))
        old = self._chain(lambda ledger: relay1.jobs_for(ledger, repo_id, issue))
        now = self._chain(lambda ledger: ledger.now()) if jobs or any(j.state == "proven" for _a, j in old) else 0
        rows = []
        for addr, j in sorted(jobs, key=lambda x: (not _is_usdc(x[1].mint), -x[1].amount, str(x[0]))):
            row = {**self._row2(addr, j, repo), "state": j.state, "mode": "merge" if j.mode == 0 else "tests"}
            said = self._terms(addr, j)
            row["terms"] = said
            if said is None:
                row["terms_note"] = "its terms could not be read from Solana just now; `/knos status` on the issue says them"
            if j.state == "held":       # the money waits for its payee to bind a wallet
                row.update(held_for_user_id=j.payee_id, held_until=_iso(j.hold_until), refunded_after=_iso(j.hold_until))
            elif j.deadline <= now:
                row["state"] = "past its deadline: it pays nobody now and goes back to its funder"
            rows.append(row)
        for addr, j in sorted(old, key=lambda x: -x[1].amount):
            row = {**self._row(addr, j, repo), "state": j.state, "mode": "merge" if j.mode == 0 else "tests"}
            if j.state == "proven":
                row.update(author_id=j.author_id, released_in_s=max(0, j.pay_after - now))
            rows.append(row)
        said = (f"{len(rows)} bounty in escrow for {repo}#{issue}." if len(rows) == 1 else
                f"{len(rows)} bounties in escrow for {repo}#{issue}." if rows else
                f"no bounty in escrow for {repo}#{issue}; a maintainer funds one by commenting /knos fund 20 on it.")
        return {"issue": f"{repo}#{issue}", "bounties": rows, "said": said, "cluster": _cluster()}

    def _check_pr(self, args: dict) -> dict:
        from . import judge
        from .proof import claims
        m = _ISSUE.fullmatch(args["pr"]) or _PULL.fullmatch(args["pr"])
        if not m:
            raise Failed("knos_check_pr: name the pull request as owner/repo#number or by its github.com URL.")
        repo, n = m.group(1), int(m.group(2))
        pr = self._ask(f"repos/{repo}/pulls/{n}")
        if not isinstance(pr, dict) or not isinstance(pr.get("head"), dict) or not pr["head"].get("sha"):
            raise Failed(f"GitHub's answer for {repo}#{n} is not a pull request.")
        head, body = str(pr["head"]["sha"]), pr.get("body") or ""
        out = {"pr": f"{repo}#{n}", "head": head, "failed_checks": []}
        kinds = sorted(claims.read(body).kinds & set(SAID))
        out["claims"] = [SAID[k] for k in kinds]
        if not kinds:
            return {**out, "verdict": "no claim",
                    "said": "The description does not say that tests pass or that CI is green."}
        claimed = " and ".join(sorted(out["claims"]))
        runs = judge.check_runs(repo, head, body=body, get=lambda url: self._get(url.split("api.github.com/", 1)[1]))
        if runs is None:
            raise Failed(f"GitHub did not answer for the checks of {head[:12]}, so the claim could not be checked.")
        # One run at a time through the judge's own rule, so "failed" means here exactly what it means at the gate.
        out["failed_checks"] = sorted({str(r.get("name", "?")) for r in runs if judge.claim_check(body, [r])})
        others = [r for r in runs if not judge._ours(r)]
        if judge.claim_check(body, runs):
            return {**out, "verdict": "false", "said": f"The description says {claimed}, but these checks failed at "
                                                       f"the head commit: {', '.join(out['failed_checks'])}."}
        if any(r.get("status") != "completed" for r in others):
            return {**out, "verdict": "checks still running",
                    "said": f"The description says {claimed}; no finished check failed, and some are still running."}
        if not others:
            return {**out, "verdict": "no checks", "said": f"The description says {claimed}, but GitHub has no check "
                                                           "at the head commit to hold that against."}
        return {**out, "verdict": "true",
                "said": f"The description says {claimed}, and no finished check failed at the head commit."}

    def _due(self, args: dict) -> dict:
        from .settle import pay as pay1
        from .settle import relay as relay1
        from .settle.v2 import pay, relay
        login = args["login"].lstrip("@")
        if not _LOGIN.fullmatch(login):
            raise Failed("knos_due: login must be a GitHub login, e.g. octocat.")
        user_id = int(self._field(f"users/{login}", "id"))
        bound = pay.read_bind(self._chain(lambda ledger: ledger.account(pay.bind_pda(user_id))))
        rep = pay.read_rep(self._chain(lambda ledger: ledger.account(pay.rep_pda(user_id))))
        held = self._chain(lambda ledger: relay.held_for(ledger, user_id))
        balances = self._chain(lambda ledger: relay.balances_for(ledger, user_id))
        dues = self._chain(lambda ledger: relay1.dues_for(ledger, user_id))
        rep1 = pay1.read_rep(self._chain(lambda ledger: ledger.account(pay1.rep_pda(user_id))))
        asking, waits = [True], []
        for addr, j in held:
            usdc, net = _is_usdc(j.mint), j.amount - pay.fee_of(j.amount)
            repo = self._name(j.repo_id, asking)
            waits.append({"repo": repo, "repo_id": j.repo_id, "issue": j.issue, "net_usdc": _usdc(net) if usdc else None,
                          "money": _money(j.mint), "net_units": net, "held_until": _iso(j.hold_until), "job": str(addr)})
        funds = [{"balance": str(a), "holds_usdc": _usdc(has) if _is_usdc(b.mint) else None, "holds_units": has, "money": _money(b.mint),
                  "faucet": b.faucet, "cap_per_job_units": b.cap_per_job or None, "spender_ids": list(b.spenders),
                  "opened_by_wallet": None if b.faucet else str(b.authority)} for a, b, has in balances]
        waiting = [{"mint": str(mint), "amount_usdc": _usdc(amount)} for mint, amount in dues if amount]
        total = sum(amount for _mint, amount in dues)
        s = lambda n: "" if n == 1 else "s"  # noqa: E731
        said = [f"{login} is paid at {bound.wallet}." if bound else f"{login} has bound no wallet."]
        if waits:
            said.append(f"{len(waits)} payment{s(len(waits))} {'is' if len(waits) == 1 else 'are'} held for {login}"
                        + ("" if bound else " until a wallet is bound") + ".")
        if waiting:
            said.append(f"The first deployment holds {_usdc(total)} USDC for {login}.")
        if not waits and not waiting:
            said.append(f"Nothing is waiting for {login}.")
        how = ([] if bound and not waits else
               [f"Bind a wallet as this GitHub account with {CLAIM}: what is held is sent there, and later payments go there."] if not bound else
               ["What is held is sent to the bound wallet by anyone's relay; `knos claim <address>` again sends it now."])
        if waiting:
            how.append("Send what the first deployment holds to any address with `knos claim --v1 <address>`.")
        return {"login": login, "user_id": user_id, "wallet": str(bound.wallet) if bound else None, "held": waits,
                "record": {"paid": rep.paid, "funders": rep.funders, "total_units": rep.total, "first": _iso(rep.first) if rep.first else None,
                           "last": _iso(rep.last) if rep.last else None, "test_paid": rep.test_paid, "test_total_usdc": _usdc(rep.test_total),
                           "self_paid": rep.self_paid,
                           "note": "paid, funders and total_units count real money from someone else (total_units: every mint's smallest "
                                   "units added up); the faucet's test USDC and bounties the account funded itself are counted apart"},
                "balances": funds,
                "first_deployment": {"waiting": waiting, "waiting_usdc": _usdc(total),
                                     "paid": {"pull_requests": rep1.paid_jobs, "repositories": rep1.repositories, "total_usdc": _usdc(rep1.total_paid)}},
                "how": " ".join(how) or "Nothing to do: payments go to the bound wallet.", "said": " ".join(said),
                "cluster": _cluster(), "note": _note()}


def _is_usdc(mint) -> bool:
    """Whether a mint is this cluster's test USDC: the devnet faucet's, or Circle's devnet USDC. Any other mint is
    named, never called USDC: anyone can fund a job in a token of their own."""
    from .settle.v2 import pay
    return _cluster() != "mainnet" and str(mint) in (str(pay.faucet_mint()), str(pay.USDC_DEVNET))


def _money(mint) -> str:
    return "test USDC" if _is_usdc(mint) else f"token {mint}"


def _note() -> str:
    return "test USDC, no real value" if _cluster() != "mainnet" else "amounts are in each job's own token"


def _cluster() -> str:
    return os.environ.get("KNOS_CLUSTER", "devnet")


def _server_info() -> dict:
    return {"name": "knos", "version": version()}


def _error(rid, code: int, message: str, data: dict | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def serve(stdin, stdout, ledger=None, github=None) -> None:
    """Answer every line of `stdin` on `stdout` until it ends. json.dumps escapes newlines and anything not ASCII, so
    each reply is one line whatever the console's encoding."""
    server = Server(ledger, github)
    for raw in stdin:
        if not raw.strip():
            continue
        reply = server.line(raw)
        if reply is not None:
            stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
            stdout.flush()


def main() -> int:
    """What `knos mcp` runs. Ends when the client closes stdin."""
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    try:
        serve(sys.stdin, sys.stdout)
    except (BrokenPipeError, KeyboardInterrupt):   # the client went away
        pass
    return 0
