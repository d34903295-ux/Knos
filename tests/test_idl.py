"""idl/knos_oidc.json and idl/knos_pay.json (Shank format, written by hand: the programs are native) say what the
Python client sends and what the programs' source defines: every instruction's discriminant, accounts with their
signer and writable flags, and arguments; every account layout; every error code; the deployed addresses."""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

from knos.settle import oidc, pay

ROOT = Path(__file__).resolve().parents[1]
IDL = {name: json.loads((ROOT / "idl" / f"{name}.json").read_text(encoding="utf-8")) for name in ("knos_oidc", "knos_pay")}
SRC = {name: (ROOT / "programs" / name / "src" / "lib.rs").read_text(encoding="utf-8") for name in IDL}
CLAIMS_RS = (ROOT / "programs" / "knos_oidc" / "src" / "claims.rs").read_text(encoding="utf-8")

K = {n: Pubkey(bytes([i + 1]) * 32) for i, n in enumerate(["payer", "funder", "funder_token", "mint", "relayer", "address", "token_account"])}
REPO, ISSUE, AUTHOR, AMOUNT = 987654321, 7, 1234567, 5_000_000
N2048, N4096 = (1 << 2047) | 0x1234567, (1 << 4095) | 0x1234567   # odd numbers of the two key sizes: only the encoding matters
TID, CHECKS, WF_SHA = bytes(range(0xC0, 0xE0)), bytes(range(32)), "c" * 40
JWT = "eyJhbGciOiJSUzI1NiJ9." + "A" * 1000 + ".c2ln"
JOB = pay.job_pda(REPO, ISSUE, K["funder"])

# builder -> (IDL instruction, the instructions it builds, how many of the IDL's optional accounts they carry,
#             the arguments the data must decode to)
CASES = {
    "knos_oidc": {
        "write_ixs": ("Write", lambda: oidc.write_ixs(K["payer"], TID, JWT), 0,
                      [{"id": TID, "total": len(JWT), "offset": off, "chunk": JWT.encode()[off:off + oidc.CHUNK]} for off in (0, oidc.CHUNK)]),
        "step_ix": ("Step", lambda: [oidc.step_ix(K["payer"], TID, oidc.key_pda(oidc.GITHUB, N2048), 8)], 0, [{"id": TID, "squarings": 8}]),
        "close_ix": ("Close", lambda: [oidc.close_ix(K["payer"], TID)], 0, [{"id": TID}]),
        "register_key_ix": ("RegisterKey", lambda: [oidc.register_key_ix(K["payer"], oidc.GITHUB, N2048), oidc.register_key_ix(K["payer"], oidc.GITLAB, N4096)], 0,
                            [{"issuer": 0, "n": oidc.modulus_bytes(N2048)}, {"issuer": 1, "n": oidc.modulus_bytes(N4096)}]),
        "register_key_ix (attested)": ("RegisterKey", lambda: [oidc.register_key_ix(K["payer"], oidc.GITHUB, N2048, K["token_account"])], 1,
                                       [{"issuer": 0, "n": oidc.modulus_bytes(N2048)}]),
        "key_params_ix": ("KeyParams", lambda: [oidc.key_params_ix(K["payer"], oidc.GITHUB, N2048)], 0,
                          [{"n0inv": oidc.key_params(N2048)[0], "r2": oidc.key_params(N2048)[1]}]),
    },
    "knos_pay": {
        "fund_ix": ("Fund", lambda: [pay.fund_ix(K["funder"], K["funder_token"], K["mint"], REPO, ISSUE, AMOUNT, "drexthealpha/Knos", WF_SHA,
                                                 mode=pay.TESTS, checks=CHECKS, work_s=7 * 86_400, review_s=86_400)], 0,
                    [{"repoId": REPO, "issue": ISSUE, "amount": AMOUNT, "work": 7 * 86_400, "review": 86_400, "mode": pay.TESTS, "checks": CHECKS,
                      "wfRepoHash": pay.wf_repo_hash("drexthealpha/Knos"), "wfSha": WF_SHA.encode()}]),
        "fund_with_token_ix": ("FundWithToken", lambda: [pay.fund_with_token_ix(K["relayer"], K["token_account"], REPO, ISSUE)], 0, [{}]),
        "pay_ix": ("Pay", lambda: [pay.pay_ix(K["relayer"], K["token_account"], JOB, AUTHOR, K["mint"], K["funder"])], 0, [{}]),
        "settle_ix": ("Settle", lambda: [pay.settle_ix(K["relayer"], JOB, AUTHOR, K["mint"], K["funder"])], 0, [{}]),
        "veto_ix": ("Veto", lambda: [pay.veto_ix(K["funder"], JOB)], 0, [{}]),
        "veto_ix (token)": ("Veto", lambda: [pay.veto_ix(K["relayer"], pay.job_pda(REPO, ISSUE), K["token_account"])], 1, [{}]),
        "refund_ix": ("Refund", lambda: [pay.refund_ix(K["relayer"], JOB, K["mint"], K["funder_token"], K["funder"])], 0, [{}]),
        "claim_ix": ("Claim", lambda: [pay.claim_ix(K["relayer"], K["token_account"], AUTHOR, K["mint"], K["address"])], 0, [{}]),
        "init_faucet_ix": ("InitFaucet", lambda: [pay.init_faucet_ix(K["payer"])], 0, [{}]),
    },
}
NOT_THE_PROGRAMS = {"create_ata_ix"}   # an instruction of the associated token account program, not of knos-pay
MODULES = {"knos_oidc": oidc, "knos_pay": pay}
FIXED = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "i64": 8, "publicKey": 32}


def size(t) -> int | None:
    """Bytes a type takes; None for `bytes`, which is raw and runs to the end."""
    if isinstance(t, dict):
        inner, count = t["array"]
        return size(inner) * count
    return FIXED.get(t)


def decode(fields: list[dict], data: bytes) -> dict:
    """`data` read as the IDL says: the fields in order, little-endian, no padding."""
    out, at = {}, 0
    for f in fields:
        n = size(f["type"])
        raw = data[at:] if n is None else data[at:at + n]
        assert len(raw) == (len(data) - at if n is None else n), f"{f['name']}: data too short"
        at += len(raw)
        out[f["name"]] = int.from_bytes(raw, "little", signed=f["type"] == "i64") if f["type"] in ("u8", "u16", "u32", "u64", "i64") else bytes(raw)
    assert at == len(data), "bytes left over"
    return out


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
        at += size(f["type"]) or 0
    return out


def layout(program: str, account: str) -> list[dict]:
    return next(a for a in IDL[program]["accounts"] if a["name"] == account)["type"]["fields"]


def constants(source: str, prefix: str, kind: str = "usize") -> dict[str, int]:
    return {m.group(1): int(m.group(2).replace("_", "")) for m in re.finditer(rf"pub const ({prefix}\w+): {kind} = ([\d_]+);", source)}


@pytest.mark.parametrize("program", list(IDL))
def test_the_address_is_the_deployed_program(program):
    ids = json.loads((ROOT / "programs" / "program_ids.json").read_text())
    assert IDL[program]["metadata"] == {"origin": "shank", "address": ids[program]}
    assert IDL[program]["name"] == program


@pytest.mark.parametrize("program", list(IDL))
def test_every_instruction_of_the_source_is_in_the_idl(program):
    """The header comment of lib.rs lists each instruction as `<tag> <Name>`; the dispatch has one arm per tag."""
    listed = {m.group(2): int(m.group(1)) for m in re.finditer(r"^//!   (\d+) (\w+) ", SRC[program], re.M)}
    arms = {int(m.group(1)) for m in re.finditer(r"^        (\d+) => \{", SRC[program].split("pub fn process")[1], re.M)}
    got = {i["name"]: i["discriminant"]["value"] for i in IDL[program]["instructions"]}
    assert got == listed and set(got.values()) == arms == set(range(len(got)))
    assert all(i["discriminant"]["type"] == "u8" for i in IDL[program]["instructions"])


@pytest.mark.parametrize("program", list(IDL))
def test_every_builder_of_the_client_is_checked(program):
    builders = {n for n, f in inspect.getmembers(MODULES[program], inspect.isfunction)
                if n.endswith(("_ix", "_ixs")) and f.__module__ == MODULES[program].__name__} - NOT_THE_PROGRAMS
    assert builders == {name.split(" ")[0] for name in CASES[program]}
    assert {c[0] for c in CASES[program].values()} == {i["name"] for i in IDL[program]["instructions"]}
    # an instruction with optional accounts is built both without and with them
    for i in IDL[program]["instructions"]:
        optional = sum(1 for a in i["accounts"] if a.get("optional"))
        assert {c[2] for c in CASES[program].values() if c[0] == i["name"]} == ({0, optional} if optional else {0})


@pytest.mark.parametrize("program,case", [(p, c) for p in CASES for c in CASES[p]])
def test_the_client_builds_what_the_idl_says(program, case):
    name, build, optional, want_args = CASES[program][case]
    idl = next(i for i in IDL[program]["instructions"] if i["name"] == name)
    ixs = build()
    assert len(ixs) == len(want_args)
    for ix, want in zip(ixs, want_args):
        data = bytes(ix.data)
        assert str(ix.program_id) == IDL[program]["metadata"]["address"]
        assert data[0] == idl["discriminant"]["value"]
        required = [a for a in idl["accounts"] if not a.get("optional")]
        accounts = required + [a for a in idl["accounts"] if a.get("optional")][:optional]
        assert len(ix.accounts) == len(accounts), [a["name"] for a in accounts]
        for meta, a in zip(ix.accounts, accounts):
            assert (meta.is_signer, meta.is_writable) == (a["isSigner"], a["isMut"]), a["name"]
        # optional accounts come last: the program reads them only after the others
        assert all(a.get("optional") for a in idl["accounts"][len(required):])
        assert decode(idl["args"], data[1:]) == want


def test_fund_data_is_the_145_bytes_the_program_requires():
    fund = next(i for i in IDL["knos_pay"]["instructions"] if i["name"] == "Fund")
    assert sum(size(a["type"]) for a in fund["args"]) == 145 and "rest.len() != 145" in SRC["knos_pay"]
    for name in ("FundWithToken", "Pay", "Settle", "Veto", "Refund", "Claim", "InitFaucet"):
        assert next(i for i in IDL["knos_pay"]["instructions"] if i["name"] == name)["args"] == []


def test_account_sizes_are_the_ones_the_programs_and_the_readers_use():
    lens = constants(SRC["knos_pay"], "")
    sizes = {a["name"]: sum(size(f["type"]) for f in a["type"]["fields"]) for a in IDL["knos_pay"]["accounts"]}
    assert sizes == {"Job": 256, "Due": 48, "Rep": 32, "Rate": 16}
    assert sizes == {"Job": lens["JOB_LEN"], "Due": lens["DUE_LEN"], "Rep": lens["REP_LEN"], "Rate": lens["RATE_LEN"]}
    # the Python readers take exactly these sizes and nothing else
    assert pay.read_job(bytes(256)) is not None and pay.read_job(bytes(255)) is None and pay.read_job(bytes(257)) is None
    due = (5).to_bytes(8, "little") + bytes(40)
    assert pay.read_due(due) == 5 and pay.read_due(due + b"\0") == 0 and pay.read_due(due[:-1]) == 0
    rep = (3).to_bytes(4, "little") + bytes(28)
    assert pay.read_rep(rep).paid_jobs == 3 and pay.read_rep(rep + b"\0").paid_jobs == 0
    # knos-oidc's accounts are a fixed header, then bytes
    t, k = constants(SRC["knos_oidc"], "T_"), constants(SRC["knos_oidc"], "K_")
    assert sum(size(f["type"]) or 0 for f in layout("knos_oidc", "Token")) == t["T_JWT"] == oidc.T_JWT == 626
    assert sum(size(f["type"]) or 0 for f in layout("knos_oidc", "Key")) == k["K_HDR"] == 8
    assert [f["name"] for f in layout("knos_oidc", "Token") if size(f["type"]) is None] == ["jwt"]


def test_field_offsets_are_the_constants_of_the_source():
    j = constants(SRC["knos_pay"], "J_")
    names = {"J_STATE": "state", "J_MODE": "mode", "J_KIND": "kind", "J_REPO": "repoId", "J_ISSUE": "issue", "J_AMOUNT": "amount",
             "J_DEADLINE": "deadline", "J_REVIEW": "review", "J_PAY_AFTER": "payAfter", "J_AUTHOR": "authorId", "J_FUNDER_ID": "funderId",
             "J_NOT_BEFORE": "notBefore", "J_VETOES": "vetoes", "J_FUNDER": "funder", "J_MINT": "mint", "J_CHECKS": "checks",
             "J_WF_REPO": "wfRepoHash", "J_WF_SHA": "wfSha"}
    assert set(names) == set(j)
    at = offsets(layout("knos_pay", "Job"))
    assert {c: at[f] for c, f in names.items()} == j
    assert set(at) - set(names.values()) == {"padding0", "padding1"}
    # the times are signed: the fields the source reads or writes as i64, and no others
    signed = {names[c] for pair in re.findall(r"i64_at\(&d, (J_\w+)\)|put_i64\(&mut d, (J_\w+),", SRC["knos_pay"]) for c in pair if c}
    assert signed == {"deadline", "review", "payAfter", "notBefore"} == {f["name"] for f in layout("knos_pay", "Job") if f["type"] == "i64"}
    assert [f["type"] for f in layout("knos_pay", "Rate")] == ["i64", "i64"]
    assert {f["name"] for f in layout("knos_oidc", "Token") if f["type"] == "i64"} == {"exp"} and "exp: i64" in SRC["knos_oidc"]
    t = constants(SRC["knos_oidc"], "T_")
    names = {"T_STAGE": "stage", "T_ISSUER": "issuer", "T_DONE": "squaringsDone", "T_LIMBS": "limbs", "T_LEN": "jwtLen", "T_POFF": "payloadOff",
             "T_PLEN": "payloadLen", "T_EXP": "exp", "T_KEY": "key", "T_PAYER": "payer", "T_ID": "id", "T_X": "x", "T_JWT": "jwt"}
    assert set(names) == set(t)
    at = offsets(layout("knos_oidc", "Token"))
    assert {c: at[f] for c, f in names.items()} == t and set(at) == set(names.values())


def test_accounts_written_as_the_idl_says_are_read_by_the_client():
    """Each field gets its own value, the bytes are laid out by the IDL, and the Python readers find every value."""
    v = {"state": 2, "mode": 1, "kind": 1, "repoId": REPO, "issue": ISSUE, "amount": AMOUNT, "deadline": 1_790_000_100, "review": 86_400,
         "payAfter": -5, "authorId": AUTHOR, "funderId": 424242, "notBefore": 1_790_000_000, "vetoes": 3, "funder": bytes(K["funder"]),
         "mint": bytes(K["mint"]), "checks": CHECKS, "wfRepoHash": pay.wf_repo_hash("drexthealpha/Knos"), "wfSha": WF_SHA.encode()}
    job = pay.read_job(encode(layout("knos_pay", "Job"), v))
    assert job == pay.Job(state="proven", mode=1, token_funded=True, repo_id=REPO, issue=ISSUE, amount=AMOUNT, deadline=1_790_000_100,
                          review=86_400, pay_after=-5, author_id=AUTHOR, funder_id=424242, not_before=1_790_000_000, vetoes=3,
                          funder=K["funder"], mint=K["mint"], checks=CHECKS, wf_repo_hash=v["wfRepoHash"], wf_sha=WF_SHA)
    assert pay.read_due(encode(layout("knos_pay", "Due"), {"amount": 4_875_000, "userId": AUTHOR, "mint": bytes(K["mint"])})) == 4_875_000
    rep = pay.read_rep(encode(layout("knos_pay", "Rep"), {"paidJobs": 9, "vetoed": 1, "totalPaid": 77_000_000, "repositories": 4, "lastRepo": REPO}))
    assert rep == pay.Record(paid_jobs=9, total_paid=77_000_000, repositories=4)
    payload = b'{"aud":"x"}'
    tok = oidc.read_token(encode(layout("knos_oidc", "Token"), {
        "stage": 2, "issuer": 1, "squaringsDone": 16, "limbs": 64, "jwtLen": 40, "payloadOff": 626 + 4, "payloadLen": len(payload), "exp": 1_790_000_300,
        "key": bytes(K["address"]), "payer": bytes(K["payer"]), "id": TID, "x": bytes(512), "jwt": b"head" + payload + bytes(25)}))
    assert (tok.verified, tok.issuer, tok.done, tok.exp, tok.key, tok.payer, tok.payload) == (True, 1, 16, 1_790_000_300, K["address"], K["payer"], payload)


def test_every_error_code_of_the_source_is_in_the_idl():
    claims = set(constants(CLAIMS_RS, "E_", "u32").values())
    assert claims == {60, 61, 62, 63}
    for program, extra in (("knos_oidc", claims), ("knos_pay", claims)):   # knos-pay reads claims with knos-oidc's reader
        errors = IDL[program]["errors"]
        codes = [e["code"] for e in errors]
        assert set(codes) == set(constants(SRC[program], "E_", "u32").values()) | extra
        assert codes == sorted(set(codes)) and len({e["name"] for e in errors}) == len(errors) and all(e["msg"] for e in errors)
    assert set(pay.ERRORS) <= {e["code"] for e in IDL["knos_pay"]["errors"]}


def test_the_idl_has_the_shape_shank_writes():
    for idl in IDL.values():
        assert set(idl) == {"version", "name", "docs", "instructions", "accounts", "errors", "metadata"}
        for i in idl["instructions"]:
            assert set(i) == {"name", "docs", "accounts", "args", "discriminant"}
            assert all({"name", "isMut", "isSigner"} <= set(a) <= {"name", "isMut", "isSigner", "desc", "optional"} for a in i["accounts"])
            assert all(set(a) == {"name", "type"} for a in i["args"])
            assert len({a["name"] for a in i["accounts"]}) == len(i["accounts"])
        for a in idl["accounts"]:
            assert a["type"]["kind"] == "struct" and all(set(f) == {"name", "type"} for f in a["type"]["fields"])
        types = [x["type"] for i in idl["instructions"] for x in i["args"]] + [f["type"] for a in idl["accounts"] for f in a["type"]["fields"]]
        assert all(t == "bytes" or size(t) for t in types)
