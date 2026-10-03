"""A repository on GitHub, a chain, a relay and the public worker for the tests of knos.flow: enough of each to act a
bounty out from the funding comment to the payment, each behind the interface the real one has (knos.judge.github,
knos.chain.Ledger, knos.settle.v2.relay.submit, knos.proof.ghrelay.wait_for). Nothing here opens the network, and no
time is really waited: `sleep` moves the clock."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse

from solders.pubkey import Pubkey

from _hub import BOT, Hub, user
from knos import closing, commands, flow
from knos.settle.v2 import pay

REPO, REPO_ID = "o/r", 555
HUBOT, MONA, EVE = user("hubot", 1), user("mona", 4242), user("eve", 666)       # hubot owns o/r; eve cannot write to it
DEVIN = user("devin-ai-integration[bot]", 158243242, "Bot")
T0 = 1_790_000_000.0                                 # 2026-09-21 14:13:20 UTC: "now" when a test begins
WF_REPO, WF_SHA = "drexthealpha/knos-workflows", "c" * 40      # where the reusable workflows live, and their pinned commit
WALLET, ADDRESS = "AT1aKj1DpgaWerxmS4YjDkNpWPNUtCCKVDvxLhFxg5Jc", "4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo"


def stamp(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def key(seed: str) -> Pubkey:
    """An address of the test's own, the same for the same word."""
    return Pubkey.from_bytes(hashlib.sha256(seed.encode()).digest())


def sha(word: str) -> str:
    """A commit id of the test's own."""
    return hashlib.sha1(word.encode()).hexdigest()


class Clock:
    def __init__(self, now: float = T0):
        self.now, self.slept = now, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


class Signer:
    """GitHub's OIDC endpoint for one run: a token with the claims GitHub would sign (the signature is the chain's
    to check, and the chain here is a fake)."""

    def __init__(self, clock: Clock, actor: int = HUBOT["id"], wf_sha: str = WF_SHA):
        self.clock, self.actor, self.wf_sha, self.asked, self.down = clock, actor, wf_sha, [], False

    def __call__(self, audience: str) -> str:
        if self.down:
            raise OSError("GitHub's token endpoint did not answer")
        self.asked.append(audience)
        file = "fund.yml" if audience.startswith("knos2:fund:") else "prove.yml"
        payload = {"aud": audience, "iat": int(self.clock()), "exp": int(self.clock()) + 300, "repository_id": str(REPO_ID),
                   "repository_owner_id": str(HUBOT["id"]), "actor_id": str(self.actor), "run_attempt": "1",
                   "runner_environment": "github-hosted", "job_workflow_sha": self.wf_sha,
                   "job_workflow_ref": f"{WF_REPO}/.github/workflows/{file}@refs/tags/v0.3.12"}
        enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
        return f"{enc({'alg': 'RS256', 'kid': 'k'})}.{enc(payload)}.c2ln"


# ---- the chain -------------------------------------------------------------------------------------------------------

def job_bytes(repo_id: int, issue: int, amount: int, source: Pubkey, terms_hash: bytes, *, state: int = 1, mode: int = 0,
              faucet: bool = True, mint: Pubkey | None = None, deadline: int = 0, not_before: int = 0, owner_id: int = HUBOT["id"],
              funder_id: int = HUBOT["id"], payee: int = 0, hold_until: int = 0, wf_repo: str = WF_REPO, wf_sha: str = WF_SHA,
              kind: int = 1) -> bytes:
    """A job account as programs-v2/knos_pay lays it out (state.rs): what knos.settle.v2.pay.read_job reads. `kind`:
    1 funded from a Balance by a comment, 0 straight from a wallet."""
    d = bytearray(pay.JOB_LEN)
    d[0], d[1], d[2], d[7] = state, mode, kind, int(faucet)
    for offset, value in ((8, repo_id), (16, issue), (24, amount), (32, deadline), (48, hold_until), (56, payee), (64, funder_id),
                          (72, not_before), (80, owner_id)):
        d[offset:offset + 8] = int(value).to_bytes(8, "little")
    mint = mint or (pay.faucet_mint() if faucet else pay.USDC_DEVNET)
    d[88:120], d[120:152], d[152:184], d[184:216] = bytes(source), bytes(pay.baltok_pda(source)), bytes(key("relayer")), bytes(mint)
    d[216:248], d[248:280], d[280:320] = terms_hash, pay.wf_repo_hash(wf_repo), wf_sha.encode()
    return bytes(d)


def bind_bytes(user_id: int, wallet: str) -> bytes:
    d = bytearray(pay.BIND_LEN)
    d[0] = 1
    d[8:16], d[16:48] = user_id.to_bytes(8, "little"), bytes(Pubkey.from_string(wallet))
    return bytes(d)


def balance_bytes(owner_id: int, authority: Pubkey, mint: Pubkey, cap: int = 0, spenders=(), faucet: bool = False, spent: int = 0) -> bytes:
    """A Balance account as programs-v2/knos_pay lays it out: what knos.settle.v2.pay.read_balance reads."""
    d = bytearray(pay.BALANCE_LEN)
    d[0], d[2] = 1, int(faucet)
    d[8:16], d[16:48], d[48:80], d[80:88] = owner_id.to_bytes(8, "little"), bytes(authority), bytes(mint), cap.to_bytes(8, "little")
    d[128:136] = spent.to_bytes(8, "little")
    for k, spender in enumerate(spenders):
        d[96 + 8 * k:104 + 8 * k] = int(spender).to_bytes(8, "little")
    return bytes(d)


def token_bytes(mint: Pubkey, owner: Pubkey, amount: int) -> bytes:
    """An SPL token account: mint, owner, then the amount at byte 64."""
    return bytes(mint) + bytes(owner) + amount.to_bytes(8, "little") + bytes(165 - 72)


class Chain:
    """Solana as far as the flow reads it, by knos.chain.Ledger's contract: `account`, `program_accounts`, `log_of`
    and `now`. `logs` holds the terms each job's funding transaction logged."""

    def __init__(self, clock: Clock):
        self.clock, self.accounts, self.logs, self.down, self.prefixed, self.ahead, self.newer = clock, {}, {}, False, False, 0, {}

    def _up(self) -> None:
        if self.down:
            raise OSError("the cluster did not answer")

    def account(self, address) -> bytes | None:
        self._up()
        return self.accounts.get(str(address))

    def program_accounts(self, program, size: int, memcmp: dict | None = None) -> list:
        self._up()
        assert program == pay.PAY_ID
        return [(Pubkey.from_string(a), d) for a, d in self.accounts.items()
                if len(d) == size and all(d[o:o + len(raw)] == raw for o, raw in (memcmp or {}).items())]

    def log_of(self, address, marker: str, check=None) -> str | None:
        """The newest log line that starts with `marker` among the transactions that touched `address`; with `check`,
        the newest one it accepts. `newer` holds lines of later transactions that named the address (anyone can send
        one). `prefixed`: a ledger that matches the line as the runtime prints it ("Program log: ..."), and returns
        it so."""
        self._up()
        raw = self.logs.get(str(address))
        lines = [*self.newer.get(str(address), []), "Program log: knos2:funded repo=555",
                 *(["Program log: knos2:terms " + raw.decode()] if raw is not None else [])]
        lines = lines if self.prefixed else [x[len("Program log: "):] for x in lines]
        return next((x for x in lines if x.startswith(marker) and (check is None or check(x))), None)

    def now(self) -> int:
        self._up()
        return int(self.clock()) + self.ahead

    def jobs(self, issue: int | None = None) -> list:
        """[(address, Job)] of every job, or of one issue's."""
        got = [(a, pay.read_job(d)) for a, d in self.accounts.items() if len(d) == pay.JOB_LEN]
        return [(a, j) for a, j in got if issue is None or j.issue == issue]

    def balance(self, name: str, held: int, spenders=(), cap: int = 0, mint: Pubkey = pay.USDC_DEVNET, owner: int = HUBOT["id"],
                spent: int = 0) -> Pubkey:
        """A Balance of real money a wallet opened for the repository's owner, and what its token account holds."""
        address = key(name)
        self.accounts[str(address)] = balance_bytes(owner, key(name + "-wallet"), mint, cap, spenders, spent=spent)
        self.accounts[str(pay.baltok_pda(address))] = token_bytes(mint, pay.auth_pda(), held)
        return address

    def held(self, balance) -> int:
        """What a Balance's token account holds."""
        return int.from_bytes(self.accounts[str(pay.baltok_pda(Pubkey.from_string(str(balance))))][64:72], "little")

    def pause(self, until: float) -> None:
        self.accounts[str(pay.pause_pda())] = int(until).to_bytes(8, "little", signed=True)

    def bind(self, who_: dict, wallet: str = WALLET) -> None:
        self.accounts[str(pay.bind_pda(who_["id"]))] = bind_bytes(who_["id"], wallet)

    def fund(self, issue: int, units: int, bought: dict | bytes, *, source: Pubkey | None = None, at: float | None = None, days: int = 14,
             **more) -> Pubkey:
        """A job as a fund token would have left it: open, with its terms logged."""
        from knos import terms
        raw = bought if isinstance(bought, bytes) else terms.canonical(bought)
        source = source or pay.faucet_balance_pda(HUBOT["id"])
        at = self.clock() if at is None else at
        address = pay.job_pda(REPO_ID, issue, source)
        more.setdefault("mode", 1 if not isinstance(bought, bytes) and bought["mode"] == "tests" else 0)
        self.accounts[str(address)] = job_bytes(REPO_ID, issue, units, source, pay.terms_hash(raw), deadline=int(at) + days * 86_400,
                                                not_before=int(at), **more)
        self.logs[str(address)] = raw
        return address


class Relay:
    """knos.settle.v2.relay, by its contract: `submit`, and nothing else. It does to the fake chain what the programs
    would, as far as these tests need it: a fund token opens a job, a pay token pays or holds the open jobs it
    names."""

    def __init__(self, clock: Clock, takes: float = 30):
        self.clock, self.takes, self.refusals, self.submitted, self.n = clock, takes, [], [], 0

    def _sig(self) -> str:
        self.n += 1
        return f"sig{self.n}"

    def submit(self, ledger: Chain, payer, jwt: str, terms: bytes | None = None, jwks=None, now=None) -> dict:
        self.submitted.append((jwt, terms))
        self.clock.sleep(self.takes)
        if self.refusals:
            return dict(self.refusals.pop(0))
        c = claims(jwt)
        aud = c["aud"].split(":")
        if aud[1] == "fund":
            issue, amount, mode, balance = int(aud[2]), int(aud[3]), int(aud[4]), aud[7]
            job = pay.job_pda(REPO_ID, issue, Pubkey.from_string(balance))
            faucet = balance == str(pay.faucet_balance_pda(int(c["repository_owner_id"])))
            if str(job) in ledger.accounts:
                return {"ok": False, "kind": "fund", "why": pay.ERRORS[82]}
            if hashlib.sha256(terms or b"").hexdigest() != aud[5]:
                return {"ok": False, "kind": "fund", "why": pay.ERRORS[81]}
            mint = pay.faucet_mint()
            if not faucet:
                b = pay.read_balance(ledger.accounts[balance])
                if int(c["actor_id"]) != b.owner_id and int(c["actor_id"]) not in b.spenders:
                    return {"ok": False, "kind": "fund", "why": pay.ERRORS[92]}
                if ledger.held(balance) < amount:
                    return {"ok": False, "kind": "fund", "why": pay.ERRORS[94]}
                mint = b.mint
                ledger.accounts[str(pay.baltok_pda(Pubkey.from_string(balance)))] = token_bytes(b.mint, pay.auth_pda(), ledger.held(balance) - amount)
            deadline = int(self.clock()) + int(aud[6])
            pin = c["job_workflow_ref"].split("/.github/workflows/")[0]
            ledger.accounts[str(job)] = job_bytes(REPO_ID, issue, amount, Pubkey.from_string(balance), bytes.fromhex(aud[5]), mode=mode,
                                                  faucet=faucet, mint=mint, deadline=deadline, not_before=int(c["iat"]),
                                                  funder_id=int(c["actor_id"]), wf_repo=pin, wf_sha=c["job_workflow_sha"])
            ledger.logs[str(job)] = terms
            return {"ok": True, "kind": "fund", "sigs": [self._sig(), self._sig()], "job": str(job), "repo_id": REPO_ID, "issue": issue,
                    "amount": amount, "mode": mode, "faucet": faucet, "balance": balance, "deadline": deadline}
        repo_id, issue, payee = int(aud[2]), int(aud[3]), int(aud[4])
        bind = pay.read_bind(ledger.accounts.get(str(pay.bind_pda(payee))))
        to = str(bind.wallet) if bind else None if aud[8] == "-" else aud[8]
        paid = []
        for address, j in ledger.jobs(issue):
            pinned = j.wf_repo_hash == pay.wf_repo_hash(c["job_workflow_ref"].split("/.github/workflows/")[0]) and j.wf_sha == c["job_workflow_sha"]
            prove = c["job_workflow_ref"].split("/.github/workflows/")[1].startswith("prove.yml@")
            if j.state != "open" or j.terms.hex() != aud[6] or str(j.mode) != aud[7] or not pinned or not prove or int(c["iat"]) < j.not_before:
                continue
            until = None if to else int(self.clock()) + pay.HOLD
            if to:
                del ledger.accounts[address]
            else:
                held = bytearray(ledger.accounts[address])
                held[0] = 3
                held[56:64], held[48:56] = payee.to_bytes(8, "little"), until.to_bytes(8, "little")
                ledger.accounts[address] = bytes(held)
            paid.append({"job": address, "amount": j.amount, "fee": pay.fee_of(j.amount), "mint": str(j.mint), "to": to, "held_until": until})
        if not paid:
            return {"ok": False, "kind": "pay", "why": "no open job on this issue accepted the token"}
        return {"ok": True, "kind": "pay", "sigs": [self._sig(), self._sig()], "repo_id": repo_id, "issue": issue, "payee_id": payee,
                "head": aud[5], "paid": paid}


class Worker:
    """knos.proof.ghrelay, by its contract: `token_id(jwt)`, and `wait_for(token_id, log_repo, timeout, every, get)`,
    which gives the line Knos's public worker logged about a token, or None in time. Here the worker makes its pass
    at that moment: it reads the token comment the flow posted (the marker, the terms line, the one word it
    searches for), relays, and logs."""
    TOKEN = re.compile(r"^knos-(fund|proof|bind): (eyJ[\w-]+\.[\w-]+\.[\w-]+)$", re.M)

    def __init__(self, hub, chain: Chain, relay: Relay, clock: Clock):
        self.hub, self.chain, self.relay, self.clock, self.silent, self.waited = hub, chain, relay, clock, False, []

    def token_id(self, jwt: str) -> str:
        return hashlib.sha256(jwt.strip().encode()).hexdigest()[:16]

    def posted(self) -> list:
        """(issue or pull request, marker, jwt, terms) of every token comment on the repository."""
        out = []
        for n, comments in self.hub.comments.items():
            for c in comments:
                for marker, jwt in self.TOKEN.findall(c["body"]):
                    assert c["user"] == BOT and "knosrelay" in c["body"] and "<sub>" in c["body"], c["body"]
                    terms = re.search(r"^knos-terms: (.+)$", c["body"], re.M)
                    assert bool(terms) == (marker == "fund"), c["body"]
                    out.append((n, marker, jwt, terms.group(1).encode() if terms else None))
        return out

    def wait_for(self, tid: str, log_repo: str, timeout: float, every: float = 3.0, get=None) -> str | None:
        self.waited.append((tid, log_repo, timeout))
        assert get is not None and every == 3.0
        if self.silent:
            self.clock.sleep(timeout)
            return None
        n, marker, jwt, terms = next(x for x in self.posted() if self.token_id(x[2]) == tid)
        self.clock.sleep(9)                                  # the worker's next pass
        r = self.relay.submit(self.chain, None, jwt, terms)
        head = f"knos-relay {marker} {REPO}#{n} {tid}"
        return f"{head} ok sig={','.join(r['sigs'])} note=relayed by the worker t=39" if r["ok"] else f"{head} fail {r['why']}"


# ---- GitHub ----------------------------------------------------------------------------------------------------------

class GitHub(Hub):
    """o/r on api.github.com, with state: issues, pull requests, comments, checks, who can write. The one callable
    knos.judge.github is: `hub(path)` reads, `hub(path, data)` posts, `hub(path, data, "PATCH" | "DELETE")` rewrites.
    A path nothing answers is a 404; `down` names paths GitHub does not answer for at all; `readonly` is a fork's
    token. Every comment Knos posts is checked here: none may itself read as a command."""

    def __init__(self, clock: Clock):
        super().__init__()
        self.clock = clock
        self.repo = {"id": REPO_ID, "full_name": REPO, "default_branch": "main", "owner": dict(HUBOT)}
        self.issues, self.pulls, self.comments, self.events = {}, {}, {}, {}
        self.checks, self.statuses, self.files, self.brought, self.closes, self.messages = {}, {}, {}, {}, {}, {}
        self.can = {"hubot": "admin", "mona": "read"}
        self.users = {u["login"]: u for u in (HUBOT, MONA, EVE, DEVIN)}
        self.contents, self.required, self.head, self.bundles = {}, [], sha("main"), {}
        self.down, self.readonly, self.unassignable, self.ids, self.wrote = (), False, set(), 100, []
        self.edited = {}                                     # pull request -> when its description was last edited
        self.checks[self.head] = [check("test")]

    # -- what a test sets up -------------------------------------------------------------------------------------------
    def issue(self, n: int, body: str = "", author: dict = HUBOT, assignees=(), state: str = "open", at: float | None = None) -> dict:
        self.issues[n] = {"number": n, "body": body, "user": author, "assignees": list(assignees), "state": state,
                          "created_at": stamp(self.clock() if at is None else at)}
        self.comments.setdefault(n, [])
        self.events.setdefault(n, [])
        return self.issues[n]

    def pull(self, n: int, author: dict = MONA, body: str = "Fixes #7", head: str | None = None, merged: float | None = None,
             merge_sha: str | None = None, base: str = "main", fork: str | None = None, assignees=()) -> dict:
        head = head or sha(f"head-{n}")
        owner = fork or "o"
        p = {"number": n, "body": body, "user": author, "assignees": list(assignees), "state": "closed" if merged else "open",
             "head": {"sha": head, "ref": f"fix-{n}", "repo": {"full_name": f"{owner}/r", "owner": {"login": owner}}},
             "base": {"ref": base, "sha": self.head, "repo": {"id": REPO_ID, "full_name": REPO, "default_branch": "main"}},
             "merged": bool(merged), "merged_at": stamp(merged) if merged else None, "merge_commit_sha": merge_sha}
        self.pulls[n] = p
        self.issue(n, body, author, assignees, p["state"])["pull_request"] = {"url": f"https://api.github.com/repos/{REPO}/pulls/{n}"}
        self.files.setdefault(n, [{"filename": "src/a.py", "patch": "@@ -1,1 +1,2 @@\n def a():\n+    return 1"}])
        self.checks.setdefault(head, [check("test")])
        return p

    def edit(self, n: int, body: str, at: float | None = None) -> None:
        """Someone edits pull request `n`'s description (its author can at any time, merged or not)."""
        self.pulls[n]["body"] = self.issues[n]["body"] = body
        self.edited[n] = stamp(self.clock() if at is None else at)

    def say(self, n: int, who_: dict, body: str, at: float | None = None, edited: bool = False) -> dict:
        self.ids += 1
        at = self.clock() if at is None else at
        c = {"id": self.ids, "user": who_, "body": body, "created_at": stamp(at), "updated_at": stamp(at + 60 if edited else at),
             "issue_url": f"https://api.github.com/repos/{REPO}/issues/{n}"}
        self.comments.setdefault(n, []).append(c)
        return c

    def merge(self, n: int, how: str = "squash", at: float | None = None) -> dict:
        """Merge pull request `n` now and return the push it makes on the default branch: a merge commit after the
        pull request's own commits, one squashed commit, or its commits rebased (the last is the merge)."""
        p, at = self.pulls[n], self.clock() if at is None else at
        own = [sha(f"{n}-c1"), sha(f"{n}-c2")]
        pushed = {"merge": [*own, sha(f"{n}-merge")], "squash": [sha(f"{n}-squash")], "rebase": [sha(f"{n}-r1"), sha(f"{n}-r2")]}[how]
        p.update(state="closed", merged=True, merged_at=stamp(at), merge_commit_sha=pushed[-1])
        self.issues[n]["state"] = "closed"
        for s in pushed:
            self.brought[s] = [n]
        return self.push(pushed)

    def push(self, shas: list[str], ref: str = "refs/heads/main") -> dict:
        return {"ref": ref, "before": self.head, "after": shas[-1], "commits": [{"id": s, "message": "work"} for s in shas],
                "repository": {**self.repo, "pushed_at": int(self.clock())}}

    def commented(self, n: int, who_: dict, body: str, at: float | None = None) -> dict:
        """The issue_comment event of a new comment on issue or pull request `n`."""
        return {"action": "created", "issue": self.issues[n], "comment": self.say(n, who_, body, at), "repository": self.repo}

    def ran(self, n: int) -> dict:
        """The workflow_run event of the "knos check" workflow that finished for pull request `n`."""
        p = self.pulls[n]
        return {"action": "completed", "repository": self.repo,
                "workflow_run": {"name": "knos check", "event": "pull_request", "head_sha": p["head"]["sha"], "head_branch": p["head"]["ref"],
                                 "head_repository": p["head"]["repo"], "pull_requests": [], "conclusion": "success"}}

    def knos(self, n: int) -> list[str]:
        """What Knos said on `n`, oldest first (not the tokens it posted for the worker)."""
        return [c["body"] for c in self.comments.get(n, []) if c["user"] == BOT and not c["body"].startswith("knos-")]

    # -- api.github.com --------------------------------------------------------------------------------------------------
    def __call__(self, path: str, data: dict | None = None, method: str | None = None):
        write = data is not None and path != "graphql" or method in ("PATCH", "DELETE")
        (self.wrote if write else self.asked).append(path)
        if data is not None:
            self.posted.append((path, data))
        if any(d in path for d in self.down):
            raise OSError(f"502 {path}")
        if write and self.readonly:
            raise urllib.error.HTTPError(f"https://api.github.com/{path}", 403, "Resource not accessible by integration", None, None)
        bare, _, query = path.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        for pattern, answer in self._ROUTES:
            m = re.fullmatch(pattern, bare)
            if m:
                got = answer(self, q, data, method, *m.groups())
                if got is not None:
                    return [] if isinstance(got, list) and int(q.get("page") or 1) > 1 else got
        raise urllib.error.HTTPError(f"https://api.github.com/{path}", 404, "Not Found", None, None)

    def _commit(self, q, data, method, ref):
        s = self.head if ref == "main" else ref
        return {"sha": s, "commit": {"message": self.messages.get(s, "work")}}

    def _comments(self, q, data, method, n):
        if data is None:
            return list(self.comments.get(int(n), []))
        assert commands.parse(data["body"]) is None, f"Knos posted a comment that reads as a command: {data['body']!r}"
        return self.say(int(n), BOT, data["body"])

    def _edit(self, q, data, method, cid):
        c = next(c for cs in self.comments.values() for c in cs if c["id"] == int(cid))
        assert method == "PATCH" and commands.parse(data["body"]) is None
        c.update(body=data["body"], updated_at=stamp(self.clock() + 1))
        return c

    def _assignees(self, q, data, method, n):
        issue = self.issues[int(n)]
        for login in data["assignees"]:
            if method == "DELETE":
                issue["assignees"] = [a for a in issue["assignees"] if a["login"] != login]
            elif login not in self.unassignable and all(a["login"] != login for a in issue["assignees"]):
                issue["assignees"].append(self.users[login])
                self.events[int(n)].append({"event": "assigned", "assignee": self.users[login], "assigner": BOT, "created_at": stamp(self.clock())})
        return issue

    def _graphql(self, q, data, method):
        n = data["variables"]["number"]
        closes = self.closes.get(n, closing.closing_issues(self.pulls[n]["body"] or "", REPO))
        nodes = [{"number": i, "repository": {"nameWithOwner": REPO}} for i in closes]
        return {"data": {"repository": {"pullRequest": {"mergedAt": self.pulls[n].get("merged_at"), "lastEditedAt": self.edited.get(n),
                                                        "closingIssuesReferences": {"totalCount": len(nodes), "nodes": nodes}}}}}

    def _memory(self, q, data, method):
        if data is None:
            return [i for i in self.issues.values() if {"name": "knos-memory"} in i.get("labels", []) and i["state"] == "open"]
        n = max([*self.issues, 0]) + 1
        self.issue(n, data["body"], BOT).update(labels=[{"name": x} for x in data["labels"]], title=data["title"])
        return {"number": n}

    def _acceptance(self, q, data, method):
        """GET contents/.knos/acceptance: one folder per issue that has acceptance checks."""
        assert q.get("ref")
        return [{"name": str(n), "path": f".knos/acceptance/{n}", "type": "dir", "sha": sha(f"tree-{n}")} for n in sorted(self.bundles)] or None

    def _tree(self, q, data, method, tree):
        files = next((f for n, f in self.bundles.items() if sha(f"tree-{n}") == tree), None)
        if files is None:
            return None
        assert q.get("recursive") == "1"
        rows = [{"path": name, "type": "blob", "mode": "120000" if isinstance(text, tuple) else "100644", "sha": sha("blob-" + name)}
                for name, text in files.items()]
        return {"sha": tree, "truncated": False, "tree": rows + [{"path": "data", "type": "tree", "mode": "040000", "sha": sha("d")}]}

    def _blob(self, q, data, method, blob):
        text = next((t for f in self.bundles.values() for name, t in f.items() if sha("blob-" + name) == blob), None)
        return None if text is None else {"encoding": "base64", "content": base64.encodebytes(text[0] if isinstance(text, tuple) else text).decode()}

    def _content(self, q, data, method, name):
        text = self.contents.get(name)
        return None if text is None else {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}

    def _open_pulls(self, q, data, method):
        return [p for p in self.pulls.values() if p["state"] == "open" and
                f"{p['head']['repo']['owner']['login']}:{p['head']['ref']}" == q.get("head")]

    _R = r"repos/o/r"
    _ROUTES = [
        (_R, lambda s, q, d, m: s.repo),
        (_R + r"/commits/([^/]+)", _commit),
        (_R + r"/rules/branches/main", lambda s, q, d, m: [{"type": "required_status_checks", "parameters": {"required_status_checks": s.required}}]
            if s.required else []),
        (_R + r"/branches/main", lambda s, q, d, m: {"name": "main", "protection": {}}),
        (_R + r"/commits/([^/]+)/check-runs", lambda s, q, d, m, c: {"total_count": len(s.checks.get(c, [])), "check_runs": list(s.checks.get(c, []))}),
        (_R + r"/commits/([^/]+)/status", lambda s, q, d, m, c: {"total_count": len(s.statuses.get(c, [])), "statuses": list(s.statuses.get(c, []))}),
        (_R + r"/actions/runs", lambda s, q, d, m: {"total_count": 0, "workflow_runs": []}),
        (_R + r"/commits/([^/]+)/pulls", lambda s, q, d, m, c: [s.pulls[n] for n in s.brought.get(c, [])]),
        (_R + r"/pulls", _open_pulls),
        (_R + r"/pulls/(\d+)", lambda s, q, d, m, n: s.pulls.get(int(n))),
        (_R + r"/pulls/(\d+)/files", lambda s, q, d, m, n: list(s.files.get(int(n), []))),
        (_R + r"/issues", _memory),
        (_R + r"/issues/(\d+)", lambda s, q, d, m, n: s.issues.get(int(n))),
        (_R + r"/issues/(\d+)/events", lambda s, q, d, m, n: list(s.events.get(int(n), []))),
        (_R + r"/issues/(\d+)/comments", _comments),
        (_R + r"/issues/comments/(\d+)", _edit),
        (_R + r"/issues/(\d+)/assignees", _assignees),
        (_R + r"/collaborators/([^/]+)/permission", lambda s, q, d, m, login: {"permission": s.can[login]} if login in s.can else None),
        (_R + r"/contents/\.knos/acceptance", _acceptance),
        (_R + r"/git/trees/([0-9a-f]+)", _tree),
        (_R + r"/git/blobs/([0-9a-f]+)", _blob),
        (_R + r"/contents/(.+)", _content),
        (r"users/([^/]+)", lambda s, q, d, m, login: s.users.get(login)),
        (r"graphql", _graphql),
    ]


def check(name: str, conclusion: str | None = "success", app: int | None = 15368, status: str = "completed", **more) -> dict:
    """A check run as GitHub's REST API gives it."""
    return {"name": name, "status": status, "conclusion": conclusion, "app": {"id": app} if app else None, **more}


class World:
    """Everything one test needs: `w.run(event)` is the Run a command gets, with this world behind every door."""

    def __init__(self, tmp_path, actor: dict = HUBOT, relay_key: bool = True, now: float = T0):
        self.tmp, self.clock = tmp_path, Clock(now)
        self.hub, self.chain = GitHub(self.clock), Chain(self.clock)
        self.relay = Relay(self.clock)
        self.worker = Worker(self.hub, self.chain, self.relay, self.clock)
        self.signer = Signer(self.clock, actor["id"])
        self.env = {"GITHUB_RUN_ID": "77", **({"KNOS_RELAY_KEY": "[1,2,3]"} if relay_key else {})}
        self.runs = 0

    def run(self, event: dict, **env) -> flow.Run:
        said = event.get("comment") or event.get("issue") or {}
        self.signer.actor = (said.get("user") or HUBOT)["id"]            # GitHub signs who wrote the comment
        self.runs += 1                                                    # every run is a fresh runner: nothing of the last one's is there
        return flow.Run(REPO, event, github=self.hub, ledger=self.chain, relay=self.relay, ghrelay=self.worker, mint=self.signer,
                        key=lambda: "the relay key", env={**self.env, **env}, clock=self.clock, sleep=self.clock.sleep,
                        scratch=self.tmp / f"runner-{self.runs}")
