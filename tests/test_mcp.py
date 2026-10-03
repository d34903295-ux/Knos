"""`knos mcp`: the server a coding agent talks to. Every test drives the real `serve` loop over in-memory streams,
with a chain and a GitHub made of dictionaries, so nothing here opens the network (conftest refuses it anyway).

The accounts are built byte for byte as knos-pay lays them out: the first deployment's (knos.settle.pay.read_job,
read_due, read_rep) and the second's (knos.settle.v2.pay.read_job, read_bind, read_rep, read_balance; the job and
bind bytes are tests/_flow.py's).
"""

from __future__ import annotations

import io
import json

from solders.pubkey import Pubkey

from _flow import bind_bytes
from _flow import job_bytes as job2_bytes

from knos import mcp, terms, version
from knos.settle import pay
from knos.settle.v2 import pay as pay2

NOW = 1_790_000_000
WIDGETS, GADGETS, GONE = 987_654_321, 555, 777      # repository ids
MONA = 1_234_567                                     # a GitHub user id


def _u64(v: int) -> bytes:
    return v.to_bytes(8, "little")


def job_bytes(repo_id: int, issue: int, amount: int, state: int = 1, mode: int = 0, deadline: int = NOW + 14 * 86_400,
              pay_after: int = 0, author_id: int = 0, funder: Pubkey | None = None) -> bytes:
    data = (bytes([state, mode, 1 if funder is None else 0]) + bytes(5) + _u64(repo_id) + _u64(issue) + _u64(amount)
            + _u64(deadline) + _u64(0) + _u64(pay_after) + _u64(author_id) + _u64(42) + _u64(0) + bytes(8)
            + bytes(funder or Pubkey.default()) + bytes(pay.faucet_mint()) + bytes(32) + pay.wf_repo_hash("drexthealpha/Knos")
            + b"c" * 40)
    assert len(data) == 256
    return data


def due_bytes(user_id: int, mint: Pubkey, amount: int) -> bytes:
    return _u64(amount) + _u64(user_id) + bytes(mint)


def rep_bytes(paid_jobs: int, total: int, repositories: int) -> bytes:
    return paid_jobs.to_bytes(4, "little") + bytes(4) + _u64(total) + repositories.to_bytes(4, "little") + bytes(12)


class Ledger:
    """The reads knos.mcp makes of a cluster, over a dictionary of accounts for each deployment's escrow (`accounts`:
    the first's; `second`: the second's, with `logs`: the terms JSON each job's funding transaction logged)."""

    def __init__(self, accounts: dict | None = None, down: bool = False, second: dict | None = None, logs: dict | None = None):
        self.accounts, self.down, self.second, self.logs = dict(accounts or {}), down, dict(second or {}), dict(logs or {})

    def _up(self) -> None:
        if self.down:
            raise OSError("connection refused\nTraceback (most recent call last): ...")

    def account(self, address):
        self._up()
        return self.accounts.get(address, self.second.get(address))

    def program_accounts(self, program, size: int, memcmp: dict[int, bytes] | None = None):
        self._up()
        assert program in (pay.PAY_ID, pay2.PAY_ID)
        return [(a, d) for a, d in (self.accounts if program == pay.PAY_ID else self.second).items()
                if len(d) == size and all(d[o:o + len(b)] == b for o, b in (memcmp or {}).items())]

    def log_of(self, address, marker: str, check=None):
        self._up()
        lines = ["knos2:funded repo=1", *(["knos2:terms " + self.logs[address].decode()] if address in self.logs else [])]
        return next((x for x in lines if x.startswith(marker) and (check is None or check(x))), None)

    def now(self) -> int:
        self._up()
        return NOW


class GitHub:
    """api.github.com as a dictionary of paths; a path it does not have is the network failing."""

    def __init__(self, pages: dict | None = None):
        self.pages, self.asked = dict(pages or {}), []

    def __call__(self, path: str):
        self.asked.append(path)
        if path not in self.pages:
            raise OSError(f"no route to api.github.com for {path}")
        return self.pages[path]


def talk(messages: list, ledger=None, github=None) -> list:
    """Run the server over these lines (dicts are serialised; strings are sent as they are). Every line it wrote."""
    stdin = io.StringIO("".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages))
    stdout = io.StringIO()
    mcp.serve(stdin, stdout, ledger=ledger, github=github or GitHub())
    lines = stdout.getvalue().splitlines()
    assert stdout.getvalue() == "".join(line + "\n" for line in lines)     # whole lines, nothing else
    return [json.loads(line) for line in lines]                             # and every one of them is JSON


def call(name: str, arguments: dict | None = None, ledger=None, github=None) -> dict:
    """One tools/call, the 2026-07-28 way. Returns the result."""
    params = {"name": name, "arguments": arguments or {}, "_meta": {mcp.VERSION_KEY: mcp.PROTOCOL}}
    (reply,) = talk([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}], ledger, github)
    assert reply["id"] == 1 and reply["result"]["resultType"] == "complete"
    return reply["result"]


def world() -> tuple[Ledger, GitHub]:
    """Three open bounties, one proven, one past its deadline; and what is waiting for mona."""
    wallet = Pubkey.from_string("4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo")
    ledger = Ledger({
        pay.job_pda(WIDGETS, 7): job_bytes(WIDGETS, 7, 20_000_000),
        pay.job_pda(WIDGETS, 7, wallet): job_bytes(WIDGETS, 7, 5_000_000, state=2, pay_after=NOW + 1800,
                                                   author_id=MONA, funder=wallet),
        pay.job_pda(GADGETS, 3): job_bytes(GADGETS, 3, 50_000_000, mode=1),
        pay.job_pda(GONE, 1): job_bytes(GONE, 1, 1_000_000),
        pay.job_pda(GADGETS, 9): job_bytes(GADGETS, 9, 90_000_000, deadline=NOW - 60),
        pay.due_pda(MONA, pay.faucet_mint()): due_bytes(MONA, pay.faucet_mint(), 19_500_000),
        pay.rep_pda(MONA): rep_bytes(3, 61_250_000, 2),
    })
    github = GitHub({
        f"repositories/{WIDGETS}": {"id": WIDGETS, "full_name": "octo/widgets"},
        f"repositories/{GADGETS}": {"id": GADGETS, "full_name": "acme/gadgets"},
        "repos/octo/widgets": {"id": WIDGETS, "full_name": "octo/widgets"},
        "repos/acme/gadgets/issues/3": {"number": 3, "title": "Retry the upload", "assignee": None, "assignees": [],
                                        "labels": [{"id": 1, "name": "bug", "color": "d73a4a"},
                                                   {"id": 2, "name": "good first issue", "color": "7057ff"}]},
        "repos/octo/widgets/issues/7": {"number": 7, "title": "Add a --json flag", "labels": [],
                                        "assignee": {"login": "mona"}, "assignees": [{"login": "mona"}]},
        "users/mona": {"id": MONA, "login": "mona"},
    })
    return ledger, github


# ---- the protocol ---------------------------------------------------------------------------------------------------


def test_a_session_client_shakes_hands_lists_the_tools_and_calls_one():
    ledger, github = world()
    hello, listed, called = talk([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": "three", "method": "tools/call", "params": {"name": "knos_due", "arguments": {"login": "mona"}}},
    ], ledger, github)
    assert hello == {"jsonrpc": "2.0", "id": 1, "result": {
        "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
        "serverInfo": {"name": "knos", "version": version()}, "instructions": mcp.INSTRUCTIONS}}
    assert "knos_bounties" in mcp.INSTRUCTIONS and "only when it is true" in mcp.INSTRUCTIONS
    tools = listed["result"]["tools"]
    assert [t["name"] for t in tools] == ["knos_bounties", "knos_bounty", "knos_check_pr", "knos_due"]
    assert listed["result"]["ttlMs"] == 300_000 and listed["result"]["cacheScope"] == "public"
    for t in tools:
        assert t["annotations"] == {"readOnlyHint": True, "openWorldHint": True} and t["title"] and t["description"]
        assert t["inputSchema"]["type"] == "object" and t["inputSchema"]["additionalProperties"] is False
    assert called["id"] == "three" and called["result"]["isError"] is False


def test_an_unknown_session_version_is_answered_with_the_newest_one_and_ping_is_empty():
    hello, pong = talk([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}},
                        {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
    assert hello["result"]["protocolVersion"] == "2025-11-25" and pong == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_a_stateless_client_discovers_lists_and_calls_with_no_handshake():
    ledger, github = world()
    meta = {"_meta": {mcp.VERSION_KEY: "2026-07-28"}}
    found, listed, called = talk([
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": meta},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": meta},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "knos_due", "arguments": {"login": "mona"}, **meta}},
    ], ledger, github)
    assert found["result"] == {"resultType": "complete", "supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}},
                               "instructions": mcp.INSTRUCTIONS,
                               "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "knos", "version": version()}}}
    assert listed["result"]["resultType"] == "complete" and len(listed["result"]["tools"]) == 4
    result = called["result"]
    assert result["resultType"] == "complete" and result["isError"] is False
    assert result["content"] == [{"type": "text", "text": json.dumps(result["structuredContent"], indent=1)}]


def test_a_protocol_version_the_server_does_not_speak_is_refused_by_name():
    (reply,) = talk([{"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {"_meta": {mcp.VERSION_KEY: "2027-01-01"}}}])
    assert reply == {"jsonrpc": "2.0", "id": 9, "error": {"code": -32022, "message": "Unsupported protocol version",
                                                         "data": {"supported": ["2026-07-28"], "requested": "2027-01-01"}}}


def test_a_notification_is_never_answered():
    assert talk([{"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1, "reason": "x"}},
                 {"jsonrpc": "2.0", "method": "tools/list"},                 # even one that names a real method
                 {"jsonrpc": "2.0", "method": "no/such/thing"},
                 {"jsonrpc": "2.0", "id": 4, "result": {}},                  # a reply to nothing this server asked
                 ""]) == []


def test_a_line_that_is_not_json_gets_a_parse_error_and_the_server_keeps_going():
    bad, wrong, pong = talk(["{not json", "[]", {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
    assert bad == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    assert wrong["error"]["code"] == -32600 and wrong["id"] is None
    assert pong["result"] == {}


def test_unknown_methods_tools_and_shapes_are_json_rpc_errors():
    replies = talk([{"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "knos_pay_me"}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"arguments": {}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": ["knos_due"]}])
    assert [r["error"]["code"] for r in replies] == [-32601, -32602, -32602, -32602]
    assert [r["id"] for r in replies] == [1, 2, 3, 4] and "knos_pay_me" in replies[1]["error"]["message"]


def test_a_batch_from_an_older_client_is_answered_as_a_batch():
    (replies,) = talk([json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                   {"jsonrpc": "2.0", "id": 2, "method": "ping"}])])
    assert [r["id"] for r in replies] == [1, 2]


def test_starting_the_server_asks_nothing_of_the_chain_or_github(monkeypatch):
    """No ledger is given, so one would be made on first use: it never is, and GitHub is never asked."""
    from knos import chain
    monkeypatch.setattr(chain, "ledger", lambda: (_ for _ in ()).throw(AssertionError("the chain was touched")))
    github = GitHub()
    replies = talk([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "server/discover"}], None, github)
    assert len(replies) == 3 and all("result" in r for r in replies) and github.asked == []


# ---- the tools ------------------------------------------------------------------------------------------------------


def test_knos_bounties_lists_open_work_largest_first_with_what_the_author_gets():
    ledger, github = world()
    got = call("knos_bounties", {}, ledger, github)["structuredContent"]
    assert [(b["repo"], b["issue"], b["amount_usdc"], b["net_usdc"]) for b in got["bounties"]] == [
        ("acme/gadgets", 3, "50.00", "48.75"), ("octo/widgets", 7, "20.00", "19.50"), (None, 1, "1.00", "0.95")]
    first, _second, unnamed = got["bounties"]              # the proven job and the one past its deadline are not work
    assert first == {"repo": "acme/gadgets", "issue": 3, "url": "https://github.com/acme/gadgets/issues/3",
                     "amount_usdc": "50.00", "net_usdc": "48.75",
                     "paid_when": "the funder's acceptance checks pass on a pull request",
                     "refunded_after": "2026-10-05T14:13:20Z", "job": str(pay.job_pda(GADGETS, 3)), "deployment": 1,
                     "title": "Retry the upload", "labels": ["bug", "good first issue"], "assigned": False}
    assert got["bounties"][1]["paid_when"] == "a maintainer merges the pull request that closes the issue"
    assert unnamed["repo"] is None and unnamed["url"] is None and unnamed["repo_id"] == GONE   # GitHub did not answer
    assert (unnamed["title"], unnamed["labels"], unnamed["assigned"]) == (None, None, None)
    assert got["open"] == 3 and got["cluster"] == "devnet" and got["note"] == "test USDC, no real value"
    for said in ("Fixes #<issue>", "No wallet is needed to start", "GitHub account", "`/knos address <Solana address>`", "held for that account for 180 days",
                 "knos claim <address>", "https://drexthealpha.github.io/Knos/#claim"):
        assert said in got["how"]


def test_knos_bounties_can_be_held_to_one_repository_and_a_number():
    ledger, github = world()
    one = call("knos_bounties", {"repo": "octo/widgets"}, ledger, github)["structuredContent"]
    assert [(b["repo"], b["issue"]) for b in one["bounties"]] == [("octo/widgets", 7)] and one["open"] == 1
    top = call("knos_bounties", {"limit": 1}, ledger, github)["structuredContent"]
    assert [b["issue"] for b in top["bounties"]] == [3] and top["open"] == 3


def test_knos_bounties_says_what_each_issue_is_about():
    ledger, github = world()
    got = call("knos_bounties", {}, ledger, github)["structuredContent"]
    gadgets, widgets, gone = got["bounties"]
    assert (gadgets["title"], gadgets["labels"], gadgets["assigned"]) == ("Retry the upload", ["bug", "good first issue"], False)
    assert (widgets["title"], widgets["labels"], widgets["assigned"]) == ("Add a --json flag", [], True)   # none is not null
    assert (gone["title"], gone["labels"], gone["assigned"]) == (None, None, None)


def test_knos_bounties_asks_github_once_per_named_bounty_and_never_for_an_unnamed_one():
    ledger, github = world()
    call("knos_bounties", {}, ledger, github)
    assert sorted(p for p in github.asked if "/issues/" in p) == ["repos/acme/gadgets/issues/3", "repos/octo/widgets/issues/7"]
    assert f"repositories/{GONE}" in github.asked                       # it was asked who the repository is, and no more


def test_a_failed_issue_lookup_leaves_its_fields_null_and_the_bounty_listed():
    ledger, github = world()
    del github.pages["repos/acme/gadgets/issues/3"]                     # this one lookup fails; the other does not
    got = call("knos_bounties", {}, ledger, github)
    assert got["isError"] is False
    gadgets, widgets, _gone = got["structuredContent"]["bounties"]
    assert (gadgets["title"], gadgets["labels"], gadgets["assigned"]) == (None, None, None)
    assert (gadgets["repo"], gadgets["issue"], gadgets["amount_usdc"]) == ("acme/gadgets", 3, "50.00")   # nothing else lost
    assert widgets["title"] == "Add a --json flag"
    for odd in ({"title": "no labels key"}, {"title": None, "labels": [], "assignees": []},
                {"title": "t", "labels": [{"name": None}], "assignees": []}):   # answers that are not an issue
        github.pages["repos/acme/gadgets/issues/3"] = odd
        row = call("knos_bounties", {}, ledger, github)["structuredContent"]["bounties"][0]
        assert (row["title"], row["labels"], row["assigned"]) == (None, None, None)       # never the text "None"


def test_github_refusing_the_issue_lookups_does_not_fail_the_list():
    import urllib.error
    ledger, github = world()

    def get(path: str):
        if "/issues/" in path:
            raise urllib.error.HTTPError(f"https://api.github.com/{path}", 403, "rate limit", None, None)
        return github(path)

    got = call("knos_bounties", {}, ledger, get)
    assert got["isError"] is False
    rows = got["structuredContent"]["bounties"]
    assert [b["repo"] for b in rows] == ["acme/gadgets", "octo/widgets", None]
    assert all((b["title"], b["labels"], b["assigned"]) == (None, None, None) for b in rows)


def test_an_issue_with_an_assignee_in_either_field_is_assigned():
    ledger, github = world()
    github.pages["repos/acme/gadgets/issues/3"] = {"title": "t", "labels": [], "assignee": {"login": "mona"}}
    assert call("knos_bounties", {}, ledger, github)["structuredContent"]["bounties"][0]["assigned"] is True
    github.pages["repos/acme/gadgets/issues/3"] = {"title": "t", "labels": [], "assignees": [{"login": "mona"}]}
    assert call("knos_bounties", {}, ledger, github)["structuredContent"]["bounties"][0]["assigned"] is True


def test_knos_bounty_shows_every_job_on_an_issue_and_its_state():
    ledger, github = world()
    got = call("knos_bounty", {"issue": "octo/widgets#7"}, ledger, github)["structuredContent"]
    opened, proven = got["bounties"]
    assert (opened["state"], opened["mode"], opened["amount_usdc"], opened["refunded_after"]) == (
        "open", "merge", "20.00", "2026-10-05T14:13:20Z") and "author_id" not in opened
    assert (proven["state"], proven["author_id"], proven["released_in_s"], proven["amount_usdc"]) == ("proven", MONA, 1800, "5.00")
    assert got["issue"] == "octo/widgets#7" and got["said"] == "2 bounties in escrow for octo/widgets#7."
    none = call("knos_bounty", {"issue": "octo/widgets#8"}, ledger, github)
    assert none["isError"] is False and none["structuredContent"]["bounties"] == []
    assert none["structuredContent"]["said"].startswith("no bounty in escrow for octo/widgets#8")


def _pull(body: str, runs: list[dict], sha: str = "a" * 40) -> GitHub:
    return GitHub({"repos/octo/widgets/pulls/12": {"number": 12, "body": body, "head": {"sha": sha}},
                   f"repos/octo/widgets/commits/{sha}/check-runs?per_page=100&page=1": {"check_runs": runs}})


def _run(name: str, conclusion: str | None) -> dict:
    return {"name": name, "status": "completed" if conclusion else "in_progress", "conclusion": conclusion}


def test_knos_check_pr_holds_the_description_to_githubs_record():
    says = "Fixes #7. All tests pass and CI is green."
    got = call("knos_check_pr", {"pr": "octo/widgets#12"}, None, _pull(says, [_run("unit", "failure"), _run("lint", "success"),
                                                                          _run("e2e", "timed_out")]))["structuredContent"]
    assert got == {"pr": "octo/widgets#12", "head": "a" * 40, "claims": ["CI is green", "tests pass"], "verdict": "false",
                   "failed_checks": ["e2e", "unit"],
                   "said": "The description says CI is green and tests pass, but these checks failed at the head commit: e2e, unit."}

    def verdict(body: str, runs: list[dict], pr: str = "octo/widgets#12") -> tuple:
        out = call("knos_check_pr", {"pr": pr}, None, _pull(body, runs))["structuredContent"]
        return out["verdict"], out["claims"], out["failed_checks"]

    assert verdict("Fixes #7. Tests pass.", [_run("unit", "success")]) == ("true", ["tests pass"], [])
    assert verdict("Fixes #7. Tests pass.", [_run("unit", "success")], "https://github.com/octo/widgets/pull/12/files")[0] == "true"
    assert verdict("Fixes #7. Tests pass.", [_run("unit", "success"), _run("e2e", None)])[0] == "checks still running"
    assert verdict("Fixes #7. Tests pass.", [_run("unit", "failure"), _run("e2e", None)])[0] == "false"   # a failure is final
    assert verdict("Fixes #7. Refactors the parser.", [_run("unit", "failure")]) == ("no claim", [], [])
    assert verdict("Fixes #7. Tests pass.", []) == ("no checks", ["tests pass"], [])   # nothing recorded is not "true"
    assert verdict("Fixes #7. Tests pass.", [_run("knos / gate", "failure")])[0] == "no checks"   # Knos's own check is not evidence


def test_knos_check_pr_says_so_when_the_checks_cannot_be_read():
    github = GitHub({"repos/octo/widgets/pulls/12": {"number": 12, "body": "Tests pass.", "head": {"sha": "b" * 40}}})
    got = call("knos_check_pr", {"pr": "octo/widgets#12"}, None, github)
    assert got["isError"] is True and "could not be checked" in got["content"][0]["text"]


def test_knos_due_shows_what_waits_what_was_paid_and_how_to_claim():
    ledger, github = world()
    got = call("knos_due", {"login": "@mona"}, ledger, github)["structuredContent"]
    assert got["login"] == "mona" and got["user_id"] == MONA
    old = got["first_deployment"]
    assert old["waiting"] == [{"mint": str(pay.faucet_mint()), "amount_usdc": "19.50"}] and old["waiting_usdc"] == "19.50"
    assert old["paid"] == {"pull_requests": 3, "repositories": 2, "total_usdc": "61.25"}
    assert got["said"] == "mona has bound no wallet. The first deployment holds 19.50 USDC for mona."
    assert "knos claim <address>" in got["how"] and "https://drexthealpha.github.io/Knos/#claim" in got["how"] and "knos claim --v1 <address>" in got["how"]
    assert (got["wallet"], got["held"], got["balances"]) == (None, [], []) and got["record"]["paid"] == 0
    github.pages["users/nobody"] = {"id": 5, "login": "nobody"}
    empty = call("knos_due", {"login": "nobody"}, ledger, github)["structuredContent"]
    assert empty["first_deployment"]["waiting"] == [] and empty["first_deployment"]["paid"]["pull_requests"] == 0
    assert empty["said"] == "nobody has bound no wallet. Nothing is waiting for nobody."


# ---- the second deployment: where new funding goes -------------------------------------------------------------------

BOUGHT = {"accept": "", "checks": [{"app": 15368, "name": "build"}, {"app": 15368, "name": "test"}], "deny": [".github/**", ".knos/**"],
          "mode": "merge", "paths": ["src/**"], "reserve": 7, "v": 1}
WALLET = "4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo"
JUNK = Pubkey.from_string("So11111111111111111111111111111111111111112")      # a mint that is nobody's USDC
HUBOT_ID = 4242                                                                 # the owner of octo/widgets


def balance_bytes(owner_id: int, authority: Pubkey, mint: Pubkey, cap: int = 0, spenders=(), faucet: bool = False, spent: int = 0) -> bytes:
    d = bytearray(pay2.BALANCE_LEN)
    d[0], d[2] = 1, int(faucet)
    d[8:16], d[16:48], d[48:80], d[80:88] = _u64(owner_id), bytes(authority), bytes(mint), _u64(cap)
    for k, who in enumerate(spenders):
        d[96 + 8 * k:104 + 8 * k] = _u64(who)
    d[128:136] = _u64(spent)
    return bytes(d)


def token_bytes(amount: int) -> bytes:
    return bytes(64) + _u64(amount) + bytes(93)


def rep2_bytes(paid: int, funders: int, total: int, test_paid: int, self_paid: int, test_total: int, first: int, last: int) -> bytes:
    return (paid.to_bytes(4, "little") + funders.to_bytes(4, "little") + _u64(total) + test_paid.to_bytes(4, "little")
            + self_paid.to_bytes(4, "little") + bytes(8) + _u64(test_total) + _u64(first) + _u64(last) + bytes(8))


def world2() -> tuple[Ledger, GitHub]:
    """The first deployment's world, and on the second: an open bounty of test USDC on octo/widgets#7 from a comment,
    one from a wallet in a token of its own making, one past its deadline, a payment proven and held for mona, the
    wallet bound to hubot, both records, and a balance a wallet set aside for hubot's repositories."""
    ledger, github = world()
    raw, faucet, wallet = terms.canonical(BOUGHT), pay2.faucet_balance_pda(HUBOT_ID), Pubkey.from_string(WALLET)
    digest = pay2.terms_hash(raw)
    job = lambda repo, issue, amount, source=faucet, **kw: (pay2.job_pda(repo, issue, source), job2_bytes(  # noqa: E731
        repo, issue, amount, source, digest, deadline=kw.pop("deadline", NOW + 14 * 86_400), owner_id=HUBOT_ID, funder_id=HUBOT_ID, **kw))
    balance = pay2.balance_pda(HUBOT_ID, wallet, pay2.USDC_DEVNET)
    ledger.second = dict([
        job(WIDGETS, 7, 30_000_000),
        job(WIDGETS, 7, 400_000_000, wallet, faucet=False, mint=JUNK, kind=0),
        job(GADGETS, 4, 70_000_000, deadline=NOW - 5),
        job(GADGETS, 5, 10_000_000, state=3, payee=MONA, hold_until=NOW + 180 * 86_400),
        job(WIDGETS, 11, 8_000_000, mode=1),
        (pay2.bind_pda(HUBOT_ID), bind_bytes(HUBOT_ID, WALLET)),
        (pay2.rep_pda(MONA), rep2_bytes(2, 1, 24_375_000, 5, 1, 97_500_000, NOW - 86_400, NOW - 3600)),
        (balance, balance_bytes(HUBOT_ID, wallet, pay2.USDC_DEVNET, cap=50_000_000, spenders=(MONA,), spent=120_000_000)),
        (pay2.baltok_pda(balance), token_bytes(75_500_000)),
    ])
    ledger.logs = {pay2.job_pda(WIDGETS, 7, faucet): raw, pay2.job_pda(WIDGETS, 7, wallet): raw, pay2.job_pda(WIDGETS, 11, faucet): b'{"not":"terms"}'}
    github.pages.update({"users/hubot": {"id": HUBOT_ID, "login": "hubot"},
                         "repos/octo/widgets/issues/11": {"number": 11, "title": "Slugify keeps punctuation", "labels": [], "assignees": []}})
    return ledger, github


def test_knos_bounties_lists_the_second_deployments_open_work_with_the_first(capsys):
    ledger, github = world2()
    got = call("knos_bounties", {}, ledger, github)["structuredContent"]
    rows = got["bounties"]
    # test USDC first, largest first, whichever deployment holds it; a token of someone's own making last, whatever
    # its number; held and expired jobs are not work
    assert [(b["deployment"], b["repo"], b["issue"], b["amount_usdc"], b.get("money")) for b in rows] == [
        (1, "acme/gadgets", 3, "50.00", None), (2, "octo/widgets", 7, "30.00", "test USDC"), (1, "octo/widgets", 7, "20.00", None),
        (2, "octo/widgets", 11, "8.00", "test USDC"), (1, None, 1, "1.00", None), (2, "octo/widgets", 7, None, f"token {JUNK}")]
    assert got["open"] == 6
    second = rows[1]
    assert second == {"repo": "octo/widgets", "issue": 7, "url": "https://github.com/octo/widgets/issues/7", "amount_usdc": "30.00",
                      "net_usdc": "29.25", "paid_when": "a maintainer merges the pull request that closes the issue",
                      "refunded_after": "2026-10-05T14:13:20Z", "job": str(pay2.job_pda(WIDGETS, 7, pay2.faucet_balance_pda(HUBOT_ID))),
                      "deployment": 2, "money": "test USDC", "amount_units": 30_000_000, "funded_from": "a balance, by a comment",
                      "title": "Add a --json flag", "labels": [], "assigned": True}
    assert rows[3]["paid_when"] == "the funder's acceptance checks pass on a pull request" and rows[3]["title"] == "Slugify keeps punctuation"
    assert (rows[5]["amount_units"], rows[5]["net_usdc"], rows[5]["funded_from"]) == (400_000_000, None, "a wallet")
    one = call("knos_bounties", {"repo": "octo/widgets", "limit": 2}, ledger, github)["structuredContent"]
    assert [(b["deployment"], b["issue"]) for b in one["bounties"]] == [(2, 7), (1, 7)] and one["open"] == 4


def test_knos_bounty_says_a_jobs_terms_in_words_and_who_a_held_one_waits_for():
    ledger, github = world2()
    got = call("knos_bounty", {"issue": "octo/widgets#7"}, ledger, github)["structuredContent"]
    usdc, junk, old, proven = got["bounties"]
    assert got["said"] == "4 bounties in escrow for octo/widgets#7." and [b["deployment"] for b in got["bounties"]] == [2, 2, 1, 1]
    assert (usdc["state"], usdc["mode"], usdc["amount_usdc"], usdc["money"]) == ("open", "merge", "30.00", "test USDC")
    assert usdc["terms"] == terms.describe(BOUGHT) == [
        "It is paid when a maintainer merges a pull request that closes this issue, if these checks passed at that pull request's last "
        "commit: `build`, `test`.",
        "The pull request may not change `.github/**` or `.knos/**`, and may only change files matching `src/**`.",
        "`/knos take` reserves the issue for 7 days."] and "terms_note" not in usdc
    assert (junk["amount_usdc"], junk["money"], junk["funded_from"], junk["terms"]) == (None, f"token {JUNK}", "a wallet", usdc["terms"])
    assert (old["state"], proven["state"], proven["author_id"]) == ("open", "proven", MONA) and "terms" not in old
    # a job whose logged terms do not hash to what it stores, or are not terms: said, never guessed
    eleven = call("knos_bounty", {"issue": "octo/widgets#11"}, ledger, github)["structuredContent"]["bounties"]
    assert len(eleven) == 1 and eleven[0]["terms"] is None and eleven[0]["mode"] == "tests"
    assert eleven[0]["terms_note"] == "its terms could not be read from Solana just now; `/knos status` on the issue says them"
    # proven and held for its payee; past its deadline
    github.pages["repos/acme/gadgets"] = {"id": GADGETS, "full_name": "acme/gadgets"}
    held = call("knos_bounty", {"issue": "acme/gadgets#5"}, ledger, github)["structuredContent"]["bounties"]
    assert [(b["state"], b["held_for_user_id"], b["held_until"], b["refunded_after"]) for b in held] == [
        ("held", MONA, "2027-03-20T14:13:20Z", "2027-03-20T14:13:20Z")]
    late = call("knos_bounty", {"issue": "acme/gadgets#4"}, ledger, github)["structuredContent"]["bounties"]
    assert [b["state"] for b in late] == ["past its deadline: it pays nobody now and goes back to its funder"]
    none = call("knos_bounty", {"issue": "octo/widgets#8"}, ledger, github)["structuredContent"]
    assert none["bounties"] == [] and none["said"] == ("no bounty in escrow for octo/widgets#8; a maintainer funds one by commenting "
                                                       "/knos fund 20 on it.")


def test_knos_due_shows_the_bound_wallet_what_is_held_the_record_and_the_balances():
    ledger, github = world2()
    mona = call("knos_due", {"login": "mona"}, ledger, github)["structuredContent"]
    assert mona["wallet"] is None and mona["held"] == [
        {"repo": "acme/gadgets", "repo_id": GADGETS, "issue": 5, "net_usdc": "9.75", "money": "test USDC", "net_units": 9_750_000,
         "held_until": "2027-03-20T14:13:20Z", "job": str(pay2.job_pda(GADGETS, 5, pay2.faucet_balance_pda(HUBOT_ID)))}]
    record = dict(mona["record"])
    assert "counted apart" in record.pop("note")
    assert record == {"paid": 2, "funders": 1, "total_units": 24_375_000, "first": "2026-09-20T14:13:20Z", "last": "2026-09-21T13:13:20Z",
                      "test_paid": 5, "test_total_usdc": "97.50", "self_paid": 1}
    assert mona["said"] == ("mona has bound no wallet. 1 payment is held for mona until a wallet is bound. The first deployment holds "
                            "19.50 USDC for mona.")
    assert mona["how"].startswith("Bind a wallet as this GitHub account with `knos claim <address>` or at https://drexthealpha.github.io/Knos/#claim: "
                                  "what is held is sent there") and mona["balances"] == []
    assert mona["first_deployment"]["waiting_usdc"] == "19.50"
    hubot = call("knos_due", {"login": "hubot"}, ledger, github)["structuredContent"]
    wallet = Pubkey.from_string(WALLET)
    assert hubot["wallet"] == WALLET and hubot["held"] == [] and hubot["record"]["paid"] == 0 and hubot["record"]["first"] is None
    assert hubot["balances"] == [{"balance": str(pay2.balance_pda(HUBOT_ID, wallet, pay2.USDC_DEVNET)), "holds_usdc": "75.50", "holds_units": 75_500_000,
                                  "money": "test USDC", "faucet": False, "cap_per_job_units": 50_000_000, "spender_ids": [MONA],
                                  "opened_by_wallet": WALLET}]
    assert hubot["said"] == f"hubot is paid at {WALLET}. Nothing is waiting for hubot." and hubot["how"] == "Nothing to do: payments go to the bound wallet."
    # the cluster not answering is one sentence, as for every tool
    ledger.down = True
    assert _sentence(call("knos_due", {"login": "mona"}, ledger, github)) == "Solana devnet did not answer: connection refused."


def _sentence(result: dict) -> str:
    """A failed tool's whole answer: isError, one text block, one line, no traceback, no structured content."""
    assert result["isError"] is True and "structuredContent" not in result and len(result["content"]) == 1
    text = result["content"][0]["text"]
    assert result["content"][0]["type"] == "text" and "\n" not in text and "Traceback" not in text and text.endswith(".")
    return text


def test_a_tool_that_cannot_answer_says_so_in_one_sentence():
    ledger, github = world()
    assert _sentence(call("knos_due", {"login": "mona"}, Ledger(down=True), github)).startswith("Solana devnet did not answer")
    assert _sentence(call("knos_bounties", {}, Ledger(down=True), github)).startswith("Solana devnet did not answer")
    assert _sentence(call("knos_due", {"login": "stranger"}, ledger, github)).startswith("GitHub did not answer for users/stranger")
    assert _sentence(call("knos_bounty", {"issue": "octo/nothing#1"}, ledger, github)).startswith("GitHub did not answer")
    assert _sentence(call("knos_check_pr", {"pr": "octo/widgets#99"}, ledger, github)).startswith("GitHub did not answer")


def test_githubs_own_refusals_are_told_apart():
    import urllib.error

    def refusing(code: int):
        def get(path: str):
            raise urllib.error.HTTPError(f"https://api.github.com/{path}", code, "no", None, None)
        return get

    ledger, _github = world()
    assert _sentence(call("knos_due", {"login": "mona"}, ledger, refusing(404))) == "GitHub has nothing at users/mona."
    assert "set GH_TOKEN" in _sentence(call("knos_due", {"login": "mona"}, ledger, refusing(403)))
    got = call("knos_bounties", {}, ledger, refusing(403))              # the list is still worth having without names
    assert got["isError"] is False and [b["repo"] for b in got["structuredContent"]["bounties"]] == [None, None, None]


def test_a_bad_argument_is_a_sentence_and_nothing_is_asked():
    ledger, github = world()
    for name, arguments, word in (("knos_bounty", {}, "needs issue"),
                                  ("knos_bounty", {"issue": "widgets 7"}, "owner/repo#number"),
                                  ("knos_bounty", {"issue": 7}, "must be text"),
                                  ("knos_bounties", {"limit": 51}, "from 1 to 50"),
                                  ("knos_bounties", {"limit": True}, "from 1 to 50"),
                                  ("knos_bounties", {"repo": "../../users/mona"}, "owner/name"),
                                  ("knos_bounties", {"wallet": "x"}, "no argument named wallet"),
                                  ("knos_check_pr", {"pr": "https://example.com/octo/widgets/pull/1"}, "github.com URL"),
                                  ("knos_due", {"login": "mona/../../repos"}, "GitHub login")):
        assert word in _sentence(call(name, arguments, ledger, github)), (name, arguments)
    assert github.asked == []
    (reply,) = talk([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "knos_due", "arguments": ["mona"]}}])
    assert "as an object" in _sentence(reply["result"])


def test_a_fault_inside_a_tool_is_a_sentence_too(capsys):
    ledger, github = world()
    github.pages["users/odd"] = {"id": "not a number"}      # an answer no one planned for
    assert _sentence(call("knos_due", {"login": "odd"}, ledger, github)) == "knos_due could not answer (ValueError)."
    logged = capsys.readouterr()
    assert logged.out == "" and logged.err.startswith("knos mcp: knos_due: ValueError")   # the cause goes to stderr


def test_the_command_writes_nothing_but_json_lines_to_stdout(monkeypatch, capsys):
    from knos.cli import main
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}) + "\n"
        + "oops\n" + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"))
    assert main(["mcp"]) == 0                               # stdin ended: a clean exit
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line).get("id") for line in lines] == [1, None, 2]


def test_a_name_from_a_model_cannot_reach_another_path_of_githubs_api_and_a_hostile_line_does_not_end_the_server():
    from knos import mcp
    for bad in ("../user#1", "o/..#1", "o/.#1", "-x/y#1", "o/" + "x" * 101 + "#1", "o/r/../../user#1"):
        assert not mcp._ISSUE.fullmatch(bad), bad
    assert mcp._ISSUE.fullmatch("octo/widgets#7") and mcp._ISSUE.fullmatch("a/b.c#2") and mcp._ISSUE.fullmatch("o/.github#3")
    out = io.StringIO()
    mcp.serve(io.StringIO("[" * 100_000 + "\n" + '{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), out)
    first, second = (json.loads(x) for x in out.getvalue().splitlines())
    assert first["error"]["code"] == -32700 and second == {"jsonrpc": "2.0", "id": 1, "result": {}}
