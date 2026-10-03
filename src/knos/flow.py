"""What a repository's one workflow file runs: four commands, one per job. The YAML only starts them. Everything a
maintainer and a contributor read is decided and written here, and every outcome ends in a comment or in the job's
summary: none is silent.

    knos command   a comment on an issue or a pull request, or a new issue. Acts on its `/knos` line and replies:
                   fund and tip put money in escrow on Solana, take and release change the issue's assignee, the
                   rest is an answer. A comment with no `/knos` line gets no reply.
    knos settle    a push to the default branch (every pull request it merged), a `/knos settle` or `/knos tip`
                   comment, or a workflow_dispatch that names a pull request. For each open job the pull request
                   could take: its terms from the chain, GitHub's record of its last commit, who is paid and where;
                   then GitHub's signed token, the relay, and one comment. Nothing is signed while anything it
                   rests on could not be read. With `--tests --pull N --head SHA` it is the job that signs for a
                   bounty paid by its acceptance checks, after the sandboxed judge passed in a job of its own.
    knos review    the "knos check" workflow finished for a pull request: the same decision without a token, in one
                   comment that later runs edit in place; what the judge learned goes to the knos-memory issue.
    knos check     a pull request, with a read-only token (it may come from a fork): the description's claims, the
                   repository's rules and, for a funded issue, the terms so far. It writes the job's summary and
                   exits 1 only for a false claim or a broken rule.

Environment: GH_TOKEN; ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN where a token is minted;
KNOS_RELAY_KEY (this job relays its own tokens; without it the token is posted as a comment and Knos's public worker
relays it); KNOS_RELAY_LOG_REPO; KNOS_RPC and KNOS_CLUSTER; GITHUB_OUTPUT, GITHUB_STEP_SUMMARY, GITHUB_RUN_ID,
GITHUB_RUN_ATTEMPT and GITHUB_SHA as GitHub sets them. No job checks the repository out: what is read of it (the
CONTRIBUTING file, an issue's acceptance checks) is read through GitHub's API at a named commit.

Everything that reaches outside goes through a `Run`, so the tests hand it a GitHub, a chain and a relay of their
own. Who may spend a Balance and which signed token pays a job are the chain's rules (programs-v2/knos_pay). What is here
decides only what this repository's own workflow asks GitHub to sign, which is as much as a funder's repository may
decide about the funder's own money.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from solders.pubkey import Pubkey

from . import closing, commands, terms, who
from .settle.v2 import pay

MARK = "<!-- knos-review -->"       # the first line of the one comment `knos review` keeps up to date
LOG_REPO = "drexthealpha/Knos"      # where the public worker writes what it relayed (KNOS_RELAY_LOG_REPO)
RELAY_WAIT = 600                    # seconds the public worker is waited for
RETRIES, RETRY_AFTER = 3, 20        # this job's own relay: a refusal that may clear (the faucet's minute) is tried again
CHECKS_WAIT = 120                   # settle: how long a required check that has not finished is waited for
CLAIMS_WAIT = 600                   # check: how long the commit's other checks are waited for, so the review sees them
MAX_COMMITS, MAX_ISSUES = 100, 10   # of one push; of one pull request
MAX_CASES = 12                      # bounties looked at for one pull request, and jobs listed for one issue: anyone can fund a
MAX_BALANCES = 24                   # job or open a Balance from a wallet, and one run and one comment have to stay small
MAX_BUNDLE = 200                    # files of one acceptance bundle, each read through GitHub's API
HOLD_DAYS = pay.HOLD // 86_400
TERMS_LOG = "knos2:terms "          # what knos-pay logs before a job's terms when it is funded
MARKER = {"fund": "fund", "pay": "proof", "proof": "proof", "bind": "bind"}     # the comment a token travels in: knos-<marker>: <jwt>
WORD = "knosrelay"                  # the one word every token comment carries: the public worker finds them by it
BIND = "`knos claim <their Solana address>` in a terminal, or https://drexthealpha.github.io/Knos/#claim in the browser"
OPEN = ("A wallet opens one for that id with `knos balance open`, lists the GitHub ids that may spend it, and adds money "
        "with `knos balance deposit`.")
WRITERS = "people with write access to this repository"
AGAIN = {False: "comment `/knos settle`",      # how a payment is tried again: after a merge; for a bounty paid by acceptance checks
         True: "run the `knos check` workflow again on this pull request"}
_SAID = {"passed": "passed", "failed": "failed", "skipped": "was skipped, which does not count as passing",
         "pending": "has not finished", "absent": "did not run on this commit", "unreadable": "could not be read from GitHub"}


class Run:
    """One command's world: the repository, the event GitHub handed the workflow, and every door to the outside.

        github(path, data=None, method=None)   knos.judge.github: the one function GitHub is reached through (GET; POST
                                               with `data`, GraphQL among them; PATCH and DELETE where Knos rewrites)
        ledger                                 knos.chain.ledger(): account, program_accounts, log_of, now
        relay                                  knos.settle.v2.relay: submit(ledger, payer, jwt, terms)
        ghrelay                                knos.proof.ghrelay: token_id(jwt), and wait_for(...) the public worker's line
        mint(audience)                         GitHub's signed token for this run
        clock(), sleep(seconds), env           this machine's clock, waiting, and the job's environment

    Each is made on first use, so a command that never needs the chain never opens it."""

    def __init__(self, repo: str, event: dict, *, github=None, ledger=None, relay=None, ghrelay=None, mint=None, key=None,
                 env=None, clock=time.time, sleep=time.sleep, scratch: str | Path | None = None) -> None:
        self.repo, self.event = repo, event if isinstance(event, dict) else {}
        self.env = os.environ if env is None else env
        self.clock, self.sleep = clock, sleep
        self._github, self._ledger, self._relay, self._ghrelay, self._mint, self._key = github, ledger, relay, ghrelay, mint, key
        self._scratch = Path(scratch) if scratch else None
        self.began = clock()
        self.outputs: dict[str, str] = {}
        self.failed = False              # something could not be done: the job ends 1
        self.relayed = 0                 # how many signed tokens left this job
        self._files: dict[int, list | None] = {}
        self._left_out: dict[int, int] = {}      # per pull request: how many more bounties wait than one run looks at
        self._edited: dict[int, float | bool | None] = {}    # per pull request: knos.closing.edited_late
        self._chain_time: tuple[int, float] | None = None

    def github(self, path: str, data: dict | None = None, method: str | None = None):
        if self._github is None:
            from . import judge
            self._github = judge.github
        return self._github(path, data, method) if method else self._github(path, data)

    @property
    def ledger(self):
        if self._ledger is None:
            from . import chain
            self._ledger = chain.ledger()
        return self._ledger

    @property
    def relay(self):
        if self._relay is None:
            from .settle.v2 import relay
            self._relay = relay
        return self._relay

    @property
    def ghrelay(self):
        if self._ghrelay is None:
            from .proof import ghrelay
            self._ghrelay = ghrelay
        return self._ghrelay

    def mint(self, audience: str) -> str:
        return self._mint(audience) if self._mint else mint(audience, self.env)

    def payer(self):
        """The key that pays this job's own relay fees (KNOS_RELAY_KEY). It holds nobody's money and decides nothing."""
        if self._key is None:
            from . import chain
            self._key = chain.key
        return self._key()

    def now(self) -> int:
        """The time the programs see: the chain's clock, read once a run and carried forward by this machine's. A
        deadline is the chain's to judge, and a cluster's clock can be minutes away from GitHub's. This machine's
        clock alone when the chain does not say."""
        if self._chain_time is None:
            try:
                self._chain_time = (int(self.ledger.now()), self.clock())
            except Exception:  # noqa: BLE001 - a guide for what is said; the chain itself refuses what is late
                self._chain_time = (int(self.clock()), self.clock())
        at, read = self._chain_time
        return int(at + self.clock() - read)

    def scratch(self) -> Path:
        """A folder for this run's small files: the judge's Sibyl store and the base's rules."""
        if self._scratch is None:
            self._scratch = Path(tempfile.mkdtemp(prefix="knos-", dir=self.env.get("RUNNER_TEMP") or None))
        self._scratch.mkdir(parents=True, exist_ok=True)
        return self._scratch

    def output(self, name: str, value) -> None:
        """One `name=value` line for the workflow's next job or step: in $GITHUB_OUTPUT, and in the job's log."""
        self.outputs[name] = str(value)
        self._append("GITHUB_OUTPUT", f"{name}={value}\n")
        print(f"{name}={value}", flush=True)

    def note(self, text: str) -> None:
        """What happened, on the run's page ($GITHUB_STEP_SUMMARY) and in the job's log."""
        self._append("GITHUB_STEP_SUMMARY", text + "\n\n")
        print(text, flush=True)

    def _append(self, variable: str, text: str) -> None:
        if self.env.get(variable):
            with open(self.env[variable], "a", encoding="utf-8") as f:
                f.write(text)

    def say(self, number, body: str) -> bool:
        """Post one comment on an issue or a pull request. When GitHub will not take it the words go to the job's
        summary instead of nowhere, and the job fails."""
        try:
            self.github(f"repos/{self.repo}/issues/{number}/comments", {"body": body})
        except Exception as why:  # noqa: BLE001 - a read-only token, or GitHub did not answer
            self.failed = True
            self.note(f"GitHub did not take this comment on #{number} ({_short(why)}):\n\n{body}")
            return False
        self.note(f"Commented on #{number}:\n\n{body}")
        return True


# ---- small things ----------------------------------------------------------------------------------------------------

def _short(why) -> str:
    """Someone else's words, on one line and short enough for a comment."""
    return re.sub(r"\s+", " ", str(why)).strip()[:200]


def _plain(text) -> str:
    """A name, safe between backticks."""
    return re.sub(r"[`\r\n]", "'", str(text))[:120]


def _amount(units) -> str:
    """Millionths as money is written, with two decimals or as many as it takes: 20.00, 12.50, 4.875."""
    whole, part = divmod(int(units), 1_000_000)
    return f"{whole}.{f'{part:06d}'.rstrip('0').ljust(2, '0')}"


def _devnet(run: Run) -> bool:
    return run.env.get("KNOS_CLUSTER", "devnet") != "mainnet"


def _money(run: Run, mint) -> str:
    """What a job's money is called. On devnet all of it is test money: the faucet's mint and Circle's devnet USDC
    are both "test USDC", so an amount reads "20.00 test USDC". Any other mint is named."""
    if _devnet(run):
        return commands.MONEY if str(mint) in (str(pay.faucet_mint()), str(pay.USDC_DEVNET)) else f"of the test token `{mint}`"
    return f"of the token `{mint}`"


def _sum(run: Run, jobs) -> str:
    """The money of some jobs, added up by what it is: "20.00 test USDC"."""
    by: dict[str, int] = {}
    for _address, j in jobs:
        by[_money(run, j.mint)] = by.get(_money(run, j.mint), 0) + j.amount
    return " and ".join(f"{_amount(units)} {money}" for money, units in by.items())


def _until(run: Run, j) -> str:
    """How long an open job still has. Past its deadline it stays on chain until someone sends the refund, and pays
    nobody."""
    return (f"until {who.when(j.deadline)}" if run.now() <= j.deadline else
            f"until its time ran out on {who.when(j.deadline)}: it pays nobody now and goes back to where it came from")


def _link(run: Run, text: str, kind: str, at) -> str:
    return f"[{text}](https://explorer.solana.com/{kind}/{at}{'?cluster=devnet' if _devnet(run) else ''})"


def _read(run: Run, path: str):
    """GitHub's answer; None when it said no or did not answer."""
    try:
        return run.github(path)
    except Exception:  # noqa: BLE001
        return None


def _repo(run: Run) -> dict | None:
    """The repository as the chain keys it ({"id", "owner", "branch"}: its id, its owner's id, its default branch):
    from the event, else asked of GitHub. None when neither says."""
    r = run.event.get("repository")
    if not (isinstance(r, dict) and r.get("id") and (r.get("owner") or {}).get("id") and r.get("default_branch")):
        r = _read(run, f"repos/{run.repo}")
    try:
        return {"id": int(r["id"]), "owner": int(r["owner"]["id"]), "branch": str(r["default_branch"])}
    except (KeyError, TypeError, ValueError):
        return None


def _pull(run: Run, number) -> dict | None:
    got = _read(run, f"repos/{run.repo}/pulls/{number}")
    return got if isinstance(got, dict) and got.get("number") else None


def _changes(run: Run, number: int) -> list | None:
    """GitHub's list of the files a pull request changes, read once a run. None when it could not be read whole."""
    if number not in run._files:
        run._files[number] = terms.pages(f"repos/{run.repo}/pulls/{number}/files", run.github, cap=30)
    return run._files[number]


# ---- the chain, read: Ledger.program_accounts, account and log_of are all this needs of it ---------------------------

def _u64(n: int) -> bytes:
    return int(n).to_bytes(8, "little")


def _jobs(run: Run, repo_id: int, issue: int) -> list:
    """[(address, Job)] of every job on one issue that holds money: open, or held for its payee. An issue
    can have several: each Balance and each wallet funds its own. Raises when the chain does not answer."""
    found = run.ledger.program_accounts(pay.PAY_ID, pay.JOB_LEN, {8: _u64(repo_id) + _u64(issue)})
    jobs = ((address, pay.read_job(data)) for address, data in found)
    return sorted(((a, j) for a, j in jobs if j is not None and j.state in ("open", "held") and (j.repo_id, j.issue) == (repo_id, issue)),
                  key=lambda x: str(x[0]))


def _balances(run: Run, owner_id: int) -> list:
    """[(address, Balance)] of every Balance set aside on chain for the repositories of one GitHub owner."""
    found = run.ledger.program_accounts(pay.PAY_ID, pay.BALANCE_LEN, {8: _u64(owner_id)})
    got = ((address, pay.read_balance(data)) for address, data in found)
    return sorted(((a, b) for a, b in got if b is not None and b.owner_id == owner_id), key=lambda x: str(x[0]))


def _holds(run: Run, balance) -> int:
    """What a Balance's token account holds now (either token program keeps the amount at byte 64)."""
    data = run.ledger.account(pay.baltok_pda(balance))
    return int.from_bytes(data[64:72], "little") if data and len(data) >= 72 else 0


def _logged(run: Run, address, digest: bytes) -> bytes | None:
    """The terms a job's funding transaction logged (`knos2:terms <json>`), as the bytes that were funded. Anyone can
    name a job's address in a transaction of their own and log a line of that shape, so the ledger is asked for the
    line whose JSON hashes to what the job stores (`digest`); a ledger whose `log_of` takes no such check gives its
    one line, and the caller holds it to the hash."""
    def of(line) -> bytes:
        text = line.decode("utf-8", "replace") if isinstance(line, (bytes, bytearray)) else str(line)
        return text.split(TERMS_LOG, 1)[-1].strip().encode("utf-8")

    def funded(line) -> bool:
        return hashlib.sha256(of(line)).digest() == digest
    for marker in (TERMS_LOG, "Program log: " + TERMS_LOG):      # a ledger may match the line with or without the runtime's prefix
        try:
            line = run.ledger.log_of(address, marker, funded)
        except TypeError:
            line = run.ledger.log_of(address, marker)
        if line:
            return of(line)
    return None


def _bundle(run: Run, ref: str, issue) -> tuple[str, dict]:
    """(the hash of .knos/acceptance/<issue>/ at one commit, its files as {path inside the folder: content}), read
    through GitHub's API. The hash is exactly what knos.judge.checks_hash computes from a checkout (sorted paths,
    each "path\\0sha256(content)\\n"); ("", {}) when that commit has no such folder. Raises OSError when GitHub does
    not answer, and terms.Refused, with the words for the funder, for a bundle that would not hash the same in a
    checkout."""
    try:
        listing = run.github(f"repos/{run.repo}/contents/.knos/acceptance?ref={urllib.parse.quote(str(ref), safe='')}")
    except OSError as why:
        if getattr(why, "code", None) == 404:
            return "", {}
        raise
    tree = next((e.get("sha") for e in listing if isinstance(e, dict) and e.get("name") == str(issue) and e.get("type") == "dir"),
                None) if isinstance(listing, list) else None
    if not tree:
        return "", {}
    got = run.github(f"repos/{run.repo}/git/trees/{tree}?recursive=1")
    if not isinstance(got, dict) or not isinstance(got.get("tree"), list):
        raise OSError("GitHub's listing of the acceptance checks was not understood")
    where, lines, held = f".knos/acceptance/{issue}/", [], {}
    files = sorted((e for e in got["tree"] if isinstance(e, dict) and e.get("type") != "tree"), key=lambda e: str(e.get("path")))
    if got.get("truncated") or len(files) > MAX_BUNDLE:
        raise terms.Refused(f"`{where}` holds more than {MAX_BUNDLE} files, too many for a bounty's acceptance checks. Keep the "
                            "checks themselves there and their data elsewhere.")
    for e in files:
        if e.get("type") != "blob" or e.get("mode") == "120000":     # a link's text is not the file a checkout would run
            raise terms.Refused(f"`{where}{_plain(e.get('path'))}` is a link or a submodule, and a bounty's acceptance checks "
                                "are plain files. Put the file itself there.")
        blob = run.github(f"repos/{run.repo}/git/blobs/{e.get('sha')}")
        try:
            content = base64.b64decode(blob["content"]) if blob.get("encoding") == "base64" else None
        except (AttributeError, KeyError, TypeError, ValueError):
            content = None
        if content is None:
            raise OSError("GitHub's copy of an acceptance check was not understood")
        lines.append(f"{e.get('path')}\0{hashlib.sha256(content).hexdigest()}\n")
        held[str(e.get("path"))] = content
    return (hashlib.sha256("".join(lines).encode()).hexdigest(), held) if lines else ("", {})


def _not_black_box(run: Run, ref: str, files: dict) -> str:
    """Why a bundle may not pay by its checks alone (knos.judge.black_box: the mechanical test, on the bundle's files
    and the same commit's .knos/proof.toml); "" when it may. Raises OSError when GitHub does not answer for that
    file: not knowing what runs the checks is not "they are black-box"."""
    from . import judge
    try:
        got = run.github(f"repos/{run.repo}/contents/.knos/proof.toml?ref={urllib.parse.quote(str(ref), safe='')}")
    except OSError as why:
        if getattr(why, "code", None) != 404:
            raise
        got = None
    text = None
    if got is not None:
        try:
            text = base64.b64decode(got["content"]).decode("utf-8") if got.get("encoding") == "base64" else None
        except (AttributeError, KeyError, TypeError, ValueError):
            text = None
        if text is None:
            raise OSError("GitHub's copy of .knos/proof.toml was not understood")
    return judge.black_box(files, judge.proof_config(text))


def _claims(jwt: str) -> dict:
    """A token's claims, unverified: GitHub handed the token to this very job, and the chain checks its signature."""
    try:
        body = jwt.split(".")[1]
        got = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:  # noqa: BLE001
        return {}
    return got if isinstance(got, dict) else {}


# ---- GitHub's signature, and the way to the chain --------------------------------------------------------------------

def mint(audience: str, env=None) -> str:
    """Ask GitHub for this run's OIDC token with one audience. The job needs `permissions: id-token: write`; a fork's
    pull_request run never has it. Raises OSError with the reason."""
    import urllib.request
    env = os.environ if env is None else env
    url, bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_URL"), env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise OSError("this job may not ask GitHub for a signed token (it needs `permissions: id-token: write`)")
    req = urllib.request.Request(f"{url}{'&' if '?' in url else '?'}audience={urllib.parse.quote(audience, safe='')}",
                                 headers={"Authorization": f"bearer {bearer}", "Accept": "application/json", "User-Agent": "knos"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - the URL GitHub gave this job
        raw = resp.read()
    try:
        value = json.loads(raw)["value"]
    except (ValueError, KeyError, TypeError):
        value = None
    if not isinstance(value, str) or value.count(".") != 2:
        raise OSError("GitHub's answer held no token")
    return value


def deliver(kind: str, jwt: str, number: int, terms_json: bytes | None = None, *, run: Run, since: float | None = None) -> dict:
    """Carry one signed token to the chain and say what it did. `kind` is fund, proof (a pay token) or bind; `number`
    is the issue or pull request it is about; `terms_json` travels with a fund token.

    With KNOS_RELAY_KEY this job relays it (knos.settle.v2.relay.submit). Without, the token is posted as a comment
    on `number` (`knos-<kind>: <jwt>`, a fund token's terms on a `knos-terms:` line), Knos's public worker carries it
    and its log line is waited for, ten minutes at most; what the token did is then read back from the chain. Either
    way the answer has one shape:

        ok, why     whether the chain took it, and why not ("" when it did)
        sigs        the transactions, as far as the relay named them
        note        what happened, in a few words: the worker's own, or this job's
        seconds     from `since` (what started this: the comment, the merge; default: now) to the answer
        kind        fund, pay or bind, with the relay's detail for it:
                      fund  job, repo_id, issue, amount, mode, faucet, balance, deadline
                      pay   repo_id, issue, payee_id, head, paid: [{job, amount, fee, mint, to, held_until}]
        timeout     True when no relayer carried it in time
        already     True when the chain showed the outcome before this token was sent

    Never raises."""
    began = run.clock() if since is None else since
    want = "pay" if kind == "proof" else kind
    run.relayed += 1
    try:
        r = _relay_here(run, jwt, terms_json) if run.env.get("KNOS_RELAY_KEY") else _relay_there(run, want, jwt, number, terms_json)
    except Exception as why:  # noqa: BLE001 - whatever went wrong is said in the comment, never raised past it
        r = {"ok": False, "kind": want, "why": f"{type(why).__name__}: {_short(why)}"}
    ok = bool(r.get("ok"))
    return {**r, "ok": ok, "kind": r.get("kind") or want, "why": "" if ok else _short(r.get("why") or "the relay gave no reason"),
            "sigs": [str(x) for x in r.get("sigs") or []], "note": str(r.get("note") or (_note(r) if ok else "")),
            "seconds": max(0, round(run.clock() - began))}


def _relay_here(run: Run, jwt: str, terms_json: bytes | None) -> dict:
    """This job's own relay. A refusal the relay marks `retry` (the faucet serves a repository once a minute; the
    cluster dropped a transaction) is tried again while the token is good."""
    ledger, payer = run.ledger, run.payer()
    for left in range(RETRIES, -1, -1):
        r = dict(run.relay.submit(ledger, payer, jwt, terms_json))
        if r.get("ok") or not r.get("retry") or not left:
            break
        run.sleep(RETRY_AFTER)
    return r


def _relay_there(run: Run, kind: str, jwt: str, number: int, terms_json: bytes | None) -> dict:
    """Someone else's relay: the token goes on the issue or pull request as a comment, where Knos's public worker (or
    anyone) finds it by one word, and the worker's public log says what became of it. The token is no secret: its
    audience names one action, and the chain takes it once."""
    c = _claims(jwt)
    aud = str(c.get("aud") or "").split(":")
    before = _open_for(run, aud) if kind == "pay" else []
    lines = [f"knos-{MARKER.get(kind, kind)}: {jwt}", *([f"knos-terms: {bytes(terms_json).decode('ascii')}"] if kind == "fund" and terms_json else [])]
    run.github(f"repos/{run.repo}/issues/{int(number)}/comments",
               {"body": "\n".join(lines) + f"\n\n<sub>{WORD}: GitHub signed this token for the one action it names. Anyone may carry it "
                                              "to Solana; Knos's public relay does.</sub>"})
    tid = str(run.ghrelay.token_id(jwt))
    r = _verdict(run.ghrelay.wait_for(tid, run.env.get("KNOS_RELAY_LOG_REPO") or LOG_REPO, RELAY_WAIT, get=run.github), kind, tid)
    if r["ok"]:
        r.update(_seen(run, kind, c, aud, before))
    return r


def _verdict(line: str | None, kind: str, tid: str) -> dict:
    """The public worker's log line about a token, as a relay result. The line names the token and then says
    `ok sig=<s1>[,<s2>...] note=<words> t=<seconds>` or `fail <why>`. None: nobody carried it in time."""
    if not line:
        return {"ok": False, "kind": kind, "timeout": True, "why": f"no relayer carried it within {RELAY_WAIT // 60} minutes"}
    m = re.search(r"(?:^|\s)(ok|fail)\b[ \t]*(.*)", str(line).split(tid, 1)[-1], re.S)
    if not m:
        return {"ok": False, "kind": kind, "why": f"the relay's log line was not understood: {_short(line)}"}
    if m.group(1) == "fail":
        return {"ok": False, "kind": kind, "why": _short(m.group(2)) or "the relay gave no reason"}
    sig, note = re.search(r"\bsig=(\S+)", m.group(2)), re.search(r"\bnote=(.*?)(?:\s+t=[0-9.]+)?\s*$", m.group(2), re.S)
    return {"ok": True, "kind": kind, "sigs": [x for x in (sig.group(1).split(",") if sig else []) if x and x != "none"],
            "note": _short(note.group(1)) if note else ""}


def _note(r: dict) -> str:
    """What a token this job relayed did, in a few words."""
    if r.get("kind") == "fund":
        return f"job {r.get('job')} holds {_amount(r.get('amount') or 0)} for #{r.get('issue')}"
    if r.get("kind") == "pay":
        rows = r.get("paid") or []
        sent = sum(1 for x in rows if x.get("to"))
        return f"{sent} paid, {len(rows) - sent} held for #{r.get('issue')}"
    return "relayed"


def _open_for(run: Run, aud: list[str]) -> list:
    """The open jobs a pay audience is for, as the chain shows them before the token is sent."""
    return [(a, j) for a, j in _jobs(run, int(aud[2]), int(aud[3]))
            if j.state == "open" and bytes(j.terms).hex() == aud[6] and str(j.mode) == aud[7]]


def _seen(run: Run, kind: str, c: dict, aud: list[str], before: list) -> dict:
    """What a token another job's relayer carried did, read back from the chain: the rest of the relay's result.
    Empty when the chain cannot be read; the caller then says what it asked for."""
    try:
        if kind == "fund":
            repo_id, issue, balance = int(c["repository_id"]), int(aud[2]), aud[7]
            job = pay.job_pda(repo_id, issue, Pubkey.from_string(balance))
            j = pay.read_job(run.ledger.account(job))
            faucet = j.faucet if j else balance == str(pay.faucet_balance_pda(int(c.get("repository_owner_id") or 0)))
            return {"job": str(job), "repo_id": repo_id, "issue": issue, "amount": j.amount if j else int(aud[3]), "mode": int(aud[4]),
                    "faucet": faucet, "balance": balance, "deadline": j.deadline if j else run.now() + int(aud[6])}
        if kind != "pay":
            return {}
        payee = int(aud[4])
        bind = pay.read_bind(run.ledger.account(pay.bind_pda(payee)))
        to = str(bind.wallet) if bind else None if aud[8] == "-" else aud[8]
        paid = []
        for address, j in before:
            now = pay.read_job(run.ledger.account(address))
            row = {"job": str(address), "amount": j.amount, "fee": pay.fee_of(j.amount), "mint": str(j.mint)}
            if now is None and to:                                      # the job is closed: paid, where the chain pays
                paid.append({**row, "to": to, "held_until": None})
            elif now is not None and now.state == "held" and now.payee_id == payee:     # met its terms, and waiting for a wallet
                paid.append({**row, "to": None, "held_until": now.hold_until})
        return {"repo_id": int(aud[2]), "issue": int(aud[3]), "payee_id": payee, "head": aud[5], "paid": paid}
    except Exception:  # noqa: BLE001
        return {}


def _pin(jwt: str) -> tuple[bytes, str] | None:
    """The workflow a token is from, as a job pins it: (sha256 of the repository that holds it, its commit)."""
    c = _claims(jwt)
    ref, sha = str(c.get("job_workflow_ref") or ""), str(c.get("job_workflow_sha") or "")
    if "/.github/workflows/" not in ref or not sha:
        return None
    return pay.wf_repo_hash(ref.split("/.github/workflows/", 1)[0]), sha


# ---- the decision: what a pull request could take, and whether it does -------------------------------------------------

@dataclass
class Case:
    """The open jobs on one issue that share their terms (one signed token pays them all), against one pull request."""
    issue: int
    tip: bool                                       # a tip: a job on the pull request's own number
    jobs: list                                      # [(address, Job)], open
    terms: dict | None = None                       # as funded, read from the chain; None when that could not be done
    raw: bytes = b""
    checks: dict = field(default_factory=dict)      # {required check: state at the head commit}
    scope: list = field(default_factory=list)       # what the pull request changed that the terms do not allow
    why: list = field(default_factory=list)         # what else stands in the way, for certain
    unread: list = field(default_factory=list)      # what could not be read: nothing is decided on it
    fix: list = field(default_factory=list)         # what someone can do about `why`
    paid: dict = field(default_factory=dict)        # knos.who.payee
    where: dict = field(default_factory=dict)       # knos.who.payout_address
    result: dict | None = None                      # the relay's answer, once a token was sent

    def verdict(self) -> str:
        """no (it is not paid as things stand), unread (something could not be read: nothing is decided), wait (a
        required check has not finished) or yes (everything holds). A certain no is said even when something else
        could not be read."""
        states = set(self.checks.values())
        if self.scope or self.why or states & {"failed", "skipped", "absent"}:
            return "no"
        if self.unread or "unreadable" in states:
            return "unread"
        return "wait" if "pending" in states else "yes"


def _terms_of(run: Run, c: Case) -> None:
    """The terms the jobs were funded with: the bytes the funding transaction logged, held to the hash in the job."""
    address, job = c.jobs[0]
    try:
        raw = _logged(run, address, bytes(job.terms))
    except Exception:  # noqa: BLE001
        raw = None
    if not raw:
        c.unread.append("the bounty's terms could not be read from Solana")
    elif hashlib.sha256(bytes(raw)).digest() != bytes(job.terms):
        c.unread.append("the terms Solana gave are not the ones this job was funded with")
    else:
        try:
            c.terms, c.raw = terms.parse(bytes(raw)), bytes(raw)
        except terms.Refused:
            c.why.append("its terms are not in a form Knos reads, so Knos cannot check it")


def _find(run: Run, rp: dict, pull: dict, tips_only: bool = False) -> tuple[list[Case], list[int], list | None, list[int], list]:
    """What a pull request could take: for each issue it closes, and for its own number (tips), the open jobs on
    chain, grouped by their terms. Returns (cases, the issues it closes, GitHub's own list of them or None, the
    numbers the chain did not answer for, the jobs already held)."""
    number = int(pull["number"])
    listed, run._edited[number] = (None, None) if tips_only else closing.facts(run.repo, number, run.github)
    closes = [] if tips_only else closing.closed_by(pull, listed)[:MAX_ISSUES]
    cases, blind, held = [], [], []
    for n, tip in [*((n, False) for n in closes if n != number), (number, True)]:
        try:
            live = _jobs(run, rp["id"], n)
        except Exception:  # noqa: BLE001 - the chain did not answer: whether #n has a job is not known
            blind.append(n)
            continue
        groups: dict = {}
        for address, j in live:
            if j.state == "open":
                groups.setdefault((bytes(j.terms), j.mode), []).append((address, j))
            else:
                held.append((n, address, j))
        cases += [Case(n, tip, jobs) for jobs in groups.values()]
    run._left_out[number] = max(0, len(cases) - MAX_CASES)
    if len(cases) > MAX_CASES:      # what this repository's own comments funded (from a Balance) first, then the largest
        cases = sorted(cases, key=lambda c: (not any(j.from_balance for _a, j in c.jobs), -sum(j.amount for _a, j in c.jobs)))[:MAX_CASES]
    for c in cases:
        _terms_of(run, c)
    return cases, closes, listed, blind, held[:MAX_CASES]


def _left_out(run: Run, number: int) -> list[str]:
    """What to say when a pull request could take more bounties than one run looks at."""
    more = run._left_out.get(number, 0)
    if not more:
        return []
    return [f"{more} more job{'s wait' if more > 1 else ' waits'} on what this pull request closes and {'were' if more > 1 else 'was'} "
            f"not looked at: one run looks at {MAX_CASES}, those funded by this repository's own comments first, then the largest."]


def _record(run: Run, sha: str, cases: list[Case], wait: float = 0, every: bool = False) -> tuple[list | None, list | None]:
    """GitHub's record of one commit, (check runs, commit statuses): read again every 15 seconds for up to `wait`
    seconds while a check a bounty requires (with `every`: any check that is not Knos's own) has not finished or
    the record cannot be read."""
    end = run.clock() + wait
    while True:
        runs, statuses = terms.head_checks(run.repo, sha, run.github)
        states = {s for c in cases if c.terms for s in terms.evidence(c.terms, runs, statuses).values()}
        busy = bool(states & {"pending", "unreadable"}) or (
            every and (runs is None or any(r.get("status") != "completed" for r in runs if not terms.ours(r))))
        if not busy or run.clock() >= end:
            return runs, statuses
        run.sleep(15)


def _judge(run: Run, pull: dict, cases: list[Case], listed: list | None, strict: bool, runs, statuses, payee: bool = True,
           tests: bool = False) -> None:
    """Fill in each case: every required check's state at the head commit, what was changed out of scope, and (with
    `payee`) who is paid and where. `strict`: money moves on this (the merge has happened, or with `tests` the
    sandboxed judge has passed), so nothing is decided on a read that failed, and a pull request merged before a
    job was funded does not take it."""
    if not cases:
        return
    number, head, author = int(pull["number"]), (pull.get("head") or {}).get("sha") or "", pull.get("user") or {}
    files = _changes(run, number)
    changed = None if files is None else sorted({str(n) for f in files if isinstance(f, dict)
                                                 for n in (f.get("filename"), f.get("previous_filename")) if n})
    permission, user, message, facts = who.permission_of(run.repo, run.github), who.user_of(run.github), "", {}
    if payee and author.get("type", "User") != "User" and head:      # what a bot's head commit says is a hint to show
        message = str(((_read(run, f"repos/{run.repo}/commits/{head}") or {}).get("commit") or {}).get("message") or "")
    for c in cases:
        try:
            if c.terms:
                c.checks = terms.evidence(c.terms, runs, statuses)
                if changed is None:
                    c.unread.append("the pull request's changed files could not be read from GitHub")
                else:
                    c.scope = terms.scope(c.terms, changed)
            late = max(j.deadline for _a, j in c.jobs)
            if run.now() > late:      # the chain refuses a signed token after the deadline, whatever it says
                c.why.append(f"its time ran out on {who.when(late)}: the money goes back to where it came from")
            if strict and not c.tip:
                _timing(run, c, pull, tests)
            if payee:
                _payee(run, pull, c, facts, permission, user, message, listed, strict)
        except Exception as why:  # noqa: BLE001 - one job's trouble is its own: the others are still decided
            c.unread.append(f"Knos stopped while reading this one ({type(why).__name__}: {_short(why)})")


def _by_tests(c: Case) -> bool:
    """Whether a case's jobs are paid by their acceptance checks (tests mode) and not by the merge."""
    return c.jobs[0][1].mode == pay.TESTS or bool(c.terms and c.terms["mode"] == "tests")


def _timing(run: Run, c: Case, pull: dict, tests: bool) -> None:
    """A bounty pays work merged after it was funded: without this, anyone could fund an issue and point at a pull
    request merged long ago. (A tip is the opposite: it is funded for a pull request that is already merged.) One
    signed token pays every job with the same terms, so the case is held to its earliest job: a job someone adds after the
    merge cannot hold back the ones that were there before it. And each bounty is paid the way it was funded: on
    the merge, or (`tests`) on its acceptance checks, which must be the very files the funder's money was put on."""
    funded = min(j.not_before for _a, j in c.jobs)
    if pull.get("merged_at") or not tests:      # an open pull request judged by its acceptance checks has no merge to be early
        at = who._ts(pull.get("merged_at"))
        if at is None:
            c.unread.append("GitHub did not say when this pull request was merged")
        elif at < funded:
            c.why.append(f"it was merged on {who.when(at)}, before this bounty was funded ({who.when(funded)}): a bounty pays work "
                         "merged after it was funded")
    if not tests:
        if _by_tests(c):
            c.why.append(f"this bounty is paid by its acceptance checks (.knos/acceptance/{c.issue}/), which Knos runs on a pull "
                         "request before the merge; the merge itself does not pay it")
        return
    if c.terms:
        try:    # the commit the judge job checked out as the base: this run's own (GITHUB_SHA), on the default branch
            ref = str(run.env.get("GITHUB_SHA") or _repo(run)["branch"])
            have, files = _bundle(run, ref, c.issue)
            fooled = _not_black_box(run, ref, files) if c.terms["mode"] == "tests" and have == c.terms["accept"] else ""
        except terms.Refused as why:
            c.why.append(_short(why))
        except Exception:  # noqa: BLE001
            c.unread.append(f"the acceptance checks (.knos/acceptance/{c.issue}/ on the default branch) could not be read from GitHub")
        else:
            if c.terms["mode"] != "tests" or have != c.terms["accept"]:
                c.why.append(f"the acceptance checks in .knos/acceptance/{c.issue}/ on the default branch are not the ones this "
                             "bounty was funded with")
            elif fooled:    # the second deployment has no veto window, so checks a pull request can fool from inside never pay alone
                c.why.append(f"the acceptance checks in .knos/acceptance/{c.issue}/ are not black-box (they {fooled}), and a "
                             "bounty is paid without a merge only by black-box checks")


def _payee(run: Run, pull: dict, c: Case, facts: dict, permission, user, message: str, listed, strict: bool) -> None:
    """Who the case pays (knos.who.payee) and where (the wallet bound on chain first, else their `/knos address`)."""
    key = "" if c.tip else c.issue
    if key not in facts:
        facts[key] = who.read(run.repo, int(pull["number"]), key, run.github)
    f = facts[key]
    # the merge accepted what the description said then: one edited since closes nothing (anyone with a merged pull
    # request could otherwise add `Fixes #N` to it and take the bounty on N), and not knowing is not "it was not"
    c.paid = who.payee(pull, f["issue"], f["events"], f["pull_comments"], f["issue_comments"], permission, c.terms, run.clock(),
                       message, user, strict=strict, closes=listed, tip=c.tip, edited=run._edited.get(int(pull["number"])))
    if not c.paid.get("id"):
        if c.paid.get("kind") == "unread":
            c.unread.append(c.paid["why"])
        else:
            c.why.append(c.paid["why"])
            c.fix += [c.paid["fix"]] if c.paid.get("fix") else []
        return
    try:
        bind = pay.read_bind(run.ledger.account(pay.bind_pda(int(c.paid["id"]))))
        bound = str(bind.wallet) if bind else None
    except Exception:  # noqa: BLE001 - not knowing is not "none bound": the signed token would name the wrong place
        bound = None
        c.unread.append(f"Solana did not say whether a wallet is bound to @{c.paid.get('login')}'s GitHub account")
    c.where = who.payout_address(c.paid, f["pull_comments"], bound)


# ---- the words -------------------------------------------------------------------------------------------------------

def _what(run: Run, c: Case) -> str:
    money = _sum(run, c.jobs) + (f" in {len(c.jobs)} jobs" if len(c.jobs) > 1 else "")
    return f"the tip for this pull request ({money})" if c.tip else f"the bounty on issue #{c.issue} ({money})"


def _rows(c: Case) -> str:
    """One line per required check with its state, then everything else that stands in the way."""
    rows = [f"- `{_plain(name)}`: {_SAID.get(state, state)}" for name, state in c.checks.items()]
    return "".join(f"\n{row}" for row in [*rows, *(f"- {w}" for w in [*c.scope, *c.why, *c.unread])])


def _pays(c: Case, pays: str = "would pay") -> str:
    """Who a case pays and where, as one or two sentences; empty when nobody is (the rows say why)."""
    if not c.paid.get("id"):
        return ""
    login, where = c.paid.get("login"), c.where
    hint = f" ({c.paid['hint'][0].upper()}{c.paid['hint'][1:]}.)" if c.paid.get("hint") else ""
    if where.get("address"):
        at = "the wallet bound to their GitHub account" if where.get("from") == "bound" else "the address in their `/knos address` comment"
        return f"\nIt {pays} @{login} ({c.paid.get('why')}) at `{where['address']}`, {at}.{hint}"
    return f"\nIt {pays} @{login} ({c.paid.get('why')}). {where.get('why', 'No address is known for them')}.{hint}"


def _before(run: Run, c: Case, pull: dict) -> str:
    """One case before the merge: whether the pull request takes it as things stand, each check, who would be paid."""
    name, v, how = _what(run, c), c.verdict(), "its acceptance checks pass" if _by_tests(c) else "it is merged"
    lead = {"yes": f"this pull request takes {name} when {how}, as things stand.",
            "wait": f"this pull request takes {name} when {how}, once the checks below have passed.",
            "unread": f"whether this pull request takes {name} is not known yet: something could not be read.",
            "no": f"this pull request does not take {name} as it stands."}[v]
    states, fixes = set(c.checks.values()), []
    if "failed" in states:
        fixes.append("A check that failed needs a new commit that passes it.")
    if states & {"skipped", "absent"}:
        fixes.append("A check that was skipped or did not run has to run and pass on the pull request's last commit.")
    if c.scope:
        fixes.append("Take the files it may not change out of the pull request.")
    return lead + _rows(c) + _pays(c) + ("\n" + " ".join([*fixes, *c.fix]) if fixes or c.fix else "")


def _after(run: Run, c: Case, pull: dict, after: str, tests: bool = False) -> str:
    """One case where money moves (after the merge; with `tests`, after the acceptance checks passed): paid, held,
    refused by the chain, or not paid with what would change that and how it is tried again."""
    how = AGAIN[tests]
    if c.result is not None:
        return _relayed(run, c, c.result, after, how)
    name, v, sha = _what(run, c), c.verdict(), f"`{str((pull.get('head') or {}).get('sha') or '')[:7]}`"
    states = set(c.checks.values())
    if v == "yes":
        return f"everything {name} asks for holds.{_rows(c)}{_pays(c, 'pays')}\n{how[0].upper()}{how[1:]} to have it paid."
    if v == "unread":
        return (f"nothing was decided about {name}: something could not be read, and nothing is signed on a guess."
                f"{_rows(c)}\nNothing was paid. {how[0].upper()}{how[1:]} to try again.")
    if v == "wait":
        return (f"not paid yet. {name[0].upper()}{name[1:]} needs every check below to pass at this pull request's last commit "
                f"({sha}), and one has not finished.{_rows(c)}\n{how[0].upper()}{how[1:]} when it has.")
    fixes = [f"If a check that did not pass is run again on that commit and passes, {how}."] \
        if states & {"failed", "skipped", "absent"} else []
    fixes += [f"{f} Then {how}." for f in c.fix]
    if not c.tip:
        fixes.append("A maintainer can pay this work anyway with `/knos tip <amount>`" + (" once it is merged." if tests else "."))
    until = min(j.deadline for _a, j in c.jobs)
    stays = f" The money stays in escrow until {who.when(until)}, then goes back to where it came from." if run.now() <= until else ""
    return (f"not paid. This pull request does not take {name} as it stands; its checks are read at its last commit ({sha})."
            f"{_rows(c)}\n{' '.join(fixes)}{stays}")


def _relayed(run: Run, c: Case, r: dict, after: str, how: str) -> str:
    """What a signed token did on the chain: paid (how much, the fee, where), held (until when, and how to receive it), or
    why the chain did not take it."""
    name, payee = _what(run, c), f"@{c.paid.get('login')}"
    took = f"{r.get('seconds', 0)} s after {after}"
    if not r.get("ok"):
        if r.get("timeout"):
            return (f"not confirmed yet. {name[0].upper()}{name[1:]} met its terms for {payee} and GitHub signed the token (it is posted "
                    f"above), but no relayer carried it to Solana within {RELAY_WAIT // 60} minutes. Solana takes the signed token "
                    f"until an hour after it expires: if one carries it, the payment is made, and `/knos status` shows it. Otherwise "
                    f"{how} for a new token.")
        return (f"not paid yet. {name[0].upper()}{name[1:]} met its terms for {payee} and GitHub signed the token, but Solana did not take "
                f"it: {_short(r.get('why') or 'no reason was given').rstrip('. ')}. {how[0].upper()}{how[1:]} to try again.")
    tx = _link(run, "transaction", "tx", r["sigs"][-1]) if r.get("sigs") else "an earlier token had carried it"
    out = []
    for held in (False, True):
        rows = [x for x in r.get("paid") or [] if bool(x.get("to")) != held]
        if not rows:
            continue
        gross, fee = sum(x["amount"] for x in rows), sum(x["fee"] for x in rows)
        money = " and ".join(dict.fromkeys(_money(run, x.get("mint") or c.jobs[0][1].mint) for x in rows))
        for_ = "as a tip for this pull request" if c.tip else f"for issue #{c.issue}"
        kind = ("tip" if c.tip else "bounty") + (f" ({len(rows)} jobs)" if len(rows) > 1 else "")
        if not held:
            to = rows[0]["to"]
            src = {"bound": f", the wallet bound to {payee}'s GitHub account", "comment": f", the address in {payee}'s `/knos address` comment"}
            out.append(f"paid. {payee} received {_amount(gross - fee)} {money} {for_}: the {kind} of {_amount(gross)} less Knos's fee "
                       f"of {_amount(fee)}. It went to `{to}`{src.get(c.where.get('from'), '') if to == c.where.get('address') else ''} "
                       f"({tx}, {took}).")
        else:
            until = who.when(max(x.get("held_until") or 0 for x in rows))
            out.append(f"held for {payee}. {_amount(gross)} {money} {for_} waits for them until {until}, because no "
                       f"wallet is known for them: none is bound to their GitHub account and no `/knos address` comment counted. To "
                       f"receive it, {payee} binds a wallet: {BIND}. It is then paid, less Knos's fee of {_amount(fee)}; "
                       f"after that date it goes back to where it came from ({tx}, {took}).")
    if not out:
        return (f"the signed token for {name} was relayed ({tx}, {took}), but Solana does not show the payment yet. `/knos status` shows "
                "what is in escrow.")
    left = len(c.jobs) - len(r.get("paid") or [])
    if left > 0:
        out.append(f"{left} other job{'s' if left > 1 else ''} on this issue did not take this token (funded through another commit "
                   "of Knos's workflows); `/knos status` shows what is still in escrow.")
    return " ".join(out)


def _join(parts: list[str], lead: str = "Knos: ") -> str:
    """Several outcomes as one comment: the first says who is speaking, each of the others starts its own paragraph."""
    return lead + "\n\n".join(p if lead and i == 0 else p[0].upper() + p[1:] for i, p in enumerate(parts))


# ---- knos command ----------------------------------------------------------------------------------------------------

def command(run: Run) -> int:
    """`knos command`: one comment (or one new issue). Acts on its `/knos` line and always replies; a comment with no
    such line, and a comment that was edited rather than written, gets nothing. A new issue whose description has a
    `/knos fund` line is funded by its author, and its description gives no other command. Returns the job's exit
    status: 1 when something could not be done for a reason that is not the commenter's (GitHub, Solana, the relay)."""
    ev = run.event
    on = ev.get("issue") if isinstance(ev.get("issue"), dict) else {}
    said = ev.get("comment") if "comment" in ev else on
    fresh = ev.get("action") == ("created" if "comment" in ev else "opened")      # an edit does not say it again
    on_pull = "pull_request" in on
    cmd = commands.parse(said.get("body") or "", on_pull) if fresh and on.get("number") and isinstance(said, dict) else None
    if cmd is None or ("comment" not in ev and not isinstance(cmd, commands.Fund) and getattr(cmd, "command", "") != "fund"):
        return 0
    try:
        reply = _act(run, cmd, said, on, on_pull)
    except Exception as why:  # noqa: BLE001 - whatever it was, the commenter is told
        run.failed = True
        reply = (f"Knos: this stopped before it was finished ({type(why).__name__}: {_short(why)}). `/knos status` shows what is in "
                 "escrow now; post the comment again to retry.")
    run.say(on["number"], reply)
    return 1 if run.failed else 0


def _act(run: Run, cmd, said: dict, on: dict, on_pull: bool) -> str:
    """Do what one command asks and return the reply. knos.who.answer decides who may and what is left to do."""
    number, commenter, name = int(on["number"]), said.get("user") or {}, getattr(cmd, "name", "")
    pull, bought, permission, user = None, None, None, None
    facts = dict.fromkeys(("issue", "events", "pull_comments", "issue_comments"))
    if name in ("take", "release"):        # the issue is in the event; its bounty, and who assigned whom, are not
        try:
            bought = _bounty(run, _repo(run), number)
        except Exception as why:  # noqa: BLE001 - not knowing whether it has a bounty is not "it has none"
            run.failed = True
            return f"Knos: whether issue #{number} has a bounty could not be read just now ({_short(why)}), so nothing was changed. Post the comment again."
        facts.update(issue=on, events=terms.pages(f"repos/{run.repo}/issues/{number}/events", run.github))
    elif on_pull and name not in ("", "status", "help"):
        pull = _pull(run, number)
        if pull is None:
            run.failed = True
            return "Knos: GitHub did not answer for this pull request, so nothing was done. Post the comment again."
        permission, user = who.permission_of(run.repo, run.github), who.user_of(run.github)
        if name not in ("tip", "settle"):
            facts, bought = _context(run, pull)
    o = who.answer(cmd, commenter, pull, facts["issue"], facts["events"], facts["pull_comments"], facts["issue_comments"],
                   permission, bought, run.clock(), user)
    if o.then in ("fund", "tip"):
        return _fund(run, cmd, said, on, pull)
    if o.then == "status":
        return _status(run, on, on_pull)
    if o.then == "settle":
        run.output("settle", number)
    if o.assign or o.unassign:
        return _assign(run, number, o)
    if isinstance(cmd, commands.Address) and o.reply == commands.reply("understood", cmd, login=commenter.get("login")):
        return o.reply + _bound_note(run, commenter, cmd.address)
    return o.reply


def _bounty(run: Run, rp: dict, n: int) -> dict | None:
    """The terms of an issue's bounty (its largest open job); None when it has none. Raises when the repository, the
    chain or the terms on it cannot be read: "no bounty" is never said on a guess."""
    if rp is None:
        raise OSError("GitHub did not answer for this repository")
    live = [(a, j) for a, j in _jobs(run, rp["id"], n) if j.state == "open"]
    for address, j in sorted(live, key=lambda x: -x[1].amount):
        c = Case(n, False, [(address, j)])
        _terms_of(run, c)
        if c.terms:
            return c.terms
        if c.unread:
            raise OSError(c.unread[0])
    return None


def _context(run: Run, pull: dict) -> tuple[dict, dict | None]:
    """For a comment on a pull request: the issue whose bounty it is for (the first funded one it closes, else the
    one it closes), what GitHub says about both, and that bounty's terms. What cannot be had is left out, and
    knos.who then holds nothing against anyone on it."""
    number, rp = int(pull["number"]), _repo(run)
    closes = closing.closed_by(pull, closing.read(run.repo, number, run.github))[:MAX_ISSUES]
    issue, bought = (closes[0] if len(closes) == 1 else ""), None
    for n in closes:
        try:
            bought = _bounty(run, rp, n)
        except Exception:  # noqa: BLE001
            bought = None
        if bought:
            issue = n
            break
    return who.read(run.repo, number, issue, run.github), bought


def _bound_note(run: Run, commenter: dict, address: str) -> str:
    """After a `/knos address`: when a wallet is already bound to that account, where the money will really go."""
    try:
        bind = pay.read_bind(run.ledger.account(pay.bind_pda(int(commenter["id"]))))
    except Exception:  # noqa: BLE001 - the reply already says a bound wallet comes first
        return ""
    if bind is None or str(bind.wallet) == address:
        return ""
    return f" A wallet is bound to @{commenter.get('login')}'s GitHub account already (`{bind.wallet}`), so the payment goes there."


def _assign(run: Run, number: int, o) -> str:
    """Do what a take or a release asks of the issue's assignees, and only then say it was done: GitHub quietly
    ignores a login it will not assign."""
    path = f"repos/{run.repo}/issues/{number}/assignees"
    try:
        if o.unassign:
            run.github(path, {"assignees": list(o.unassign)}, "DELETE")
        if o.assign:
            now = run.github(path, {"assignees": list(o.assign)})
            have = {str(a.get("login") or "").lower() for a in (now or {}).get("assignees") or [] if isinstance(a, dict)}
            missing = [x for x in o.assign if str(x).lower() not in have]
            if missing:
                return (f"Knos: GitHub did not assign @{missing[0]} to issue #{number}, so nothing was reserved. A maintainer can "
                        "assign them with GitHub's own assignee control.")
    except Exception as why:  # noqa: BLE001
        run.failed = True
        return (f"Knos: GitHub did not change who issue #{number} is assigned to ({_short(why)}), so nothing was "
                f"{'reserved' if o.assign else 'released'}. Post the comment again.")
    return o.reply


def _can_write(run: Run, rp: dict, account: dict) -> bool | None:
    """Whether this account may fund here: the repository's owner, and whoever GitHub answers write, maintain or admin
    for (GET /repos/{repo}/collaborators/{login}/permission), never the label on a comment. None: GitHub did not
    answer. The chain then holds the commenter to the Balance: its owner's, or a spender its wallet listed."""
    if account.get("id") and account["id"] == rp["owner"]:
        return True
    return who.is_maintainer(account.get("login"), who.permission_of(run.repo, run.github))


def _fund(run: Run, cmd, said: dict, on: dict, pull: dict | None) -> str:
    """`/knos fund` and `/knos tip`: only for someone who can write to the repository. Fix the terms, choose the
    Balance, have GitHub sign exactly that, carry it to the chain, and say what is now in escrow. Every way it can
    stop says what was not done and what to type."""
    number, commenter, tip = int(on["number"]), said.get("user") or {}, isinstance(cmd, commands.Tip)
    again = "post the comment again" if "comment" in run.event else "post the `/knos fund` line as a comment here"
    retry = again.capitalize()                     # the same words, to start a sentence with
    since = who._ts(said.get("created_at")) or run.began
    after = "the comment" if "comment" in run.event else "the issue was opened"
    rp = _repo(run)
    if rp is None:
        run.failed = True
        return f"Knos: GitHub did not answer for this repository, so nothing was funded. {retry}."
    may = _can_write(run, rp, commenter)
    if may is None:
        run.failed = True
        return (f"Knos: GitHub did not answer whether @{commenter.get('login')} can write to this repository, so nothing was "
                f"funded. {retry}.")
    if not may:
        return commands.reply("not_allowed", cmd, who=WRITERS)
    if str(run.env.get("GITHUB_RUN_ATTEMPT") or "1") != "1":      # the chain refuses it: a re-run keeps the first actor's name
        return f"Knos: nothing was funded. This is a re-run, and money moves only on the first run of a comment. {retry}."
    if not tip and on.get("state") == "closed":
        return f"Knos: issue #{number} is closed, so nothing was funded. Reopen it and {again}."
    try:
        paused = pay.read_pause(run.ledger.account(pay.pause_pda()))
        if paused > run.now():
            return (f"Knos: nothing was funded. New funding is paused on Solana until {who.when(paused)} (a pause lasts "
                    f"{pay.PAUSE_MAX // 86_400} days at most); payments, refunds and withdrawals go on. {retry} after that.")
        balance, mint_, faucet, no = _balance(run, rp, commenter, cmd, number, again)
    except Exception as why:  # noqa: BLE001 - the chain did not answer
        run.failed = True
        return f"Knos: Solana could not be read just now ({_short(why)}), so nothing was funded. {retry}."
    if balance is None:
        return no
    payee = ""
    if tip:     # money that could reach nobody is not put in escrow
        comments = terms.pages(f"repos/{run.repo}/issues/{number}/comments", run.github)
        paid = who.payee(pull or {}, None, None, comments, None, who.permission_of(run.repo, run.github), None, run.clock(), "",
                         who.user_of(run.github), strict=True, tip=True)
        if not paid.get("id"):
            fix = f"{retry}." if paid.get("kind") == "unread" else f"{paid.get('fix', '')} Then {again}."
            return f"Knos: no tip was sent: {paid['why']}. {fix.strip()}"
        payee = str(paid.get("login") or "")
    try:
        built = _built(run, cmd, rp, number)
        data = terms.canonical(built.terms)
    except terms.Refused as why:
        return f"Knos: {why}"
    except OSError as why:      # the acceptance checks: not knowing whether there are any is not "there are none"
        run.failed = True
        return (f"Knos: GitHub did not answer for this issue's acceptance checks (.knos/acceptance/{number}/ on the default "
                f"branch: {_short(why)}), so the bounty's terms could not be fixed and nothing was funded. {retry}.")
    work = (terms.TIP_DAYS if tip else cmd.days) * 86_400
    mode = pay.TESTS if built.terms["mode"] == "tests" else pay.MERGE
    try:
        jwt = run.mint(pay.fund_audience(number, cmd.units, mode, pay.terms_hash(data), balance, work))
    except Exception as why:  # noqa: BLE001
        run.failed = True
        return f"Knos: GitHub did not sign the request ({_short(why)}), so nothing was funded. {retry}."
    r = deliver("fund", jwt, number, data, run=run, since=since)
    what = "the tip" if tip else "the bounty"
    if not r["ok"]:
        run.failed = True
        if r.get("timeout"):
            return (f"Knos: not confirmed yet. GitHub signed the request (it is posted above) and no relayer carried it to Solana "
                    f"within {RELAY_WAIT // 60} minutes. Solana takes the signed token until an hour after it expires: if one carries it, {what} is funded, and "
                    f"`/knos status` shows it. Otherwise {again}.")
        return (f"Knos: nothing was funded. GitHub signed the request and Solana did not take it: {r['why'].rstrip('. ')}. To try "
                f"again, {again}.")
    job = _link(run, "job on Solana", "address", r.get("job") or pay.job_pda(rp["id"], number, balance))
    money = f"{_amount(r.get('amount') or cmd.units)} {_money(run, mint_)}"
    took = f"{r['seconds']} s after {after}"
    if tip:
        run.output("settle", number)
        return (f"Knos: a tip of {money} for this pull request is in escrow ({job}), {took}. It is paid to @{payee} next; the "
                "result follows here.")
    told = terms.describe(built.terms, built.source)
    deadline = who.when(r.get("deadline") or run.now() + work)
    source = "the devnet faucet" if r.get("faucet", faucet) else f"the balance `{balance}`"
    return "\n\n".join((
        f"Knos: {money} from {source} is in escrow for issue #{number} ({job}), {took}.",
        " ".join([*told[:-1], *built.notes, f"If it is not paid by {deadline}, the money goes back to where it came from."]),
        f"To earn it: open a pull request whose description says `Fixes #{number}`. {told[-1]} For the money to reach you when it "
        "is paid, comment `/knos address <your Solana address>` on your pull request; without an address it waits for you "
        f"until you bind a wallet ({HOLD_DAYS} days at most)."))


def _built(run: Run, cmd, rp: dict, number: int) -> terms.Built:
    """The terms a command buys. A tip asks for nothing. A bounty: the funder's words, the default branch's rules and
    what ran on its head; and in tests mode (the default branch holds .knos/acceptance/<issue>/, and that bundle is
    black-box by knos.judge.black_box) the hash of those checks, as the judge will compute it from its checkout. A
    bundle that is not black-box buys merge mode, and the note for the funder says why and what would change it.
    Raises terms.Refused with the words for the funder, and OSError when GitHub does not say whether the issue has
    acceptance checks or what runs them."""
    if isinstance(cmd, commands.Tip):
        return terms.Built(terms.tip(), "tip", [])
    head = (_read(run, f"repos/{run.repo}/commits/{urllib.parse.quote(rp['branch'], safe='')}") or {}).get("sha")
    accept, files = _bundle(run, str(head or rp["branch"]), number)
    # paid by its checks alone (mode 1) only when they are black-box: the second deployment has no veto window, and checks that
    # share a process with the pull request's code can be made to pass from inside (docs/TAMPER.md)
    fooled = _not_black_box(run, str(head or rp["branch"]), files) if accept else ""
    required = runs = statuses = None
    if cmd.checks != () and head:        # `checks: none` asks nothing of the repository
        required = terms.required_checks(run.repo, rp["branch"], run.github)
        runs, statuses = terms.head_checks(run.repo, str(head), run.github, events=True)
    built = terms.build(cmd, required, runs, statuses, "" if fooled else accept)
    if fooled:
        built.notes.append(f"Its acceptance checks (.knos/acceptance/{number}/) {fooled}, so only your merge pays this bounty (a "
                           "pull request can fool such checks). To have the checks alone pay, add a `blackbox.sh` there that runs "
                           "the pull request's code through `$KNOS_RUN` and compares its output.")
    return built


def _ours(run: Run, ids: list):
    """Whether a comment may spend a Balance, as a function of the Balance. Any wallet can open a Balance for any
    GitHub owner, in a token of its own making, holding any number of it, and list anyone as a spender; the owner
    spends every Balance opened for their id. If the largest simply won, a stranger could make every bounty a
    funder's comment funds be in the stranger's token. So a comment spends a Balance only when its money is the one
    Knos names on this cluster (devnet: test USDC), or when the wallet that opened it is the one bound on chain to
    the GitHub account of one of `ids` (the commenter, the repository's owner): GitHub signed that claim for the
    account's owner, so that Balance is theirs. The Binds are read once, and only when a Balance needs them. Raises
    when the chain does not answer."""
    named = {str(pay.faucet_mint()), str(pay.USDC_DEVNET)} if _devnet(run) else set()
    bound: list | None = None

    def ours(b) -> bool:
        nonlocal bound
        if str(b.mint) in named:
            return True
        if bound is None:
            binds = (pay.read_bind(run.ledger.account(pay.bind_pda(int(i)))) for i in dict.fromkeys(ids) if i)
            bound = [str(x.wallet) for x in binds if x is not None]
        return str(b.authority) in bound
    return ours


def _balance(run: Run, rp: dict, commenter: dict, cmd, number: int, again: str):
    """The Balance a fund or a tip spends: (its address, its mint, whether it is the faucet's, ""), or (None, None,
    False, the reply) when there is none. It is chosen here and named in the token, and the chain spends no other:
    a Balance of the repository owner's that lists this commenter (the owner, or a spender its wallet listed), that
    a comment may spend (`_ours`: test USDC, or one the commenter's or the owner's bound wallet opened) and that
    holds the amount, the largest first; else, on devnet, the owner's faucet Balance (test money, for whoever this
    workflow lets fund: `_fund` asked GitHub that they can write). One Balance funds an issue once."""
    uid, units = commenter.get("id"), cmd.units
    live = _jobs(run, rp["id"], number)
    real = [(a, b) for a, b in _balances(run, rp["owner"]) if not b.faucet]
    ours = _ours(run, [uid, rp["owner"]])
    listed = sorted(((a, b) for a, b in real if uid and (uid == b.owner_id or uid in b.spenders)),      # any wallet can open one:
                    key=lambda x: (not ours(x[1]), -x[1].spent, str(x[0])))[:MAX_BALANCES]              # those in use first
    mine = sorted(((_holds(run, a), str(a), a, b) for a, b in listed), key=lambda x: (not ours(x[3]), -x[0], x[1]))
    fits = [(a, b.mint, False) for held, _s, a, b in mine
            if ours(b) and held >= units and (not b.cap_per_job or units <= b.cap_per_job)]
    if not fits and _devnet(run) and units <= pay.FAUCET_CAP:
        fits = [(pay.faucet_balance_pda(rp["owner"]), pay.faucet_mint(), True)]
    taken = {str(j.source): (a, j) for a, j in live}
    free = [x for x in fits if str(x[0]) not in taken]
    if free:
        return (*free[0], "")
    if fits:
        address, j = taken[str(fits[0][0])]
        state = f"open {_until(run, j)}" if j.state == "open" else f"held until {who.when(j.hold_until)}"
        where = "This pull request" if isinstance(cmd, commands.Tip) else f"Issue #{number}"
        return None, None, False, (
            f"Knos: nothing was added. {where} already has {_sum(run, [(address, j)])} in escrow from this balance, {state} "
            f"({_link(run, 'job on Solana', 'address', address)}). A balance holds one "
            + ("tip on a pull request at a time: comment `/knos settle` to have this one paid." if isinstance(cmd, commands.Tip) else
               "bounty on an issue at a time; `/knos status` shows it."))
    rows = []
    for held, _s, a, b in mine[:3]:
        if not ours(b):
            rows.append(f"- Balance `{a}` holds {_amount(held)} {_money(run, b.mint)}, which a comment does not spend: Knos does not "
                        "name that token, and the wallet that opened the balance is not the one bound to your GitHub account or to "
                        "this repository's owner's.")
        elif b.cap_per_job and units > b.cap_per_job:
            rows.append(f"- Balance `{a}` holds {_amount(held)} {_money(run, b.mint)}, and its wallet lets one job take at most "
                        f"{_amount(b.cap_per_job)}.")
        else:
            rows.append(f"- Balance `{a}` holds {_amount(held)} {_money(run, b.mint)}: send {_amount(units - held)} more to its "
                        f"token account `{pay.baltok_pda(a)}` (a plain token transfer; anyone can).")
    if _devnet(run):
        rows.append(f"- The devnet faucet gives at most {_amount(pay.FAUCET_CAP)} test USDC for one job: "
                    f"`/knos {cmd.name} {commands.amount(pay.FAUCET_CAP)}` works now.")
    if real and not mine:
        rows.append(f"- {len(real)} Balance{'s are' if len(real) > 1 else ' is'} set aside on Solana for this repository's owner, "
                    f"and {'none lists' if len(real) > 1 else 'it does not list'} your GitHub id ({uid}) as a spender. The wallet "
                    "that opened it adds you with `knos balance set`.")
    elif not real:
        rows.append(f"- No Balance on Solana is set aside for this repository's owner (GitHub id {rp['owner']}). {OPEN}")
    return None, None, False, (f"Knos: nothing was funded: no balance you can spend holds {_amount(units)}.\n" + "\n".join(rows)
                               + f"\nThen {again}.")


def _status(run: Run, on: dict, on_pull: bool) -> str:
    """`/knos status`: on an issue, what is in escrow for it, its terms and who holds it; on a pull request, what the
    automatic check would say now: which bounty it takes, each required check, who is paid and where."""
    number, rp = int(on["number"]), _repo(run)
    if rp is None:
        run.failed = True
        return "Knos: GitHub did not answer for this repository. Comment `/knos status` again."
    if not on_pull:
        return _escrow(run, rp, on)
    pull = _pull(run, number)
    if pull is None:
        run.failed = True
        return "Knos: GitHub did not answer for this pull request. Comment `/knos status` again."
    merged, found = bool(pull.get("merged_at")), _find(run, rp, pull)
    cases, closes, listed, blind, held = found
    run.failed = run.failed or bool(blind)      # the answer is incomplete, and not for anything the commenter did
    if merged:
        runs, statuses = _record(run, (pull.get("head") or {}).get("sha") or "", cases)
        _judge(run, pull, cases, listed, True, runs, statuses)
        parts = [_after(run, c, pull, "") for c in cases] + _left_out(run, number) + _unseen(blind, number, "comment `/knos status`")
    else:
        parts, _gate_said = _advice(run, rp, pull, found, 0, True, False)
    return _join(parts or [_nothing(run, pull, closes, listed, held, False, merged)])


def _escrow(run: Run, rp: dict, on: dict) -> str:
    """What is in escrow for one issue: each job, its terms in words, its deadline, and who holds the issue."""
    n = int(on["number"])
    try:
        live = sorted(_jobs(run, rp["id"], n), key=lambda x: -x[1].amount)
    except Exception as why:  # noqa: BLE001
        run.failed = True
        return (f"Knos: Solana could not be read just now ({_short(why)}), so what is in escrow for issue #{n} is not known. "
                "Comment `/knos status` again.")
    if not live:
        return f"Knos: nothing is in escrow for issue #{n}. A maintainer puts a bounty on it with `/knos fund <amount>`."
    parts, bought = [], None
    more, live = len(live) - MAX_CASES, sorted(live, key=lambda x: (not x[1].from_balance, -x[1].amount))[:MAX_CASES]
    for address, j in live:
        job = _link(run, "job on Solana", "address", address)
        if j.state != "open":
            parts.append(f"{_sum(run, [(address, j)])} for issue #{n} is held for GitHub user id {j.payee_id} until "
                         f"{who.when(j.hold_until)}: it is paid when they bind a wallet ({job}).")
            continue
        c = Case(n, False, [(address, j)])
        _terms_of(run, c)
        bought = bought or c.terms
        told = " ".join(terms.describe(c.terms)) if c.terms else f"Its terms are not known here: {(c.unread or c.why)[0]}."
        parts.append(f"{_sum(run, [(address, j)])} is in escrow for issue #{n} {_until(run, j)} ({job}). {told}")
    holds = [h for h in who.reservation(on, terms.pages(f"repos/{run.repo}/issues/{n}/events", run.github), bought, run.clock())
             if not h.lapsed]
    if holds:
        parts[-1] += " It is assigned to " + ", ".join(f"@{h.login}" + (f" until {who.when(h.until)}" if h.until else "")
                                                       for h in holds[:4]) + ": only their pull request is paid for it."
    elif bought:
        parts[-1] += " Nobody holds it."
    if more > 0:
        parts.append(f"{more} more job{'s' if more > 1 else ''} on this issue {'are' if more > 1 else 'is'} not listed: the smallest, "
                     "each funded straight from a wallet.")
    return _join(parts)


def _unseen(blind: list[int], number: int, how: str = "comment `/knos settle`") -> list[str]:
    """What to say about the numbers the chain did not answer for, and how it is tried again."""
    if not blind:
        return []
    named = ", ".join(f"issue #{n}" for n in blind if n != number) or "this pull request's tips"
    return [f"Solana could not be read for {named}, so whether anything is in escrow there is not known. {how[0].upper()}{how[1:]} "
            "to try again."]


def _nothing(run: Run, pull: dict, closes: list[int], listed: list | None, held: list, tips_only: bool, merged: bool) -> str:
    """The answer when a pull request has no open job to take: what it closes, and what is already held."""
    if tips_only:
        return ("nothing to pay: no tip waits for this pull request on Solana. A tip sent a moment ago may not have arrived; "
                "comment `/knos settle` in a minute.")
    issues = ", ".join(f"#{n}" for n in closes)
    said = (f"no bounty is open on the issue{'s' if len(closes) > 1 else ''} this pull request closes ({issues})" if closes else
            "this pull request closes no issue (its description would say `Fixes #N`)" if listed is not None else
            "GitHub did not say which issues this pull request closes, and its description closes none")
    out = [f"{'nothing to pay' if merged else 'this pull request takes no bounty as it stands'}: {said}, and no tip waits for it."]
    for n, _address, j in held:
        who_ = f"@{pull['user']['login']}" if (pull.get("user") or {}).get("id") == j.payee_id else f"GitHub user id {j.payee_id}"
        out.append(f"The bounty on issue #{n} ({_sum(run, [(_address, j)])}) is already held for {who_} until "
                   f"{who.when(j.hold_until)}: it is paid when they bind a wallet ({BIND}).")
    return " ".join(out)


# ---- knos settle -----------------------------------------------------------------------------------------------------

def settle(run: Run, tests: bool = False, pull: int | None = None, head: str = "", issue: int | None = None) -> int:
    """`knos settle`: pay what merged pull requests earned. A push to the default branch settles every pull request it
    merged, and says nothing about one with nothing in escrow. A `/knos settle` comment and a workflow_dispatch with
    a pull request's number settle that one and always answer; a `/knos tip` comment pays the tip `knos command`
    has just funded. With `tests` (`--tests --pull N --head SHA`) it is the job that signs after the sandboxed
    judge passed: see `_attest`. Returns the job's exit status: 1 when something could not be read, signed or
    relayed (the comment says how it is tried again)."""
    ev, rp = run.event, _repo(run)
    if rp is None:
        run.note("Knos settle: GitHub did not answer for this repository, so nothing was looked at. `/knos settle` on a merged "
                 "pull request tries its payment again.")
        return 1
    if tests:
        return _attest(run, rp, pull, head, issue)
    asked, tips_only, after, since = True, False, "this run began", run.began
    if "comment" in ev:
        on, said = ev.get("issue") or {}, ev.get("comment") or {}
        cmd = commands.parse(said.get("body") or "", True) if "pull_request" in on and ev.get("action") == "created" else None
        if not isinstance(cmd, (commands.Settle, commands.Tip)):
            run.note("Knos settle: this comment asks for no payment.")
            return 0
        numbers, tips_only, after = [on.get("number")], isinstance(cmd, commands.Tip), "the comment"
        asked = not tips_only      # `knos command` has answered a tip already: with nothing to pay there is nothing to add
        since = who._ts(said.get("created_at")) or run.began
    elif "after" in ev and "ref" in ev:
        pulls, whole = _merged_by(run, rp)
        if not whole:
            run.failed = True
            run.note("Knos settle: GitHub did not answer for every commit of this push, so a pull request it merged may have been "
                     "missed. `/knos settle` on a merged pull request tries its payment again.")
        if not pulls:
            run.note("Knos settle: this push merged no pull request into the default branch.")
        for merged in pulls:
            _settle_safely(run, rp, merged, who._ts(merged.get("merged_at")) or run.began, "the merge", False, False)
        return 1 if run.failed else 0
    else:
        numbers = [_named(ev)]
    for n in numbers:
        merged = _pull(run, n) if n else None
        if merged is None:
            run.failed = True
            run.note(f"Knos settle: GitHub did not answer for pull request #{n}." if n else
                     "Knos settle: this run names no pull request (the workflow_dispatch input `pull`).")
        elif not merged.get("merged_at"):
            run.note(f"Knos settle: pull request #{n} is not merged. Its payment is tried once it is.")
        else:
            _settle_safely(run, rp, merged, since, after, asked, tips_only)
    return 1 if run.failed else 0


def _attest(run: Run, rp: dict, number: int | None, head: str, issue: int | None) -> int:
    """`knos settle --tests --pull N --head SHA [--issue I]`: the job that signs for a bounty paid by its acceptance
    checks. The workflow starts it only after the sandboxed judge passed for that pull request at that commit, in
    a job that holds no token; this job runs no pull request code. It trusts nothing the judge job said beyond its
    success: the pull request, the jobs, their terms, GitHub's record of the commit, the payee and the address are
    all read again here. The head must still be the pull request's, and the acceptance checks on the default
    branch must be the ones the bounty was funded with. Then it signs with mode 1, relays, and comments."""
    found = _pull(run, number) if number else None
    if found is None:
        run.note(f"Knos settle: GitHub did not answer for pull request #{number}, so nothing was signed." if number else
                 "Knos settle: --tests needs the pull request the judge passed (--pull) and its head commit (--head).")
        return 1
    now = str((found.get("head") or {}).get("sha") or "")
    if not head or now != head:
        run.note(f"Knos settle: pull request #{number} is at commit `{now[:7]}` now, and its acceptance checks passed at "
                 f"`{str(head)[:7] or 'no commit'}`, so nothing was signed. They run again for the new commit.")
        return 0
    if found.get("state") != "open" and not found.get("merged_at"):
        run.note(f"Knos settle: pull request #{number} was closed without being merged, so nothing was signed.")
        return 0
    _settle_safely(run, rp, found, run.began, "its acceptance checks passed", False, False, int(issue or 0))
    return 1 if run.failed else 0


def _named(ev: dict) -> int | None:
    """The pull request a workflow_dispatch names: its input `pull` (also read: pull_request, number, pr)."""
    inputs = ev.get("inputs") if isinstance(ev.get("inputs"), dict) else {}
    for key in ("pull", "pull_request", "number", "pr"):
        if str(inputs.get(key) or "").strip().lstrip("#").isdigit():
            return int(str(inputs[key]).strip().lstrip("#"))
    return None


def _merged_by(run: Run, rp: dict) -> tuple[list[dict], bool]:
    """The pull requests a push to the default branch merged, lowest number first, and whether GitHub answered for
    every pushed commit. GitHub names the pull request that brought each commit in; one counts when it is merged,
    its base is the default branch and its merge commit is among the pushed ones. That holds for a merge commit, a
    squash (the one new commit is the merge) and a rebase (the last rebased commit is)."""
    ev = run.event
    shas = [s for s in dict.fromkeys([*(c.get("id") for c in ev.get("commits") or [] if isinstance(c, dict)), ev.get("after")])
            if isinstance(s, str) and re.fullmatch(r"[0-9a-f]{40}", s) and s.strip("0")]
    if ev.get("ref") != f"refs/heads/{rp['branch']}" or ev.get("deleted") or not shas:
        return [], True
    numbers, whole = set(), True
    if len(shas) > MAX_COMMITS:
        run.note(f"Knos settle: this push has {len(shas)} commits; the newest {MAX_COMMITS} were looked at. `/knos settle` on a "
                 "merged pull request settles it whatever the push.")
    for sha in reversed(shas[-MAX_COMMITS:]):       # the newest first: the head commit is the merge
        got = _brought_in(run, sha, patient=sha == ev.get("after"))
        if got is None:
            whole = False
            continue
        numbers.update(int(p["number"]) for p in got if isinstance(p, dict) and str(p.get("number") or "").isdigit())
    pulls = []
    for n in sorted(numbers):
        p = _pull(run, n)
        if p is None:
            whole = False
        elif p.get("merged_at") and (p.get("base") or {}).get("ref") == rp["branch"] and p.get("merge_commit_sha") in shas \
                and str(((p.get("base") or {}).get("repo") or {}).get("full_name") or run.repo).lower() == run.repo.lower():
            pulls.append(p)
    return pulls, whole


def _brought_in(run: Run, sha: str, patient: bool) -> list | None:
    """GET /repos/{repo}/commits/{sha}/pulls: the pull requests GitHub ties to a commit. Right after a merge the
    answer can be empty for a moment, so the head commit (`patient`) is asked again, briefly. None: no answer."""
    got = None
    for pause in (2, 4, None) if patient else (None,):
        try:
            got = run.github(f"repos/{run.repo}/commits/{sha}/pulls?per_page=100")
        except Exception:  # noqa: BLE001
            got = None
        if got or pause is None:
            break
        run.sleep(pause)
    return got if isinstance(got, list) else None


def _settle_safely(run: Run, rp: dict, pull: dict, since: float, after: str, asked: bool, tips_only: bool,
                   tests: int | None = None) -> None:
    """One pull request, and whatever goes wrong with it is said, never dropped: on the pull request when someone
    asked or a token had already left, else on the run's page."""
    sent = run.relayed
    try:
        _settle_one(run, rp, pull, since, after, asked, tips_only, tests)
    except Exception as why:  # noqa: BLE001
        run.failed = True
        text = (f"Knos: this pull request's payment stopped before it was finished ({type(why).__name__}: {_short(why)}). "
                "`/knos status` shows what is in escrow" + ("." if tests is not None else "; comment `/knos settle` to try again."))
        if asked or run.relayed != sent:
            run.say(pull["number"], text)
        else:
            run.note(f"#{pull['number']}: {text}")


def _judged(cases: list[Case], issue: int = 0) -> list[Case]:
    """The cases the sandboxed judge decides: the bounties paid by acceptance checks on one issue (`issue`; else the
    first issue the pull request closes that has one, which is the one `knos review` names for the judge)."""
    found = [c for c in cases if not c.tip and c.jobs[0][1].mode == pay.TESTS and issue in (0, c.issue)]
    return [c for c in found if c.issue == found[0].issue]


def _settle_one(run: Run, rp: dict, pull: dict, since: float, after: str, asked: bool, tips_only: bool, tests: int | None) -> None:
    """One pull request where money moves: decide, sign and relay a token for each case where everything holds, and
    write the one comment. `asked`: a person asked, so there is an answer even when nothing is in escrow. `tests`
    (not None): the sandboxed judge passed for it, and only the bounty it judged (issue `tests`, 0: the first) is
    this job's to sign; None: the pull request is merged, and what the merge pays is."""
    number, head, by_tests = int(pull["number"]), (pull.get("head") or {}).get("sha") or "", tests is not None
    cases, closes, listed, blind, held = _find(run, rp, pull, tips_only)
    if by_tests:
        cases = _judged(cases, tests)
        blind = [n for n in blind if n != number and tests in (0, n)]
    runs, statuses = _record(run, head, cases, CHECKS_WAIT)
    _judge(run, pull, cases, listed, True, runs, statuses, tests=by_tests)
    for c in cases:
        if c.verdict() == "yes":
            try:
                _prove(run, rp, pull, c, since)
            except Exception as why:  # noqa: BLE001 - one job's trouble is its own: the others are still paid
                c.unread.append(f"its signed token could not be made ({type(why).__name__}: {_short(why)})")
    if any(c.verdict() == "unread" or (c.result is not None and not c.result.get("ok")) for c in cases):
        run.failed = True
    parts = [_after(run, c, pull, after, by_tests) for c in cases] + ([] if by_tests else _left_out(run, number))
    if blind and (asked or closes or parts or tips_only):     # a push says nothing about a pull request that closes no issue
        run.failed = True
        parts += _unseen(blind, number, AGAIN[by_tests])
    if not parts:
        if not asked:
            unlisted = listed is None and not closes     # GitHub did not say what it closes: not known, which is not "nothing"
            run.failed = run.failed or (unlisted and not tips_only)
            run.note(f"Knos settle: no bounty paid by acceptance checks is open for an issue pull request #{number} closes, so "
                     "there was nothing to sign." if by_tests else
                     f"Knos settle: no tip waits for pull request #{number} on Solana, so there is nothing to pay." if tips_only else
                     f"Knos settle: GitHub did not say which issues pull request #{number} closes. If one of them has a bounty, "
                     "`/knos settle` on the pull request pays it." if unlisted else
                     f"Knos settle: Solana could not be read for pull request #{number}, which closes no issue. `/knos settle` on it "
                     "pays a tip that waits for it." if blind else
                     f"Knos settle: nothing is in escrow for pull request #{number}, so there is nothing to pay.")
            return
        parts = [_nothing(run, pull, closes, listed, held, tips_only, True)]
    run.say(number, _join(parts))


def _prove(run: Run, rp: dict, pull: dict, c: Case, since: float) -> None:
    """Everything a case asks for holds: have GitHub sign the token and carry it to the chain. The token names the
    payee's own address only when no wallet is bound (a bound wallet is where the chain pays whatever is named)."""
    address = c.where.get("address") if c.where.get("from") == "comment" else None
    job = c.jobs[0][1]
    aud = pay.pay_audience(rp["id"], c.issue, int(c.paid["id"]), (pull.get("head") or {}).get("sha") or "", bytes(job.terms),
                           job.mode, address)
    try:
        jwt = run.mint(aud)
    except Exception as why:  # noqa: BLE001
        c.unread.append(f"GitHub did not sign the token ({_short(why)})")
        return
    pin = _pin(jwt)
    if pin and not any(bytes(j.wf_repo_hash) == pin[0] and j.wf_sha == pin[1] for _a, j in c.jobs):
        c.why.append(f"it was funded through Knos's workflows at commit `{job.wf_sha[:7]}`, and only a run at that commit is accepted for it; this "
                     f"run used `{pin[1][:7]}`")
        c.fix.append(f"Point this repository's workflow file at commit `{job.wf_sha}` of Knos's workflows again.")
        return
    c.result = deliver("proof", jwt, int(pull["number"]), run=run, since=since)


# ---- knos review and knos check --------------------------------------------------------------------------------------

def review(run: Run) -> int:
    """`knos review`: the check again where it may write. Finds the pull request of the finished "knos check" run by
    its head commit and head repository, and keeps one comment on it up to date: whether it takes a bounty and
    which, what is still missing, who would be paid and how to give an address. What the judge learned goes to the
    knos-memory issue. When the pull request closes an issue whose bounty is paid by acceptance checks, what the
    workflow's next jobs need is written to $GITHUB_OUTPUT: `tests` and `issue` (that issue's number), `pull`,
    `head` (the commit reviewed) and `terms` (the bounty's terms as funded, one line of JSON)."""
    wr, rp = run.event.get("workflow_run") or {}, _repo(run)
    pull = _pull_of(run, wr) if rp and wr.get("event") == "pull_request" else None
    if pull is None:
        run.note("Knos review: no open pull request has this run's head commit (it has moved on, or the run was not for a pull "
                 "request), so there is nothing to review.")
        return 0
    try:
        found, head = _find(run, rp, pull), (pull.get("head") or {}).get("sha") or ""
        parts, _gate_said = _advice(run, rp, pull, found, 0, True, True)
        judged = next((c for c in _judged(found[0]) if c.terms), None)      # tests mode: the sandboxed judge runs next, on this
        if judged is not None:
            for name, value in (("tests", judged.issue), ("issue", judged.issue), ("pull", int(pull["number"])), ("head", head),
                                ("terms", judged.raw.decode("ascii"))):
                run.output(name, value)
        return 0 if _publish(run, int(pull["number"]), parts, head) else 1
    except Exception as why:  # noqa: BLE001
        run.note(f"Knos review: stopped before it was finished ({type(why).__name__}: {_short(why)}). `/knos status` on the pull "
                 "request says the same things.")
        return 1


def check(run: Run) -> int:
    """`knos check`: the advisory check on a pull request. It comments nowhere and writes nothing on any chain; its
    answer is the job's summary and its exit status: 1 only for a claim in the description that a finished check
    contradicts, or a broken rule of the repository (its CONTRIBUTING file, and what its history made required).
    Never for an issue that is not funded, a bounty this pull request does not take, or anything that is pending or
    could not be read. It waits up to ten minutes for the checks it reads to finish, so the review that follows it
    sees how they ended."""
    pull, rp = run.event.get("pull_request"), _repo(run)
    if not isinstance(pull, dict) or not pull.get("number") or rp is None:
        run.note("Knos check: this run is not for a pull request Knos could read, so nothing was checked.")
        return 0
    try:
        found = _find(run, rp, pull)
        parts, gate_said = _advice(run, rp, pull, found, CLAIMS_WAIT, False, False)
    except Exception as why:  # noqa: BLE001 - Knos's own failure is never held against a pull request
        run.note(f"Knos check: stopped before it was finished ({type(why).__name__}: {_short(why)}). Nothing is held against "
                 "this pull request.")
        return 0
    broken = bool(gate_said["reasons"])
    run.note("### Knos check\n\n" + _join(parts or ["this pull request closes no funded issue, and nothing is held against it."], "")
             + "\n\n" + ("**Failed**: a claim in the description is false, or a rule of this repository is broken (listed above)."
                         if broken else "**Passed**: no false claim and no broken rule."
                         + (" Whether this pull request takes a bounty never fails this check." if parts else "")))
    return 1 if broken else 0


def _pull_of(run: Run, wr: dict) -> dict | None:
    """The open pull request a finished workflow run was for: the one whose head is the run's head commit in the
    run's head repository. A fork's run names no pull request, so it is found by its head branch and checked."""
    sha, branch = str(wr.get("head_sha") or ""), str(wr.get("head_branch") or "")
    head = wr.get("head_repository") or {}
    owner, full = str((head.get("owner") or {}).get("login") or ""), str(head.get("full_name") or "").lower()
    found = _read(run, f"repos/{run.repo}/pulls?state=open&head={urllib.parse.quote(f'{owner}:{branch}', safe='')}&per_page=100")
    if not isinstance(found, list) or not any(isinstance(p, dict) and (p.get("head") or {}).get("sha") == sha for p in found):
        found = _read(run, f"repos/{run.repo}/commits/{sha}/pulls?per_page=100")
    for p in found if isinstance(found, list) else []:
        at = (p.get("head") or {}) if isinstance(p, dict) else {}
        if at.get("sha") == sha and p.get("state") == "open" and str((at.get("repo") or {}).get("full_name") or "").lower() == full:
            return _pull(run, p.get("number"))
    return None


def _advice(run: Run, rp: dict, pull: dict, found: tuple, wait: float, payee: bool, learn: bool) -> tuple[list[str], dict]:
    """What the automatic check says of a pull request before its merge, as paragraphs, and the gate's own verdict:
    each bounty it could take judged against GitHub's record so far (`found` is `_find`'s answer), then the free
    check on its description and on the repository's rules (knos.judge.gate), then a funded issue it mentions
    without closing. Runs none of its code. `wait`: seconds to wait for checks that have not finished; `payee`: say
    who would be paid; `learn`: write what the judge learned to its memory."""
    from .proof import claims
    cases, closes, listed, blind, _held = found
    number, head, body = int(pull["number"]), (pull.get("head") or {}).get("sha") or "", pull.get("body") or ""
    claimed = bool(claims.read(body).kinds & {"tests", "ci"})
    runs, statuses = _record(run, head, cases, wait, every=claimed and wait > 0)
    _judge(run, pull, cases, listed, False, runs, statuses, payee=payee)
    funded = sorted({c.issue for c in cases if not c.tip} | set(_mentioned(run, rp, pull, closes)))
    g = _gate(run, pull, runs, statuses, funded, learn)
    parts = [_before(run, c, pull) for c in cases]
    if g["reasons"]:
        parts.append("this is held against the pull request, bounty or not:" + "".join(f"\n- {r.split(': ', 1)[-1]}" for r in g["reasons"]))
    parts += [*(f"{u}." for u in g["evidence"]["unverified"]), *g["evidence"]["notes"], *_left_out(run, number),
              *_unseen(blind, number, "comment `/knos status`")]
    return parts, g


def _mentioned(run: Run, rp: dict, pull: dict, closes: list[int]) -> list[int]:
    """The funded issues a description names without closing: the bounty its author may have meant."""
    named = [int(a or b) for a, b in re.findall(r"#(\d{1,10})\b|/issues/(\d{1,10})\b", closing.cut(pull.get("body") or ""))]
    funded = []
    for n in list(dict.fromkeys(named))[:MAX_ISSUES]:
        if n in closes or n == pull.get("number"):
            continue
        try:
            if any(j.state == "open" for _a, j in _jobs(run, rp["id"], n)):
                funded.append(n)
        except Exception:  # noqa: BLE001 - a hint, not a decision: left out when the chain does not answer
            pass
    return funded


def _gate(run: Run, pull: dict, runs, statuses, funded: list[int], learn: bool) -> dict:
    """knos.judge.gate on a pull request, with nothing checked out: the description's claims against GitHub's record
    of its head commit, the repository's CONTRIBUTING rules as its base has them, and what the judge's memory (the
    knos-memory issue, loaded into Sibyl's local store) requires. `learn`: post what this run learned there."""
    from . import judge
    from .proof import history, memory
    root = run.scratch()
    try:
        store = history.SibylStore.local(root / "memory")
        if memory.pull(run.repo, store, run.github) is None:
            run.note("Knos: the judge's memory (the knos-memory issue) could not be read; this run remembers nothing.")
    except Exception:  # noqa: BLE001 - no store: the same check, without memory
        store = history.NullStore()
    g = judge.gate(_rules(run, pull, root / "base"), _diff(_changes(run, int(pull["number"]))), store, run.repo,
                   (pull.get("user") or {}).get("login"), pull.get("body") or "", runs, statuses=statuses, funded=funded)
    if learn:
        try:
            memory.push(run.repo, store, run.github, run.github, str(run.env.get("GITHUB_RUN_ID") or ""))
        except Exception as why:  # noqa: BLE001
            run.note(f"Knos: what this run learned could not be written to the knos-memory issue ({_short(why)}).")
    return g


def _rules(run: Run, pull: dict, to: Path) -> Path:
    """The base's CONTRIBUTING file in a folder of its own. The gate reads a repository's rules from the base,
    never from the pull request, and this job checks nothing out."""
    from .proof import history
    base = pull.get("base") or {}
    ref = urllib.parse.quote(str(base.get("sha") or base.get("ref") or ""), safe="")
    to.mkdir(parents=True, exist_ok=True)
    for rel in history.CONTRIBUTING:
        got = _read(run, f"repos/{run.repo}/contents/{rel}?ref={ref}")
        if isinstance(got, dict) and got.get("encoding") == "base64" and isinstance(got.get("content"), str):
            (to / rel).parent.mkdir(parents=True, exist_ok=True)
            (to / rel).write_bytes(base64.b64decode(got["content"]))
    return to


def _diff(files: list | None) -> str | None:
    """A pull request's changes as one unified diff, from GitHub's list of its files (each file's `patch`). A file
    too large for GitHub to show has no patch, and no rule sees it. None when the list could not be read."""
    if files is None:
        return None
    out = []
    for f in files:
        if isinstance(f, dict) and f.get("filename") and isinstance(f.get("patch"), str):
            old = f.get("previous_filename") or f["filename"]
            out.append(f"diff --git a/{old} b/{f['filename']}\n--- a/{old}\n+++ b/{f['filename']}\n{f['patch']}\n")
    return "".join(out)


def _publish(run: Run, number: int, parts: list[str], head: str) -> bool:
    """The review, as the one comment this workflow keeps on a pull request: found by its marker and edited in
    place, else posted. With nothing to say, nothing is posted; a comment from an earlier run is brought up to date."""
    mine = next((c for c in terms.pages(f"repos/{run.repo}/issues/{number}/comments", run.github) or []
                 if isinstance(c, dict) and (c.get("user") or {}).get("type") == "Bot" and str(c.get("body") or "").startswith(MARK)),
                None)
    if not parts and mine is None:
        run.note(f"Knos review: pull request #{number} closes no funded issue and nothing is held against it, so nothing was "
                 "written on it.")
        return True
    text = _join(parts or ["this pull request takes no bounty now: it closes no funded issue, and nothing is held against it."])
    body = f"{MARK}\n{text}\n\nThis is how things stood at commit `{head[:7]}`. `/knos status` on this pull request says how they stand now."
    if mine is None:
        return run.say(number, body)
    try:
        run.github(f"repos/{run.repo}/issues/comments/{mine['id']}", {"body": body}, "PATCH")
    except Exception as why:  # noqa: BLE001
        run.failed = True
        run.note(f"GitHub did not take the edit of Knos's comment on #{number} ({_short(why)}):\n\n{body}")
        return False
    run.note(f"Brought Knos's comment on #{number} up to date:\n\n{body}")
    return True
