"""Carries one GitHub-signed token to the second deployment: has knos-oidc verify it, and sends the knos-pay
instruction its audience asks for. Anyone can run this and pay the fees; the money goes where the token and the chain
say, never where the relayer says. A workflow that has a relay key calls `submit` itself (knos.flow); Knos's public
worker calls it for every token a workflow posts instead (knos.proof.ghrelay).

    submit(ledger, payer, jwt, terms=None, jwks=None, now=None)
      knos2:fund:...     {"ok": True, "kind": "fund", "sigs", "job", "repo_id", "issue", "amount", "mode", "faucet", "balance", "deadline"}
      knos2:pay:...      {"ok": True, "kind": "pay", "sigs", "repo_id", "issue", "payee_id", "head",
                          "paid": [{"job", "amount", "fee", "mint", "to": wallet or None, "held_until": time or None}]}
      knos2:bind:...     {"ok": True, "kind": "bind", "sigs", "user_id", "wallet", "settled": [{"job", "amount", "fee", "mint"}]}
      knos-oidc:key:...  {"ok": True, "kind": "key", "sigs", "key", "added": bool, "refreshed": bool, "first": {...}}
                         carried to both deployments. `key`, `added` (RegisterKey: the key now waits a day and the
                         guardian) and `refreshed` (Refresh: it lives 30 days from now) are the second verifier's;
                         `first` is what knos.settle.relay answered. When only the first deployment took the token,
                         "why" says what the second refused it for.
      refused            {"ok": False, "kind", "why"}; "retry": True when the same token may succeed later ("wait":
                         seconds, when that is known), and "transient": True when the cluster, not the token, was why
      done before        the same result with "already": True and no fee spent, when the chain already shows what the
                         token asks for (another relayer carried it)

`terms` is the terms JSON whose hash a fund token's audience carries. `jwks` maps an issuer id to its JWKS document
(fetched from the issuer and kept ten minutes when not given). `now` is the chain's time (read from it when not given).

Before a fee is spent, `precheck` asks everything the two programs will ask, with reads alone: the claims, the
audience, the workflow, the key that signed, the job, the Balance, the faucet's rate, and GitHub's signature itself
(the same arithmetic as on chain, done here). Anyone can have GitHub sign any audience from a repository of their own
and post it where a relayer looks, so a relayer that paid first and asked later could be made to pay for nothing.

Round trips. A GitHub token of the usual size (the harness's are 1,770 to 1,910 bytes) takes four transactions and
three waits, whatever it asks for:

    1  Write | Write              the head of the token, side by side (one Write for a token up to 1,756 bytes)
    2  Write, Step(8)             the rest of it, and the first half of the RSA verification
    3  Step(8), <escrow>, Close   the second half, what the audience asks for, and the token account closed again

The first deployment's relay sends each chunk, each step, the escrow instruction and the close in a transaction of
its own and waits for each: seven and seven for the same token. The last Step and the escrow share a transaction when
both fit in 1,232 bytes and 1,400,000 compute units; a fund with the longest terms (600 bytes) does not, and takes two
more. tests/test_relay2.py counts every path's transactions, waits and compute units, and prints them with -s.

`ledger` needs send(ixs, payer) -> signature, account(address) -> bytes | None, program_accounts(program, size,
{offset: bytes}) -> [(address, data)] and now(); it is used better when it also has send_all, infos, recent and logs
(knos.chain.Ledger has them all).
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from ... import chain
from .. import oidc as first_oidc
from .. import relay as first
from ..relay import claims_of, header_of
from . import oidc, pay

KINDS = {"fund": "knos2:fund:", "pay": "knos2:pay:", "bind": "knos2:bind:", "key": "knos-oidc:key:"}
# GitHub's four keys of 2 Oct 2026, the root knos-oidc starts from (programs-v2/knos_oidc/src/pins.rs, GENESIS):
# RegisterKey takes these with no attestation, so a relayer that meets one the chain has not seen registers it.
GENESIS = frozenset({"29dfb1c0b82f6c6f770f45a7a78b73a30dfe33980f4a82a6fb4280ccf7417e1a",
                     "dc2cea85e2a48ec836bbde7177dff1de2a77fe17faef2276ff0d9460d7a73565",
                     "e0bfde8963254fb2f7871c4d80d968fc25e272687fd72ad90d89b3e51ea240af",
                     "478592fecdfacedd7b679f9f428586b22785ec752b6bea6cc1b793ae134975fb"})
ROTATE_REF = "drexthealpha/knos-oidc-rotate/.github/workflows/rotate.yml@"
CLAIM_REF = "drexthealpha/knos-oidc-rotate/.github/workflows/claim.yml@"
# The pins the programs hold (program_ids.json). A test build accepts test pins beside them; the tests add those here.
ROTATE_SHAS = frozenset({oidc.IDS["rotate_sha"]})
CLAIM_SHAS = frozenset({pay.IDS["claim_sha"]})
ATTESTERS = frozenset((oidc.IDS["attest_owner_id"], repo) for repo in oidc.IDS["attest_repo_ids"])
REFRESH_AFTER = 86_400      # a key is refreshed when that moves its expiry by a day or more: once a day, not per token
JWKS_TTL = 600
ROOM = chain.MAX_TX_BYTES - 4   # what this relay lets a transaction weigh (a ledger that signs another way may add 2 bytes)
# Compute units, measured in LiteSVM (tests/test_relay2.py prints them) and rounded up. The last Step also hashes and
# decodes the token, so it costs more the longer the token is: (for any token, for each of its bytes), by key size.
_LAST_STEP = {2048: (760_000, 75), 4096: (820_000, 75)}
_CU = {"faucet": 100_000, "fund": 110_000, "ata": 30_000, "pay": 130_000, "settle": 90_000, "bind": 40_000, "close": 5_000,
       "register": 45_000, "refresh": 35_000, "params": {2048: 150_000, 4096: 550_000}}
_SPARE = 50_000             # compute units left unplanned in a transaction
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")     # PKCS#1 v1.5: "a SHA-256 digest follows"
_HEX64, _HEX40 = re.compile(r"[0-9a-f]{64}"), re.compile(r"[0-9a-f]{40}")
_T_PAYER, _T_ID = 50, 82    # where a token account keeps the key that pays for it, and the token's id
# what knos-oidc means by its error numbers (knos-pay's 80-99, and the key's 76-78, are worded in pay.ERRORS)
_VERIFIER = {64: "the token is longer than the verifier takes", 65: "the signature is not as long as its key",
             67: "a wrong account for the verifier, or another run moved this token's account",
             68: "the signing key is not ready on chain", 69: "another run moved this token's account",
             70: "the signature is not the issuer's", 71: "the token is not signed with RS256", 72: "the token's issuer is not the key's",
             73: "the chain does not trust this signing key and no attestation names it",
             74: "the attestation is not the rotate workflow's, run by its schedule or by hand in the attester's repository",
             75: "the attestation has expired"}


class _Stop(Exception):
    """submit's answer, raised where it became known: a refusal, or work the chain already shows."""
    def __init__(self, result: dict):
        super().__init__(result.get("why", "already done"))
        self.result = result


def _no(kind: str | None, why: str, **more) -> _Stop:
    return _Stop({"ok": False, "kind": kind, "why": why, **more})


def kind_of(aud: str) -> str | None:
    return next((k for k, prefix in KINDS.items() if aud.startswith(prefix)), None)


def _when(t: int) -> str:
    try:
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return f"time {t}"


def _units(n: int) -> str:
    return f"{n / 1_000_000:,.2f}"


# -- the token, read here before the chain reads it ---------------------------------------------------------------------
@dataclass
class _Token:
    jwt: str
    c: dict                 # its claims
    aud: str
    kind: str | None        # what its audience asks of the escrow; None for any other audience
    issuer: int
    n: int                  # the modulus of the key that signed it
    tid: bytes
    key: Pubkey             # the verifier's account of that key
    account: Pubkey         # where this relayer has it verified

    @property
    def bits(self) -> int:
        return 8 * len(oidc.modulus_bytes(self.n))


_KEPT: dict[int, tuple[float, dict]] = {}


def _jwks(issuer: int, jwks: dict | None, fresh: bool = False) -> dict:
    """An issuer's JWKS document: the one given, else fetched from the issuer and kept ten minutes."""
    if jwks and issuer in jwks:
        return jwks[issuer]
    kept = _KEPT.get(issuer)
    if fresh or kept is None or time.monotonic() - kept[0] > JWKS_TTL:
        kept = _KEPT[issuer] = (time.monotonic(), first.fetch_jwks(issuer))
    return kept[1]


def signed(jwt: str, n: int) -> bool:
    """Whether the key with modulus `n` signed this token (RS256): what knos-oidc works out on chain in two
    transactions, worked out here for nothing, so that a forged token never costs a relayer a fee."""
    try:
        head, body, sig = jwt.split(".")
        raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        k = len(oidc.modulus_bytes(n))
        if len(raw) != k or header_of(jwt).get("alg") != "RS256":
            return False
        digest = _DIGEST_INFO + hashlib.sha256(f"{head}.{body}".encode()).digest()
        return pow(int.from_bytes(raw, "big"), 65537, n).to_bytes(k, "big") == b"\x00\x01" + b"\xff" * (k - len(digest) - 3) + b"\x00" + digest
    except (ValueError, TypeError, AttributeError):
        return False


def _open(jwt: str, me: Pubkey, jwks: dict | None) -> _Token:
    """The token, with the key its header names looked up among the issuer's published keys. Refuses one that none
    of them signed."""
    try:
        c, h = claims_of(jwt), header_of(jwt)
        aud = c["aud"] if isinstance(c["aud"], str) else c["aud"][0]
        issuer = next(i for i, url in oidc.ISSUERS.items() if c.get("iss") == url)
        kid = h.get("kid")
    except (StopIteration, KeyError, IndexError, ValueError, TypeError, AttributeError):
        raise _no(None, "this is not a token GitHub Actions or GitLab CI issued") from None
    kind = kind_of(aud)
    if not 0 < len(jwt.encode()) <= oidc.MAX_JWT:
        raise _no(kind, f"the token is {len(jwt.encode())} bytes; the verifier takes up to {oidc.MAX_JWT}")
    keys_ = dict(oidc.jwks_keys(_jwks(issuer, jwks)))
    if kid not in keys_ and not (jwks and issuer in jwks):
        keys_ = dict(oidc.jwks_keys(_jwks(issuer, jwks, fresh=True)))      # the issuer may have added a key since we last looked
    n = keys_.get(kid)
    if n is None:
        raise _no(kind, f"the issuer's key set has no key {str(kid)[:60]!r}")
    if not signed(jwt, n):
        raise _no(kind, "the signature is not the issuer's")
    tid = oidc.token_id(jwt)
    return _Token(jwt, c, aud, kind, issuer, n, tid, oidc.key_pda(issuer, n), oidc.token_pda(me, tid))


def _workflow(c: dict) -> tuple[bytes, str, str]:
    """(hash of the repository that holds the workflow, the workflow file's name, its commit) from a token's claims."""
    repo, _, rest = str(c.get("job_workflow_ref", "")).partition("/.github/workflows/")
    return pay.wf_repo_hash(repo), rest.split("@", 1)[0], str(c.get("job_workflow_sha", ""))


def _address(text: str) -> Pubkey:
    """An address written the one way Solana writes it (the programs compare the text). ValueError otherwise."""
    key = Pubkey.from_string(text)
    if str(key) != text:
        raise ValueError(text)
    return key


def _ints(c: dict, *names: str) -> list[int]:
    return [int(c[n]) for n in names]


# -- reading the chain --------------------------------------------------------------------------------------------------
def _read(ledger, addresses) -> dict[Pubkey, tuple[Pubkey | None, bytes] | None]:
    """{address: (owner, data)} for each account, None for one that does not exist: in one request when the ledger
    can do that (the owner is None when it cannot say)."""
    addresses = list(dict.fromkeys(addresses))
    many, owner = getattr(ledger, "infos", None), getattr(ledger, "owner", None)
    if many is not None:
        return dict(zip(addresses, many(addresses)))
    out = {}
    for a in addresses:
        data = ledger.account(a)
        out[a] = None if data is None else (owner(a) if owner else None, data)
    return out


def _data(got: dict, address: Pubkey) -> bytes | None:
    return got[address][1] if got.get(address) else None


def _amount(data: bytes | None) -> int:
    """What a token account holds (either token program)."""
    return int.from_bytes(data[64:72], "little") if data and len(data) >= 165 else 0


def jobs_for(ledger, repo_id: int, issue: int) -> list[tuple[Pubkey, pay.Job]]:
    """Every job on this issue that holds money: open, or held for its payee. An issue can have several: each Balance
    and each wallet funds its own."""
    found = ledger.program_accounts(pay.PAY_ID, pay.JOB_LEN, {8: repo_id.to_bytes(8, "little") + issue.to_bytes(8, "little")})
    jobs = [(addr, pay.read_job(data)) for addr, data in found]
    return sorted(((a, j) for a, j in jobs if j is not None and j.state in ("open", "held")), key=lambda x: str(x[0]))


def balances_for(ledger, owner_id: int) -> list[tuple[Pubkey, pay.Balance, int]]:
    """(address, Balance, what its token account holds now) for every Balance set aside for one GitHub owner's
    repositories: each wallet's, and on devnet the faucet's."""
    found = ledger.program_accounts(pay.PAY_ID, pay.BALANCE_LEN, {8: owner_id.to_bytes(8, "little")})
    bals = sorted(((a, b) for a, b in ((a, pay.read_balance(d)) for a, d in found) if b is not None), key=lambda x: str(x[0]))
    held = _read(ledger, [pay.baltok_pda(a) for a, _b in bals])
    return [(a, b, _amount(_data(held, pay.baltok_pda(a)))) for a, b in bals]


def held_for(ledger, user_id: int) -> list[tuple[Pubkey, pay.Job]]:
    """Every job that is held and waits for this GitHub user to bind a wallet."""
    found = ledger.program_accounts(pay.PAY_ID, pay.JOB_LEN, {0: bytes([3]), 56: user_id.to_bytes(8, "little")})
    jobs = [(addr, pay.read_job(data)) for addr, data in found]
    return sorted(((a, j) for a, j in jobs if j is not None and j.state == "held" and j.payee_id == user_id), key=lambda x: str(x[0]))


def keys(ledger) -> list[tuple[Pubkey, oidc.Key, int]]:
    """(address, Key, modulus) of every signing key the second verifier has: GitHub's first, each issuer's oldest first."""
    out = []
    for limbs in (64, 128):     # a token account can be as long as a key account, and its third byte is never a limb count
        for addr, data in ledger.program_accounts(oidc.OIDC_ID, oidc.K_HDR + 8 * limbs, {2: bytes([limbs])}):
            k = oidc.read_key(data)
            if k is not None:
                out.append((addr, k, int.from_bytes(data[oidc.K_HDR:oidc.K_HDR + 4 * limbs], "little")))
    return sorted(out, key=lambda x: (x[1].issuer, x[1].active_at, str(x[0])))


def _last(ledger, address: Pubkey) -> list[str]:
    """The transaction that last touched an account, for a result that sends none of its own. Best effort."""
    find = getattr(ledger, "last_signature", None)
    try:
        sig = find(address) if find else None
    except Exception:  # noqa: BLE001 - the verdict does not depend on finding the transaction
        sig = None
    return [sig] if sig else []


# -- what each audience asks of the escrow, decided with reads alone ----------------------------------------------------
Group = tuple[list[Instruction], int]       # instructions that go in one transaction together, and their compute units


@dataclass
class _Plan:
    t: _Token
    groups: list[Group]                     # the escrow's instructions, in order
    done: Callable[[list[str]], dict]       # the result, once they have all landed
    token: bool = True                      # whether they need the token verified (and its account closed behind them)
    alone: bool = False                     # whether the transactions after the one with the last Step depend on nothing but it
    lead: bool = False                      # whether the first group must land before the others whatever else is so (a Bind, then the payments it frees)
    closes: bool = False                    # whether a group already closes the token account
    have: bytes | None = None               # the token account as it stands
    register: list[Instruction] = field(default_factory=list)   # a signing key the chain needs first (a genesis key it has not seen)


def _github_token(t: _Token, now: int) -> None:
    """What knos-pay asks of every token before it reads the audience (gh.rs): GitHub's, a GitHub-hosted runner, and
    times that GitHub's clock wrote."""
    if t.issuer != oidc.GITHUB:
        raise _no(t.kind, "the escrow takes GitHub's tokens only")
    if t.c.get("runner_environment") != "github-hosted":
        raise _no(t.kind, "not from a GitHub-hosted runner")
    try:
        iat, exp = _ints(t.c, "iat", "exp")
    except (KeyError, ValueError, TypeError):
        raise _no(t.kind, "malformed audience or claims") from None
    if iat > now + pay.TOKEN_AHEAD or exp - iat > pay.TOKEN_LIFE:
        raise _no(t.kind, "the token's times are not GitHub's: it is dated ahead of the chain's clock, or lives longer than an hour")


def _plan_fund(ledger, me: Pubkey, t: _Token, terms: bytes | None, now: int) -> _Plan:
    kind, c, p = "fund", t.c, t.aud.split(":")
    try:
        if len(p) != 8 or p[4] not in ("0", "1") or not _HEX64.fullmatch(p[5]):
            raise ValueError(t.aud)
        issue, amount, mode, work, balance = int(p[2]), int(p[3]), int(p[4]), int(p[6]), _address(p[7])
        repo_id, owner_id, actor, iat = _ints(c, "repository_id", "repository_owner_id", "actor_id", "iat")
    except (KeyError, ValueError, TypeError):
        raise _no(kind, "malformed audience or claims") from None
    _wf_repo, wf_file, wf_sha = _workflow(c)
    if wf_file != "fund.yml":
        raise _no(kind, "a fund token must come from fund.yml")
    if c.get("event_name") not in ("issue_comment", "issues"):
        raise _no(kind, "a bounty is funded by a comment on an issue or by a new issue, and this run was started by something else")
    if str(c.get("run_attempt")) != "1":
        raise _no(kind, "this token is from a re-run, and only a run's first attempt can fund; comment again")
    if terms and (len(terms) > pay.MAX_TERMS or not all(0x20 <= b < 0x7f for b in terms) or pay.terms_hash(terms).hex() != p[5]):
        raise _no(kind, "these are not the terms GitHub signed for: their hash is not the one the token carries")
    if not pay.MIN_AMOUNT <= amount <= pay.MAX_AMOUNT:
        raise _no(kind, f"a bounty is from {_units(pay.MIN_AMOUNT)} to {_units(pay.MAX_AMOUNT)}; this token asks for {_units(amount)}")
    if not pay.MIN_WORK <= work <= pay.MAX_WORK:
        raise _no(kind, f"a bounty is open for a minute to {pay.MAX_WORK // 86_400} days; this token asks for {work} seconds")
    faucet = balance == pay.faucet_balance_pda(owner_id)
    job, baltok = pay.job_pda(repo_id, issue, balance), pay.baltok_pda(balance)
    got = _read(ledger, [pay.pause_pda(), balance, baltok, job, pay.faucet_mint(), pay.rate_pda(repo_id)])
    b, j = pay.read_balance(_data(got, balance)), pay.read_job(_data(got, job))
    result = dict(kind=kind, job=str(job), repo_id=repo_id, issue=issue, amount=amount, mode=mode, balance=str(balance))

    def done(sigs: list[str], made: pay.Job | None = None) -> dict:
        made = made or pay.read_job(ledger.account(job))
        if made is None:
            return {"ok": False, "kind": kind, "why": "the funding did not reach the chain"}
        return {"ok": True, **result, "sigs": sigs, "amount": made.amount, "faucet": made.faucet, "deadline": made.deadline}
    if j is not None:       # a job keeps its token's time and commenter: this very token made it, carried by another relayer
        if (j.not_before, j.funder_id, j.terms.hex(), j.mode, j.wf_sha) == (iat, actor, p[5], mode, wf_sha):
            raise _Stop({**done(_last(ledger, job), j), "already": True})
        raise _no(kind, f"this issue already has a bounty from this balance (job {job})")
    if b is not None and iat <= b.last_iat:
        raise _no(kind, "this token was used already, and a fund token works once" if iat == b.last_iat else
                  "an older fund token than the last one this balance took; comment again")
    if not terms:
        raise _no(kind, "the bounty's terms did not come with its token (the `knos-terms:` line of the funding comment)")
    until = pay.read_pause(_data(got, pay.pause_pda()))
    if now < until:
        soon = until < int(c.get("exp", 0)) + oidc.LATE - 60        # the pause ends while this token still works
        raise _no(kind, f"new funding is paused until {_when(until)}; payments and refunds go on",
                  **({"retry": True, "wait": until - now} if soon else {}))
    groups: list[Group] = []
    if faucet:
        if _data(got, pay.faucet_mint()) is None:
            raise _no(kind, "this token spends the devnet faucet's test USDC, and this cluster has no faucet")
        if amount > pay.FAUCET_CAP:
            raise _no(kind, f"the faucet gives at most {_units(pay.FAUCET_CAP)} test USDC for one bounty; open a balance for more")
        if actor == 0:
            raise _no(kind, "this token names no commenter")
        if _amount(_data(got, baltok)) < amount:        # FaucetOpen mints the token's amount; a refund may have left it there already
            last, last_iat = pay.read_rate(_data(got, pay.rate_pda(repo_id)))
            if last and iat <= last_iat:
                raise _no(kind, "an older fund token than the repository's last one; comment again")
            if now < last + pay.FUND_PERIOD:
                raise _no(kind, "the faucet serves a repository once a minute", retry=True, wait=last + pay.FUND_PERIOD - now)
            groups.append(([pay.faucet_open_ix(me, t.account, t.key, owner_id, repo_id)], _CU["faucet"]))
        mint, program = pay.faucet_mint(), pay.TOKEN
    else:
        if b is None:
            raise _no(kind, f"the balance this token names ({balance}) does not exist")
        if b.owner_id != owner_id:
            raise _no(kind, "that balance is for another GitHub owner's repositories")
        if actor == 0 or not (b.faucet or actor == b.owner_id or actor in b.spenders):
            raise _no(kind, "this commenter may not spend that balance: only its repository owner and the spenders its wallet listed can")
        if b.cap_per_job and amount > b.cap_per_job:
            raise _no(kind, f"that balance allows {_units(b.cap_per_job)} for one bounty; this token asks for {_units(amount)}")
        if _amount(_data(got, baltok)) < amount:
            raise _no(kind, f"the balance holds {_units(_amount(_data(got, baltok)))}, less than this bounty; add money to it or fund less")
        mint = b.mint
        program = (got[baltok][0] if got.get(baltok) else None) or pay.TOKEN     # a Balance's token account belongs to its mint's token program
    groups.append(([pay.fund_balance_ix(me, t.account, t.key, balance, mint, repo_id, issue, terms, program)], _CU["fund"]))
    return _Plan(t, groups, done)


def _payout(me: Pubkey, j: pay.Job, wallet: Pubkey, ix: Instruction, cu: int) -> Group:
    """A payment and the two token accounts it needs, made if they are not there (either token program)."""
    return ([pay.create_ata_ix(me, wallet, j.mint, j.token_program), pay.create_ata_ix(me, pay.FEE_OWNER, j.mint, j.token_program), ix],
            2 * _CU["ata"] + cu)


def _paid_before(ledger, repo_id: int, issue: int, payee: int, since: int) -> list[dict]:
    """What knos-pay itself logged when this payee was paid for this issue, among the last few payments on their
    record and no earlier than `since` (the signed token's own time): the token another relayer carried, after which the job
    is gone. An earlier bounty on the same issue, paid long before, is not this token's doing."""
    recent, logs = getattr(ledger, "recent", None), getattr(ledger, "logs", None)
    out = []
    try:
        for sig, when in (recent(pay.rep_pda(payee), 5) if recent and logs else []):
            if when is not None and when < since:
                continue
            for line in chain.said(logs(sig), pay.PAY_ID):
                m = re.fullmatch(rf"knos2:paid repo={repo_id} issue={issue} payee={payee} amount=(\d+) fee=(\d+) to=(\S+)", line)
                if m:
                    out.append({"job": "", "amount": int(m.group(1)) + int(m.group(2)), "fee": int(m.group(2)), "mint": "",
                                "to": m.group(3), "held_until": None, "sig": sig})
    except Exception:  # noqa: BLE001 - not finding it only means the plain refusal is given
        return []
    return out


def _plan_pay(ledger, me: Pubkey, t: _Token, now: int) -> _Plan:
    kind, c, p = "pay", t.c, t.aud.split(":")
    try:
        if len(p) != 9 or not _HEX40.fullmatch(p[5]) or not _HEX64.fullmatch(p[6]) or p[7] not in ("0", "1"):
            raise ValueError(t.aud)
        repo_id, issue, payee, mode = int(p[2]), int(p[3]), int(p[4]), int(p[7])
        ran_in, iat = _ints(c, "repository_id", "iat")
        if payee == 0 or (p[8] != "-" and not _address(p[8])):
            raise ValueError(t.aud)
    except (KeyError, ValueError, TypeError):
        raise _no(kind, "malformed audience or claims") from None
    wf_repo, wf_file, wf_sha = _workflow(c)
    if wf_file != "prove.yml" or ran_in != repo_id:
        raise _no(kind, "not from the workflow or the repository the audience names")
    result = dict(kind=kind, repo_id=repo_id, issue=issue, payee_id=payee, head=p[5])
    jobs = jobs_for(ledger, repo_id, issue)
    opened = [(a, j) for a, j in jobs if j.state == "open"]
    pinned = [(a, j) for a, j in jobs if (j.wf_repo_hash, j.wf_sha) == (wf_repo, wf_sha)]
    agreed = [(a, j) for a, j in pinned if (j.terms.hex(), j.mode) == (p[6], mode)]
    mine = [(a, j) for a, j in agreed if j.state == "open" and now <= j.deadline and iat >= j.not_before]
    if not mine:
        held = [(a, j) for a, j in agreed if j.state == "held" and j.payee_id == payee]
        if held:        # another relayer carried this token: the jobs wait for the payee to bind a wallet
            entries = [{"job": str(a), "amount": j.amount, "fee": pay.fee_of(j.amount), "mint": str(j.mint), "to": None, "held_until": j.hold_until} for a, j in held]
            raise _Stop({"ok": True, **result, "sigs": _last(ledger, held[0][0]), "already": True, "paid": entries})
        before = _paid_before(ledger, repo_id, issue, payee, iat - pay.TOKEN_AHEAD)
        if before:      # and here the payee had a wallet: the job was paid and is gone
            raise _Stop({"ok": True, **result, "sigs": list(dict.fromkeys(e.pop("sig") for e in before)), "already": True, "paid": before})
        raise _no(kind, "no bounty is in escrow for this issue (never funded, or already paid or refunded)" if not jobs else
                  "the bounty on this issue is already held for another GitHub user" if not opened else
                  "no open bounty on this issue pins this workflow at this commit" if not any(j.state == "open" for _a, j in pinned) else
                  "the open bounty on this issue has other terms than the ones this token was made for" if not any(j.state == "open" for _a, j in agreed) else
                  "the bounty's deadline has passed: it goes back to its funder" if all(now > j.deadline for _a, j in agreed if j.state == "open") else
                  "this token is older than the bounty: it was made before the funding")
    bound = pay.read_bind(_data(_read(ledger, [pay.bind_pda(payee)]), pay.bind_pda(payee)))
    wallet = pay.destination(bound, t.aud)
    if wallet == pay.auth_pda():
        raise _no(kind, "the address this token names is the escrow's own account")
    groups: list[Group] = []
    for addr, j in mine:
        ix = pay.pay_ix(me, t.account, t.key, addr, j, payee, wallet)
        groups.append(([ix], _CU["pay"]) if wallet is None else _payout(me, j, wallet, ix, _CU["pay"]))

    def done(sigs: list[str]) -> dict:
        after, paid = _read(ledger, [a for a, _j in mine]), []
        for addr, j in mine:
            left = pay.read_job(_data(after, addr))         # gone: paid. Held: no wallet was known. Still open: this token did not pay it
            if left is None or left.state == "held":
                paid.append({"job": str(addr), "amount": j.amount, "fee": pay.fee_of(j.amount), "mint": str(j.mint),
                             "to": str(wallet) if left is None and wallet is not None else None, "held_until": left.hold_until if left else None})
        return {"ok": True, **result, "sigs": sigs, "paid": paid} if paid else {"ok": False, "kind": kind, "why": "no open job on this issue accepted the token"}
    return _Plan(t, groups, done, alone=True)


def _plan_bind(ledger, me: Pubkey, t: _Token, now: int) -> _Plan:
    kind, c, p = "bind", t.c, t.aud.split(":")
    try:
        if len(p) != 3:
            raise ValueError(t.aud)
        wallet = _address(p[2])
        user, owner, iat = _ints(c, "actor_id", "repository_owner_id", "iat")
    except (KeyError, ValueError, TypeError):
        raise _no(kind, "malformed audience or claims") from None
    how = "`knos claim <address>` does it: it runs Knos's claim workflow in your own repository named knos-claim"
    if not str(c.get("job_workflow_ref", "")).startswith(CLAIM_REF) or c.get("job_workflow_sha") not in CLAIM_SHAS:
        raise _no(kind, f"a wallet is bound only by Knos's claim workflow at its pinned commit; {how}")
    if user == 0 or user != owner or not str(c.get("repository", "")).endswith("/knos-claim"):
        raise _no(kind, f"the claim must run in a repository named knos-claim that the claiming account owns; {how}")
    # by hand only: the form of "Run workflow" cannot be filled in by a link, so only a run its owner started counts
    if c.get("event_name") != "workflow_dispatch" or str(c.get("run_attempt")) != "1":
        raise _no(kind, "the claim must be started by hand by the account's owner (Run workflow, with the address typed in), and not be a re-run; "
                        "`knos claim <address>` does that")
    bound = pay.read_bind(_data(_read(ledger, [pay.bind_pda(user)]), pay.bind_pda(user)))
    held = [(a, j) for a, j in held_for(ledger, user) if now <= j.hold_until]
    result = dict(kind=kind, user_id=user, wallet=str(wallet))
    settles = [_payout(me, j, wallet, pay.settle_ix(me, addr, j, wallet), _CU["settle"]) for addr, j in held]

    def done(sigs: list[str], already: bool = False) -> dict:
        after = _read(ledger, [a for a, _j in held]) if held else {}
        settled = [{"job": str(a), "amount": j.amount, "fee": pay.fee_of(j.amount), "mint": str(j.mint)} for a, j in held if _data(after, a) is None]
        return {"ok": True, **result, "sigs": sigs, **({"already": True} if already else {}), "settled": settled}
    if bound is not None and iat <= bound.iat:
        if (bound.iat, bound.wallet) != (iat, wallet):
            raise _no(kind, "a newer claim has bound a wallet since this one; run the claim again to change it")
        if not settles:         # another relayer carried it, and nothing is left to pay
            raise _Stop(done(_last(ledger, pay.bind_pda(user)), True))
        return _Plan(t, settles, lambda sigs: done(sigs, True), token=False, alone=True)       # a Settle needs no token
    bind = ([pay.bind_ix(me, t.account, t.key, user), oidc.close_ix(me, t.tid)], _CU["bind"] + _CU["close"])
    return _Plan(t, [bind, *settles], done, alone=True, lead=True, closes=True)


def _plan_key(ledger, me: Pubkey, t: _Token, jwks: dict | None, now: int) -> _Plan:
    """A key token on the second verifier: RegisterKey for a key it does not have, Refresh for one it has."""
    kind, c, p = "key", t.c, t.aud.split(":")
    try:
        issuer, want = int(p[2]), p[3]
        if len(p) != 4 or issuer not in oidc.ISSUERS:
            raise ValueError(t.aud)
    except (ValueError, IndexError):
        raise _no(kind, "malformed audience or claims") from None
    n = next((n for _kid, n in oidc.jwks_keys(_jwks(issuer, jwks)) if oidc.key_hash(n).hex() == want), None)
    if n is None:
        raise _no(kind, "the issuer's key set has no key with that hash")
    key = oidc.key_pda(issuer, n)
    k = oidc.read_key(ledger.account(key))
    if k is not None and k.revoked:
        raise _no(kind, "the guardian revoked this key; it cannot be used again")
    try:
        where = tuple(_ints(c, "repository_owner_id", "repository_id"))
    except (KeyError, ValueError, TypeError):
        where = ()
    names = (t.issuer == oidc.GITHUB and str(c.get("job_workflow_ref", "")).startswith(ROTATE_REF) and c.get("job_workflow_sha") in ROTATE_SHAS
             and c.get("runner_environment") == "github-hosted" and where in ATTESTERS and c.get("event_name") in ("schedule", "workflow_dispatch"))

    def done(added: bool = False, refreshed: bool = False) -> Callable[[list[str]], dict]:
        return lambda sigs: {"ok": True, "kind": kind, "sigs": sigs, "key": str(key), "added": added, "refreshed": refreshed}
    params: Group = ([oidc.key_params_ix(me, issuer, n)], _CU["params"][8 * len(oidc.modulus_bytes(n))])
    if k is None:
        if not names:
            raise _no(kind, "the second verifier takes a new key only from the rotate workflow at its pinned commit, run by its "
                            "schedule or by hand in the attester's own repository")
        register = ([oidc.register_key_ix(me, issuer, n, t.account), oidc.close_ix(me, t.tid)], _CU["register"] + _CU["close"])
        return _Plan(t, [register, params], done(added=True), closes=True)
    if k.state != 1:            # registered, its parameters never sent: anyone may send them, and no token is needed
        return _Plan(t, [params], done(), token=False)
    if not names or now + oidc.KEY_TTL - k.expires_at < REFRESH_AFTER:
        raise _Stop(done()([]))  # the verifier has it, and a day of life gained is not worth a transaction
    refresh = ([oidc.refresh_ix(me, issuer, n, t.account), oidc.close_ix(me, t.tid)], _CU["refresh"] + _CU["close"])
    return _Plan(t, [refresh], done(refreshed=True), closes=True)


def _signer(ledger, me: Pubkey, t: _Token, plan: _Plan, now: int) -> None:
    """Reads the key that signed the token and the account this relayer verifies it in. The key must be one the
    verifier would use now; a genesis key the chain has not seen is registered on the way."""
    got = _read(ledger, [t.key, t.account])
    plan.have = _data(got, t.account)
    k = oidc.read_key(_data(got, t.key))
    if k is None and t.issuer == oidc.GITHUB and oidc.key_hash(t.n).hex() in GENESIS:
        plan.register = [oidc.register_key_ix(me, t.issuer, t.n), oidc.key_params_ix(me, t.issuer, t.n)]
        return
    if k is not None and k.state == 0:
        plan.register, k.state = [oidc.key_params_ix(me, t.issuer, t.n)], 1
    usable, why = oidc.key_usable(k, now)
    if not usable:
        raise _no(t.kind, "the token cannot be verified: " + why)


def _plan(ledger, payer: Keypair, jwt: str, terms: bytes | str | None, jwks: dict | None, now: float | None) -> _Plan:
    """Everything `submit` will send for this token, or the _Stop that says why it sends nothing. Reads only."""
    me = payer.pubkey()
    t = _open(jwt.strip(), me, jwks)
    if t.kind is None:
        raise _no(None, f"not an audience of the second deployment: {t.aud[:60]!r}")
    now = int(now if now is not None else ledger.now())
    if int(t.c.get("exp", 0)) + oidc.LATE <= now:
        raise _no(t.kind, "token expired")
    if t.kind == "key":
        plan = _plan_key(ledger, me, t, jwks, now)
    else:
        _github_token(t, now)
        plan = (_plan_fund(ledger, me, t, terms.encode() if isinstance(terms, str) else terms, now) if t.kind == "fund"
                else _plan_pay(ledger, me, t, now) if t.kind == "pay" else _plan_bind(ledger, me, t, now))
    if plan.token:
        _signer(ledger, me, t, plan, now)
    return plan


def precheck(ledger, payer: Keypair, jwt: str, terms: bytes | None = None, jwks: dict | None = None, now: float | None = None) -> dict | None:
    """What `submit` would answer without sending anything: None to go ahead, the refusal, or (when the chain already
    shows what the token asks for) the result with "already": True. Reads only; no fee."""
    try:
        _plan(ledger, payer, jwt, terms, jwks, now)
    except _Stop as stop:
        return stop.result
    return None


# -- sending: few transactions, few waits -------------------------------------------------------------------------------
def _write_ix(me: Pubkey, tid: bytes, total: int, off: int, chunk: bytes) -> Instruction:
    """The verifier's Write (oidc.write_ixs cuts every 880 bytes; this relay cuts where its transactions have room)."""
    return Instruction(oidc.OIDC_ID, b"\x00" + tid + total.to_bytes(2, "little") + off.to_bytes(2, "little") + chunk,
                       [AccountMeta(me, True, True), AccountMeta(oidc.token_pda(me, tid), False, True), AccountMeta(oidc.SYSTEM, False, False)])


def _fits(me: Pubkey, ixs: list[Instruction], cu: int = 0) -> bool:
    return chain.tx_size(ixs, me) <= ROOM and cu <= chain.MAX_COMPUTE_UNITS - _SPARE


def _steps(bits: int, done: int) -> list[int]:
    """Squarings for each Step still to send, from `done` of 16: the verifier client's plan, picked up where the
    token account stands."""
    out, at = [], 0
    for sq in oidc.step_plan(bits):
        at += sq
        if at > done:
            out.append(at - max(done, at - sq))
    return out


def _verification(me: Pubkey, t: _Token, have: bytes | None, register: list[Instruction]) -> tuple[list[list[list[Instruction]]], Instruction | None, int]:
    """(rounds, last Step, its compute units): the transactions that bring the token to its last Step, in rounds
    whose transactions do not depend on each other, and the Step that finishes the verification, which the caller
    sends together with what needs the verified token. An account that already holds part of this token (a run that
    was cut short) is carried on from where it stands; a verified one needs nothing (no last Step)."""
    raw, state = t.jwt.encode(), oidc.read_token(have)
    rounds: list[list[list[Instruction]]] = [[register]] if register else []
    if state is not None and state.verified:
        return rounds, None, 0
    written = have[oidc.T_JWT:] if have is not None and state is not None and len(have) == oidc.T_JWT + len(raw) else None
    done = state.done if state is not None and state.stage == 1 else 0
    if state is not None and (written is None or (state.stage == 1 and (written != raw or state.key != t.key or done >= 16))):
        rounds.append([[oidc.close_ix(me, t.tid)]])       # not this token as this relay writes it: start again
        written, done = None, 0
    steps = [oidc.step_ix(me, t.tid, t.key, sq) for sq in _steps(t.bits, done)]
    before = steps[:1] if len(steps) > 1 else []          # the last Step is the caller's; the one before it takes the token's tail along
    if done == 0:
        room = lambda *beside: 200 + ROOM - chain.tx_size([_write_ix(me, t.tid, len(raw), 0, bytes(200)), *beside], me)  # noqa: E731
        cut = len(raw) - min(len(raw), room(*before)) if before else len(raw)
        heads = [(off, raw[off:min(off + room(), cut)]) for off in range(0, cut, room())]
        lead = [[_write_ix(me, t.tid, len(raw), off, chunk)] for off, chunk in heads if written is None or written[off:off + len(chunk)] != chunk]
        if lead and register and len(rounds) == 1:
            rounds[0] += lead                              # the key's registration and the head of the token do not depend on each other
        elif lead:
            rounds.append(lead)
        if cut < len(raw) and (written is None or written[cut:] != raw[cut:]):
            before = [_write_ix(me, t.tid, len(raw), cut, raw[cut:]), *before]
    rounds += [[before]] if before else []
    rounds += [[[s]] for s in steps[1:-1]]
    fixed, per_byte = _LAST_STEP[t.bits]
    return rounds, steps[-1], fixed + per_byte * len(raw)


def _round(ledger, payer: Keypair, txs: list[list[Instruction]]) -> list[str]:
    """Transactions that do not depend on each other: sent together and waited for once, when the ledger can."""
    many = getattr(ledger, "send_all", None)
    if many is not None and len(txs) > 1:
        return list(many(txs, payer))
    return [ledger.send(ixs, payer) for ixs in txs]


def _out_of_compute(why: BaseException) -> bool:
    return any(mark in str(why) for mark in ("ProgramFailedToComplete", "Program failed to complete", "omputational budget exceeded",
                                             "exceeded CUs meter", "ComputationalBudgetExceeded"))


def _land(ledger, payer: Keypair, last: Instruction | None, last_cu: int, groups: list[Group], close: Instruction | None, alone: bool,
          lead: bool = False) -> tuple[list[str], bool]:
    """Sends the last Step, the escrow's groups and the Close in as few transactions and waits as they fit in: the
    Step shares its transaction with the groups that fit beside it, and the Close rides in the last one. Groups left
    over go in further transactions, side by side when `alone` says they depend on nothing but the first (with no
    Step to send, on nothing at all, unless `lead` says the first group comes first). Returns (signatures, whether
    the token account was closed)."""
    me = payer.pubkey()
    txs: list[Group] = []
    cur, cu = ([last], last_cu) if last is not None else ([], 0)
    for ixs, need in groups:
        if cur and not _fits(me, cur + ixs, cu + need):
            txs.append((cur, cu))
            cur, cu = [], 0
        cur, cu = cur + ixs, cu + need
    if cur:
        txs.append((cur, cu))
    first_n = 1 if last is not None or lead or not alone else 0     # the transaction with the Step (or the Bind) lands before any other
    together = alone and len(txs) - first_n > 1             # the rest land in any order, so the Close waits for them all
    closed = close is not None and bool(txs) and not together and _fits(me, txs[-1][0] + [close], txs[-1][1] + _CU["close"])
    if closed:
        txs[-1] = (txs[-1][0] + [close], txs[-1][1] + _CU["close"])
    sigs: list[str] = []
    for ixs, _cu in txs[:first_n]:
        try:
            sigs.append(ledger.send(ixs, payer))
        except Exception as why:
            if last is None or len(ixs) == 1 or not _out_of_compute(why):
                raise
            sigs.append(ledger.send([last], payer))         # the Step took more than was measured: it goes alone, the rest after it
            more, shut = _land(ledger, payer, None, 0, groups, close, alone, lead)
            return sigs + more, shut
    rest = [ixs for ixs, _cu in txs[first_n:]]
    sigs += _round(ledger, payer, rest) if together else [ledger.send(ixs, payer) for ixs in rest]
    if close is not None and not closed:
        sigs.append(ledger.send([close], payer))
    return sigs, close is not None


def _code(text: str) -> int | None:
    """The program's error number, in whatever words a ledger reported it."""
    m = re.search(r"custom program error: 0x([0-9a-fA-F]+)", text)
    if m:
        return int(m.group(1), 16)
    m = re.search(r"'Custom': (\d+)|Custom\((\d+)\)", text)
    return int(m.group(1) or m.group(2)) if m else None


_BROKE = ("found no record of a prior credit", "insufficient lamports", "InsufficientFundsForFee", "AccountNotFound")


def transient(why: BaseException) -> bool:
    """A failure that says nothing about the token: the cluster did not answer or dropped the transaction, the fee
    payer is out of SOL, or another run with this same relayer key was moving the same token account (knos-oidc
    refuses with 67 or 69, knos-pay with 84 when the account was closed under it; what would make 84 a verdict on the
    token was asked before anything was sent). The same token may succeed on the next pass."""
    if isinstance(why, (OSError, TimeoutError)):
        return True
    text = str(why)
    return _code(text) in (67, 69, 84) or any(mark in text for mark in ("Blockhash not found", "block height exceeded", "Too Many Requests",
                                                                        "Node is behind", *_BROKE))


def _failed(kind: str | None, why: BaseException) -> dict:
    """A failure as a result, in words: the program's own meaning for its error number when it gave one."""
    text = str(why)
    code = _code(text)
    if code == 90:      # the faucet's minute went to another token between the read and the transaction
        return {"ok": False, "kind": kind, "why": "the faucet serves a repository once a minute", "retry": True, "wait": pay.FUND_PERIOD}
    said = pay.ERRORS.get(code) or _VERIFIER.get(code)
    words = ("the relayer's key has no SOL to pay the fees with" if any(mark in text for mark in _BROKE)
             else f"{said} (error {code})" if said else f"{type(why).__name__}: {text[:200]}")
    return {"ok": False, "kind": kind, "why": words, **({"retry": True, "transient": True} if transient(why) else {})}


def why_failed(why: BaseException) -> str:
    """What a failed transaction of the escrow's or the verifier's means, in the words of their clients (for a wallet's
    own commands: `knos balance`, `knos fund-wallet`)."""
    return _failed(None, why)["why"]


def _carry(ledger, payer: Keypair, plan: _Plan, again: Callable[[], _Plan]) -> dict:
    """Sends what the plan holds and answers with what the chain then shows. `again` plans the same token anew: after
    a failure it tells whether the chain shows the token's work all the same (a transaction reported lost had landed)."""
    me, t, sigs = payer.pubkey(), plan.t, []
    closed = not plan.token
    try:
        last, last_cu = None, 0
        if plan.token:
            rounds, last, last_cu = _verification(me, t, plan.have, plan.register)
            for txs in rounds:
                sigs += _round(ledger, payer, txs)
        landed, shut = _land(ledger, payer, last, last_cu, plan.groups, oidc.close_ix(me, t.tid) if plan.token and not plan.closes else None, plan.alone,
                             plan.lead)
        sigs += landed
        closed = closed or shut or plan.closes
        return plan.done(sigs)
    except Exception as why:  # noqa: BLE001 - one bad token never stops the relay loop
        closed = not plan.token
        try:
            again()
        except _Stop as stop:
            if stop.result.get("ok"):
                return {k: v for k, v in {**stop.result, "sigs": list(dict.fromkeys([*sigs, *stop.result.get("sigs", [])]))}.items() if k != "already"}
        except Exception:  # noqa: BLE001, S110 - the cluster is not answering: the failure stands, and is tried again
            pass
        return _failed(t.kind, why)
    finally:
        if not closed:      # the token account has done its work, or failed to: take the rent back either way
            try:
                if ledger.account(t.account) is not None:
                    ledger.send([oidc.close_ix(me, t.tid)], payer)
            except Exception:  # noqa: BLE001, S110 - `sweep` closes it later
                pass


def _kind(jwt: str) -> str | None:
    try:
        aud = claims_of(jwt.strip())["aud"]
        return kind_of(aud if isinstance(aud, str) else aud[0])
    except Exception:  # noqa: BLE001 - not a token: `_open` says so
        return None


def _first(ledger, payer: Keypair, jwt: str, jwks: dict | None, now: float | None) -> dict:
    """A key token on the first deployment, as ever (knos.settle.relay), with the key sets this module already has."""
    docs = dict(jwks or {})
    for issuer in oidc.ISSUERS:
        try:
            docs.setdefault(issuer, _jwks(issuer, jwks))
        except Exception:  # noqa: BLE001, S110 - the first deployment's relay fetches what it needs
            pass
    return first.submit(ledger, payer, jwt, docs, now)


def submit(ledger, payer: Keypair, jwt: str, terms: bytes | None = None, jwks: dict | None = None, now: float | None = None) -> dict:
    """Verify the token and do what its audience says, on the second deployment (a key token: on both). Never raises
    for a bad token: returns {"ok": False, "kind", "why"}, with "retry": True when the same token may succeed later.
    The token's account is closed behind it, so the relayer's rent comes back whatever happened. A key token is ok
    when either deployment took it or had nothing to do; a later run of the rotate workflow brings the key again."""
    kind = _kind(jwt)
    before = _first(ledger, payer, jwt, jwks, now) if kind == "key" else None
    try:
        plan = lambda: _plan(ledger, payer, jwt, terms, jwks, now)  # noqa: E731
        result = _carry(ledger, payer, plan(), plan)
    except _Stop as stop:
        result = stop.result
    except Exception as why:  # noqa: BLE001 - the cluster or the issuer did not answer: nothing was sent
        result = _failed(kind, why)
    if before is None:
        return result
    if result["ok"]:
        return {**result, "sigs": [*before.get("sigs", []), *result["sigs"]], "first": before}
    if before.get("ok"):        # the first deployment took it; the second said why it did not
        return {"ok": True, "kind": "key", "sigs": list(before.get("sigs", [])), "key": str(before.get("key", "")), "added": False,
                "refreshed": False, "why": result["why"], "first": before}
    return result


def verify_only(ledger, payer: Keypair, jwt: str, jwks: dict | None = None, now: float | None = None) -> dict:
    """Verifies a token of any audience into its account and leaves it there, for someone else's program to read:
    {"ok": True, "kind": "verify", "sigs", "account", "payer", "exp"}. The account is the verifier's
    ["tok", payer, sha256(token)]: a consumer checks that knos-oidc owns it and reads the claims (docs/OIDC.md). It
    stays until an hour past the token's expiry, when `sweep` takes its rent back. The same reads as `submit` come
    first: a token no published key signed, or whose key the chain would refuse, costs nothing."""
    kind = "verify"
    try:
        me = payer.pubkey()
        t = _open(jwt.strip(), me, jwks)
        t.kind = kind
        now = int(now if now is not None else ledger.now())
        exp = int(t.c.get("exp", 0))
        if exp + oidc.LATE <= now:
            raise _no(kind, "token expired")
        plan = _Plan(t, [], dict)
        _signer(ledger, me, t, plan, now)
        result = dict(ok=True, kind=kind, account=str(t.account), payer=str(me), exp=exp)
        rounds, last, _cu = _verification(me, t, plan.have, plan.register)
        if last is None:
            return {**result, "sigs": [], "already": True}
        sigs: list[str] = []
        for txs in [*rounds, [[last]]]:
            sigs += _round(ledger, payer, txs)
        return {**result, "sigs": sigs}
    except _Stop as stop:
        return {**stop.result, "kind": kind}
    except Exception as why:  # noqa: BLE001
        return _failed(kind, why)


# -- what needs no token: anyone's to send ------------------------------------------------------------------------------
def _each(ledger, payer: Keypair, txs: list[list[Instruction]]) -> list[str]:
    """Sends each transaction; one that fails (someone else was first, a destination that cannot be made) does not
    stop the others."""
    sigs = []
    for ixs in txs:
        try:
            sigs.append(ledger.send(ixs, payer))
        except Exception:  # noqa: BLE001, S112 - best effort: the next pass looks again
            continue
    return sigs


def refund_due(ledger, payer: Keypair, now: int) -> list[str]:
    """Sends back every job nobody can be paid from any more: open past its deadline, or held past its hold. To the
    Balance it came from, or to the funding wallet's token account (made if it is gone). Needs ledger.program_accounts."""
    me, txs = payer.pubkey(), []
    for state in (1, 3):
        for addr, data in ledger.program_accounts(pay.PAY_ID, pay.JOB_LEN, {0: bytes([state])}):
            j = pay.read_job(data)
            if j is not None and now > (j.deadline if j.state == "open" else j.hold_until):
                home = [] if j.from_balance else [pay.create_ata_ix(me, j.refund_to, j.mint, j.token_program)]
                txs.append([*home, pay.refund_ix(me, addr, j)])
    return _each(ledger, payer, txs)


def settle_held(ledger, payer: Keypair) -> list[str]:
    """Pays every held job whose payee has bound a wallet since it was held. Needs ledger.program_accounts."""
    me, now = payer.pubkey(), int(ledger.now())
    held = [(addr, j) for addr, j in ((a, pay.read_job(d)) for a, d in ledger.program_accounts(pay.PAY_ID, pay.JOB_LEN, {0: bytes([3])}))
            if j is not None and now <= j.hold_until]
    binds = _read(ledger, [pay.bind_pda(j.payee_id) for _a, j in held])
    txs = []
    for addr, j in held:
        bound = pay.read_bind(_data(binds, pay.bind_pda(j.payee_id)))
        if bound is not None:
            txs.append(_payout(me, j, bound.wallet, pay.settle_ix(me, addr, j, bound.wallet), 0)[0])
    return _each(ledger, payer, txs)


def _exp(jwt: bytes) -> int | None:
    """The expiry an unverified token claims; None when the bytes are not (yet) a whole token."""
    try:
        return int(claims_of(jwt.decode("ascii"))["exp"])
    except Exception:  # noqa: BLE001 - half written
        return None


def sweep(ledger, payer: Keypair, now: int) -> list[str]:
    """Takes back the rent of this relayer's token accounts that nothing can use any more, on both verifiers: each
    one whose token is an hour past its expiry (a verify-only account is left for its consumer until then), verified
    or cut short; and one that never got its whole token, an hour after its last transaction. Needs
    ledger.program_accounts."""
    me, txs, seen = payer.pubkey(), [], getattr(ledger, "touched", None)
    for program in (oidc.OIDC_ID, first_oidc.OIDC_ID):
        for addr, data in ledger.program_accounts(program, None, {_T_PAYER: bytes(me)}):
            tok = oidc.read_token(data)
            if tok is None or tok.payer != me:
                continue
            exp = tok.exp if tok.verified else _exp(bytes(data[oidc.T_JWT:]))
            if exp is None and seen is not None:
                exp = seen(addr)
            if exp is not None and exp + oidc.LATE <= now:
                txs.append([oidc.close_ix(me, bytes(data[_T_ID:_T_ID + 32]), program)])
    return _each(ledger, payer, txs)


def register_missing(ledger, payer: Keypair, jwks: dict | None = None) -> list[str]:
    """What anyone can do for the verifier's keys with no token: register each of GitHub's genesis keys the chain has
    not seen, and send the parameters of a key that was registered without them."""
    me, txs = payer.pubkey(), []
    for _kid, n in oidc.jwks_keys(_jwks(oidc.GITHUB, jwks)):
        if oidc.key_hash(n).hex() in GENESIS and ledger.account(oidc.key_pda(oidc.GITHUB, n)) is None:
            txs.append([oidc.register_key_ix(me, oidc.GITHUB, n), oidc.key_params_ix(me, oidc.GITHUB, n)])
    for _addr, k, n in keys(ledger):
        if k.state == 0 and not k.revoked:
            txs.append([oidc.key_params_ix(me, k.issuer, n)])
    return _each(ledger, payer, txs)
