"""idl/knos_pay_v2.json (Shank format, written by hand: the program is native) against the second escrow's source and
client, beyond what tests/test_idl.py checks for every program (the address, the discriminants, what each builder of
the client sends and where each account is): every instruction's accounts and arguments as the header of
programs-v2/knos_pay/src/lib.rs lists them, the number of accounts each handler takes, every account layout against
the constants of state.rs and against what the client's readers read, the error codes and bounds, the pins, and the
audiences."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

from knos.settle.v2 import pay

ROOT = Path(__file__).resolve().parents[1]
IDL = json.loads((ROOT / "idl" / "knos_pay_v2.json").read_text(encoding="utf-8"))
SRC = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "programs-v2" / "knos_pay" / "src").glob("*.rs")}
LIB, STATE = SRC["lib.rs"], SRC["state.rs"]
CLAIMS_RS = (ROOT / "programs-v2" / "knos_oidc" / "src" / "claims.rs").read_text(encoding="utf-8")
IDS = json.loads((ROOT / "programs-v2" / "program_ids.json").read_text())

K = {n: Pubkey(bytes([i + 1]) * 32) for i, n in enumerate(["authority", "funder", "mint", "relayer", "wallet"])}
REPO, ISSUE, PAYEE, OWNER, AMOUNT = 987654321, 7, 1234567, 424242, 5_000_000
TERMS = pay.terms_json({"checks": [{"app": 15368, "name": "test"}], "mode": "merge", "v": 1})
WF_SHA = "c" * 40
BAL = pay.balance_pda(OWNER, K["authority"], K["mint"])
FIXED = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "i64": 8, "publicKey": 32}


def size(t) -> int | None:
    """Bytes a type takes; None for `bytes`, which is raw and runs to the end."""
    if isinstance(t, dict):
        inner, count = t["array"]
        return size(inner) * count
    return FIXED.get(t)


def encode(fields: list[dict], values: dict) -> bytes:
    out = b""
    for f in fields:
        v, n = values.get(f["name"], 0), size(f["type"])
        if isinstance(v, int):
            v = v.to_bytes(n, "little", signed=f["type"] == "i64")
        assert n is None or len(v) == n, f["name"]
        out += bytes(v)
    return out


def offsets(fields: list[dict]) -> dict[str, int]:
    out, at = {}, 0
    for f in fields:
        out[f["name"]] = at
        at += size(f["type"])
    return out


def layout(account: str) -> list[dict]:
    return next(a for a in IDL["accounts"] if a["name"] == account)["type"]["fields"]


def constants(source: str, prefix: str, kind: str = "usize") -> dict[str, int]:
    return {m.group(1): int(m.group(2).replace("_", "")) for m in re.finditer(rf"pub const ({prefix}\w+): {kind} = ([\d_]+);", source)}


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return {"system": "systemProgram"}.get(name, head + "".join(w.capitalize() for w in rest))


def u64s(values) -> bytes:
    return b"".join(v.to_bytes(8, "little") for v in values)


def a_job(**over) -> pay.Job:
    """A job as the client reads it from bytes laid out by the IDL."""
    v = {"state": 1, "kind": 1, "tokenProgram": 0, "repoId": REPO, "issue": ISSUE, "amount": AMOUNT, "ownerId": OWNER, "payeeId": PAYEE,
         "source": bytes(BAL), "refundTo": bytes(pay.baltok_pda(BAL)), "rentTo": bytes(K["relayer"]), "mint": bytes(K["mint"]), "wfSha": WF_SHA.encode()}
    v.update(over)
    return pay.read_job(encode(layout("Job"), v))


FROM_BALANCE = a_job()
FROM_WALLET = a_job(kind=0, tokenProgram=1, ownerId=0, source=bytes(K["funder"]), refundTo=bytes(K["funder"]), rentTo=bytes(K["funder"]))
T22 = pay.TOKEN_2022


def header() -> dict[str, dict]:
    """The instructions as the header of lib.rs lists them: `<tag> <Name>  <accounts in order>`, then an optional
    `data:` line with the arguments."""
    out = {}
    lines = LIB.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^//!   (\d+) (\w+) +(\S.*)$", line)
        if not m:
            continue
        accounts = []
        for word in re.split(r" {2,}", m.group(3))[0].split():
            name, flags = re.match(r"^(\w+)(?:\(([sw,]+)\))?$", word).groups()
            accounts.append({"name": camel(name), "isSigner": "s" in (flags or ""), "isMut": "w" in (flags or "")})
        data = re.match(r"^//! +data: (.+)$", lines[i + 1])
        args = []
        for arg in (data.group(1).split(", ") if data else []):
            _name, kind = arg.split(" ", 1)
            array = re.match(r"^\[(\w+); (\d+)\]$", kind)
            args.append({"array": [array.group(1), int(array.group(2))]} if array else kind)
        out[m.group(2)] = {"tag": int(m.group(1)), "accounts": accounts, "args": args}
    return out


def test_the_address_and_the_pins_are_the_second_deployments():
    assert IDL["metadata"] == {"origin": "shank", "address": IDS["knos_pay"]} and IDL["name"] == "knos_pay_v2"
    assert str(pay.PAY_ID) == IDS["knos_pay"] and str(pay.OIDC_ID) == IDS["knos_oidc"]
    assert pay.IDS == IDS                                    # the client's copy of the pinned values is the programs' file
    pins = dict(re.findall(r'pub const (\w+): Pubkey = pubkey!\("(\w+)"\);', LIB))
    assert pins == {"OIDC_ID": IDS["knos_oidc"], "FEE_OWNER": IDS["fee_owner"], "GUARDIAN": IDS["guardian"]}
    assert f'pub const CLAIM_SHA: &[u8; 40] = b"{IDS["claim_sha"]}";' in LIB
    assert (str(pay.FEE_OWNER), str(pay.GUARDIAN)) == (IDS["fee_owner"], IDS["guardian"])
    tokens = dict(re.findall(r'pub const (\w+): Pubkey = pubkey!\("(\w+)"\);', SRC["token.rs"]))
    assert tokens == {"TOKEN": str(pay.TOKEN), "TOKEN_2022": str(pay.TOKEN_2022)}


def test_every_instruction_of_the_source_is_in_the_idl_with_its_accounts_and_arguments():
    """The header comment of lib.rs is the program's own account of itself; the dispatch has one arm per tag."""
    listed = header()
    arms = {int(m.group(1)) for m in re.finditer(r"^        (\d+) => \w+::\w+\(", LIB.split("pub fn process")[1], re.M)}
    got = {i["name"]: i["discriminant"]["value"] for i in IDL["instructions"]}
    assert got == {name: h["tag"] for name, h in listed.items()} and set(got.values()) == arms == set(range(len(got)))
    assert all(i["discriminant"]["type"] == "u8" for i in IDL["instructions"])
    for i in IDL["instructions"]:
        h = listed[i["name"]]
        assert [{k: a[k] for k in ("name", "isSigner", "isMut")} for a in i["accounts"]] == h["accounts"], i["name"]
        assert [a["type"] for a in i["args"]] == h["args"], i["name"]
        # the handler takes exactly that many accounts
        fn = re.search(rf"^        {h['tag']} => (\w+)::(\w+)\(", LIB, re.M)
        body = SRC[fn.group(1) + ".rs"].split(f"pub fn {fn.group(2)}(")[1]
        taken = re.search(r"let \[([^\]]+)\] = take\(accounts\)\?;", body).group(1)
        assert len(taken.split(",")) == len(i["accounts"]), i["name"]


def test_account_sizes_and_field_offsets_are_the_constants_of_the_source():
    lens = constants(STATE, "")
    sizes = {a["name"]: sum(size(f["type"]) for f in a["type"]["fields"]) for a in IDL["accounts"]}
    assert sizes == {"Balance": 160, "Job": 320, "Bind": 56, "Rep": 64, "Pair": 1, "Pause": 8, "Rate": 16}
    assert sizes == {"Balance": lens["BALANCE_LEN"], "Job": lens["JOB_LEN"], "Bind": lens["BIND_LEN"], "Rep": lens["REP_LEN"], "Pair": lens["PAIR_LEN"],
                     "Pause": lens["PAUSE_LEN"], "Rate": lens["RATE_LEN"]}
    assert {name for name in lens if name.endswith("_LEN")} == {"BALANCE_LEN", "JOB_LEN", "BIND_LEN", "REP_LEN", "PAIR_LEN", "PAUSE_LEN", "RATE_LEN"}
    assert (pay.JOB_LEN, pay.BALANCE_LEN, pay.BIND_LEN, pay.REP_LEN) == (320, 160, 56, 64)
    names = {
        "Job": {"J_STATE": "state", "J_MODE": "mode", "J_KIND": "kind", "J_BUMP": "bump", "J_TOKEN_PROGRAM": "tokenProgram", "J_FAUCET": "faucet",
                "J_REPO": "repoId", "J_ISSUE": "issue", "J_AMOUNT": "amount", "J_DEADLINE": "deadline", "J_HOLD_UNTIL": "holdUntil", "J_PAYEE": "payeeId",
                "J_FUNDER_ID": "funderId", "J_NOT_BEFORE": "notBefore", "J_OWNER_ID": "ownerId", "J_SOURCE": "source", "J_REFUND_TO": "refundTo",
                "J_RENT_TO": "rentTo", "J_MINT": "mint", "J_TERMS": "terms", "J_WF_REPO": "wfRepoHash", "J_WF_SHA": "wfSha"},
        "Balance": {"B_VERSION": "version", "B_BUMP": "bump", "B_FAUCET": "faucet", "B_OWNER_ID": "ownerId", "B_AUTHORITY": "authority", "B_MINT": "mint",
                    "B_CAP": "capPerJob", "B_LAST_IAT": "lastIat", "B_SPENDERS": "spenders", "B_SPENT": "spent"},
        "Bind": {"BD_VERSION": "version", "BD_BUMP": "bump", "BD_USER": "userId", "BD_WALLET": "wallet", "BD_IAT": "iat"},
        "Rep": {"R_PAID": "paid", "R_FUNDERS": "funders", "R_TOTAL": "total", "R_TEST_PAID": "testPaid", "R_SELF_PAID": "selfPaid",
                "R_TEST_TOTAL": "testTotal", "R_FIRST": "first", "R_LAST": "last"},
    }
    prefix = {"Job": "J_", "Balance": "B_", "Bind": "BD_", "Rep": "R_"}
    for account, fields in names.items():
        consts = {c: v for c, v in constants(STATE, prefix[account]).items() if account != "Balance" or not c.startswith("BD_")}
        assert set(fields) == set(consts), account
        at = offsets(layout(account))
        assert {c: at[f] for c, f in fields.items()} == consts, account
        assert all(f.startswith("padding") for f in set(at) - set(fields.values())), account
    # the times are signed: exactly the fields the source reads or writes as i64
    signed = {c for src in SRC.values() for pair in re.findall(r"i64_at\(&d, ([A-Z]\w+)\)|put_i64\(&mut d, ([A-Z]\w+),", src) for c in pair if c}
    assert signed == {"J_DEADLINE", "J_HOLD_UNTIL", "J_NOT_BEFORE", "B_LAST_IAT", "BD_IAT", "R_FIRST", "R_LAST"}
    for account, fields in names.items():
        assert {f["name"] for f in layout(account) if f["type"] == "i64"} == {fields[c] for c in signed if c in fields}, account
    for account in ("Pause", "Rate"):
        assert all(f["type"] == "i64" for f in layout(account))


def test_accounts_written_as_the_idl_says_are_read_by_the_client():
    """Each field gets its own value, the bytes are laid out by the IDL, and the Python readers find every value."""
    v = {"state": 3, "mode": 1, "kind": 1, "bump": 254, "tokenProgram": 1, "faucet": 1, "repoId": REPO, "issue": ISSUE, "amount": AMOUNT,
         "deadline": 1_790_000_100, "holdUntil": -5, "payeeId": PAYEE, "funderId": 555000, "notBefore": 1_790_000_000, "ownerId": OWNER,
         "source": bytes(BAL), "refundTo": bytes(K["wallet"]), "rentTo": bytes(K["relayer"]), "mint": bytes(K["mint"]), "terms": bytes(range(32)),
         "wfRepoHash": pay.wf_repo_hash("drexthealpha/Knos"), "wfSha": WF_SHA.encode()}
    assert pay.read_job(encode(layout("Job"), v)) == pay.Job(
        state="held", mode=1, from_balance=True, token_program=T22, faucet=True, repo_id=REPO, issue=ISSUE, amount=AMOUNT, deadline=1_790_000_100,
        hold_until=-5, payee_id=PAYEE, funder_id=555000, not_before=1_790_000_000, owner_id=OWNER, source=BAL, refund_to=K["wallet"],
        rent_to=K["relayer"], mint=K["mint"], terms=bytes(range(32)), wf_repo_hash=v["wfRepoHash"], wf_sha=WF_SHA)
    assert FROM_BALANCE.funder == OWNER and FROM_WALLET.funder == K["funder"] and not FROM_WALLET.from_balance and FROM_BALANCE.state == "open"
    b = {"version": 1, "bump": 253, "faucet": 1, "ownerId": OWNER, "authority": bytes(K["authority"]), "mint": bytes(K["mint"]), "capPerJob": 77,
         "lastIat": -3, "spenders": u64s([5, 0, 6, 0]), "spent": 99}
    assert pay.read_balance(encode(layout("Balance"), b)) == pay.Balance(faucet=True, owner_id=OWNER, authority=K["authority"], mint=K["mint"],
                                                                         cap_per_job=77, last_iat=-3, spenders=(5, 6), spent=99)
    assert pay.read_bind(encode(layout("Bind"), {"version": 1, "bump": 2, "userId": PAYEE, "wallet": bytes(K["wallet"]), "iat": 1_790_000_007})) == \
        pay.Bind(user_id=PAYEE, wallet=K["wallet"], iat=1_790_000_007)
    r = {"paid": 9, "funders": 4, "total": 77_000_000, "testPaid": 3, "selfPaid": 2, "testTotal": 5_000_000, "first": 1_790_000_001, "last": 1_790_000_002}
    assert pay.read_rep(encode(layout("Rep"), r)) == pay.Record(paid=9, funders=4, total=77_000_000, test_paid=3, self_paid=2, test_total=5_000_000,
                                                               first=1_790_000_001, last=1_790_000_002)
    assert pay.read_pause(encode(layout("Pause"), {"until": 1_790_000_300})) == 1_790_000_300 and pay.read_pause(None) == 0
    assert pay.read_rate(encode(layout("Rate"), {"lastUse": 1_790_000_500, "lastIat": 1_790_000_499})) == (1_790_000_500, 1_790_000_499)
    assert pay.read_rate(None) == pay.read_rate(bytes(8)) == (0, 0)
    # the readers take exactly these sizes, and a version they know
    for read, n in ((pay.read_job, 320), (pay.read_balance, 160), (pay.read_bind, 56)):
        good = bytes([1]) + bytes(n - 1)
        assert read(good) is not None and read(good + b"\0") is None and read(good[:-1]) is None and read(None) is None
    assert pay.read_balance(bytes(160)) is None and pay.read_bind(bytes(56)) is None
    assert pay.read_rep(bytes(65)) == pay.read_rep(None) == pay.Record(0, 0, 0, 0, 0, 0, 0, 0) and pay.read_pause(bytes(9)) == 0


def test_every_error_code_and_bound_of_the_source_is_the_clients():
    claims = set(constants(CLAIMS_RS, "E_", "u32").values())
    assert claims == {60, 61, 62, 63}
    # the escrow's own codes are 80..99; it passes on the claim reader's and, for a token whose key is no longer usable,
    # the three of knos-oidc's key rule. The client has plain words for every code that is not the claim reader's.
    stale_key = {76, 77, 78}
    own = set(constants(LIB, "E_", "u32").values())
    assert {e["code"] for e in IDL["errors"]} == own | claims | stale_key and own == set(range(80, 100))
    assert set(pay.ERRORS) == own | stale_key and all(text and text[0].islower() and not text.endswith(".") for text in pay.ERRORS.values())
    value = lambda name: eval(re.search(rf"pub const {name}: \w+ = ([\d_ *]+);", LIB).group(1).replace("_", ""), {})  # noqa: E731, S307 - digits and * only
    for name in ("FEE_BPS", "FEE_MIN", "MIN_AMOUNT", "MAX_AMOUNT", "MIN_WORK", "MAX_WORK", "HOLD", "FAUCET_CAP", "FUND_PERIOD", "CLOCK_SLACK", "PAUSE_MAX", "MAX_TERMS",
                 "TOKEN_AHEAD", "TOKEN_LIFE"):
        assert value(name) == getattr(pay, name), name
    for amount in (0, 1, 49_999, 50_000, 1_000_000, 1_999_999, 2_000_000, 5_000_000, 123_456_789, 500_000_000):
        assert pay.fee_of(amount) == min(max(amount // 10_000 * 250 + amount % 10_000 * 250 // 10_000, 50_000), amount)


def test_audiences_and_terms_are_what_the_program_parses():
    address = K["wallet"]
    funding = pay.fund_audience(7, AMOUNT, pay.TESTS, pay.terms_hash(TERMS), BAL, 3600)
    assert funding == f"knos2:fund:7:5000000:1:{pay.terms_hash(TERMS).hex()}:3600:{BAL}" and pay.named_balance(funding) == BAL
    assert pay.fund_audience(7, AMOUNT, pay.MERGE, bytes(32), BAL).split(":")[6] == str(14 * 86_400)        # two weeks of work unless said
    assert pay.pay_audience(REPO, 7, PAYEE, "a" * 40, pay.terms_hash(TERMS), 0, address) == f"knos2:pay:{REPO}:7:{PAYEE}:{'a' * 40}:{pay.terms_hash(TERMS).hex()}:0:{address}"
    assert pay.pay_audience(REPO, 7, PAYEE, "a" * 40, pay.terms_hash(TERMS), 1).endswith(":1:-")
    assert pay.bind_audience(address) == f"knos2:bind:{address}"
    # the part counts the program splits on
    for audience, parts, reader in ((funding, 8, "fund_aud"), (pay.pay_audience(REPO, 7, PAYEE, "a" * 40, bytes(32), 0), 9, "pay_aud"),
                                    (pay.bind_audience(address), 3, "bind_aud")):
        assert len(audience.split(":")) == parts and f"parts::<{parts}>(aud)" in SRC["gh.rs"].split(f"pub fn {reader}(")[1].split("\n}")[0]
    # canonical terms: sorted keys, no spaces, ASCII; the hash is sha256 of exactly those bytes
    assert pay.terms_json({"v": 1, "checks": [{"name": "tést", "app": 0}], "accept": ""}) == b'{"accept":"","checks":[{"app":0,"name":"t\\u00e9st"}],"v":1}'
    assert pay.terms_hash(b"{}").hex() == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    with pytest.raises(ValueError):
        pay.terms_json({"paths": ["x" * 600]})
    # where a proof pays: the bound wallet, else the address in the audience, else nowhere yet
    bound = pay.Bind(user_id=PAYEE, wallet=K["funder"], iat=1)
    with_address, without = pay.pay_audience(REPO, 7, PAYEE, "a" * 40, bytes(32), 0, address), pay.pay_audience(REPO, 7, PAYEE, "a" * 40, bytes(32), 0)
    assert [pay.destination(b, a) for b in (bound, None) for a in (with_address, without)] == [K["funder"], K["funder"], address, None]
    assert pay.funder_key(K["funder"]) == bytes(K["funder"]) and pay.funder_key(OWNER).hex() == __import__("hashlib").sha256(b"gh" + OWNER.to_bytes(8, "little")).hexdigest()


def test_raw_bytes_come_last_and_the_idl_says_how_the_program_can_change():
    for i in IDL["instructions"]:
        assert [a["type"] for a in i["args"]].count("bytes") <= 1 and all(a["type"] != "bytes" for a in i["args"][:-1])
    text = json.dumps(IDL)
    assert "until an outside review; then made immutable" in text
    assert "immutable" not in text.replace("until an outside review; then made immutable", "")
