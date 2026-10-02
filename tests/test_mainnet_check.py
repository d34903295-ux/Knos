"""knos mainnet-check, offline: every gate driven by injected fetchers. The gates are about immutability and
provenance: there is no multisig to check, because nothing is left for one to control."""

from __future__ import annotations

from solders.pubkey import Pubkey

from knos import mainnet_check as mc
from knos.settle import oidc

IDS = {"knos_oidc": str(Pubkey.new_unique()), "knos_pay": str(Pubkey.new_unique()), "rotate_sha": "ab" * 20}
N1 = (1 << 2047) + 12345          # two "issuer keys": one a genesis constant, one registered on chain
N2 = (1 << 2047) + 67891


def jwk(n: int, kid: str) -> dict:
    import base64
    b = n.to_bytes(256, "big")
    return {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid, "e": "AQAB",
            "n": base64.urlsafe_b64encode(b).rstrip(b"=").decode()}


def elf(name: str, pin: str = IDS["rotate_sha"], genesis=(N1,)) -> bytes:
    body = b"\x7fELF" + name.encode() + b"code" * 40 + mc.SECURITY_TXT + b"name: knos"
    if name == "knos_oidc":
        body += pin.encode() + b"".join(oidc.key_hash(n) for n in genesis)
    return body + b"\x00" * 64


def world(*, authority=None, elfs=None, verified=None, registered=(N2,), commit=True, published=(N1, N2),
          checks=(True, "run 7: every job passed"), audit=(True, "docs/AUDIT.md")):
    elfs = elfs or {n: elf(n) for n in mc.PROGRAMS}
    accts = {}
    for name in mc.PROGRAMS:
        pd = Pubkey.find_program_address([bytes(Pubkey.from_string(IDS[name]))], mc.LOADER)[0]
        head = (3).to_bytes(4, "little") + bytes(8) + (b"\x01" + bytes(authority) if authority else b"\x00" + bytes(32))
        accts[str(pd)] = (str(mc.LOADER), head + elfs[name])
    for n in registered:
        accts[str(oidc.key_pda(0, n, Pubkey.from_string(IDS["knos_oidc"])))] = (IDS["knos_oidc"], b"key")

    def get(url: str):
        if "api.github.com" in url:
            return {"sha": IDS["rotate_sha"]} if commit else None
        return {"keys": [jwk(n, f"k{i}") for i, n in enumerate(published)]} if "githubusercontent" in url else {"keys": []}

    v = verified if verified is not None else {n: {"executable_hash": mc.elf_hash(elfs[n]), "source": "test"} for n in mc.PROGRAMS}
    return mc.Fetch(account=accts.get, verified=v.get, program_checks=lambda: checks, get=get, audit=lambda: audit)


def results(fetch, env=None, monkeypatch=None):
    return {name: ok for name, ok, _ in mc.run(fetch, ids=IDS, env=env or {})}


def _patch_key_pda(monkeypatch):
    real = oidc.key_pda
    monkeypatch.setattr(oidc, "key_pda", lambda issuer, n, program=None: real(issuer, n, Pubkey.from_string(IDS["knos_oidc"])))


def test_all_gates_pass_and_exit_zero(monkeypatch):
    _patch_key_pda(monkeypatch)
    got = results(world())
    assert all(got.values()), got
    assert len(got) == 11 and "mainnet: locked (by design)" in got


def test_each_gate_fails_on_its_own(monkeypatch):
    _patch_key_pda(monkeypatch)
    cases = {
        "knos_oidc: no upgrade authority (immutable)": world(authority=Pubkey.new_unique()),
        "knos_pay: on-chain bytes are this repository's verified build": world(verified={"knos_oidc": {"executable_hash": mc.elf_hash(elf("knos_oidc"))}, "knos_pay": {"executable_hash": "0" * 64}}),
        "knos_pay: security.txt in the on-chain binary": world(elfs={"knos_oidc": elf("knos_oidc"), "knos_pay": b"\x7fELF" + b"x" * 90}),
        "rotate workflow pin is in the verifier and on GitHub": world(commit=False),
        "every key the issuers publish today is accepted": world(registered=()),
        "program checks pass (Wycheproof, differential, fuzz, cargo-audit)": world(checks=(False, "fuzz: failure")),
        "external audit published": world(audit=(False, "no docs/AUDIT.md")),
    }
    for gate, fetch in cases.items():
        got = results(fetch)
        assert got[gate] is False, gate
        if "security.txt" not in gate:
            assert sum(not ok for ok in got.values()) == (2 if "upgrade authority" in gate else 1), (gate, got)


def test_an_upgrade_authority_fails_whoever_holds_it(monkeypatch):
    """No fixture of keys can pass this gate: any upgrade authority at all is a failure (the 0.3.9 gate could be
    passed by a multisig in which two Knos keys still met the threshold)."""
    _patch_key_pda(monkeypatch)
    for holder in (Pubkey.new_unique(), Pubkey.from_string("4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo")):
        got = {n: (ok, d) for n, ok, d in mc.run(world(authority=holder), ids=IDS, env={})}
        assert got["knos_pay: no upgrade authority (immutable)"][0] is False
        assert str(holder) in got["knos_pay: no upgrade authority (immutable)"][1]


def test_the_rotate_pin_must_be_in_the_on_chain_binary(monkeypatch):
    _patch_key_pda(monkeypatch)
    other = world(elfs={"knos_oidc": elf("knos_oidc", pin="cd" * 20), "knos_pay": elf("knos_pay")})
    assert results(other)["rotate workflow pin is in the verifier and on GitHub"] is False


def test_unlocking_mainnet_fails_its_gate_and_main_reports(monkeypatch):
    _patch_key_pda(monkeypatch)
    assert results(world(), env={"KNOS_ALLOW_MAINNET": "1"})["mainnet: UNLOCKED (KNOS_ALLOW_MAINNET=1)"] is False
    monkeypatch.setattr(mc.oidc, "IDS", IDS)
    lines: list[str] = []
    assert mc.main(lines.append, fetch=world(audit=(False, "no docs/AUDIT.md: no outside audit has been done"))) == 1
    assert lines[-1] == "10/11 gates pass; mainnet stays locked"
    assert any(x.startswith("FAIL  external audit published") for x in lines)
    out: list[str] = []
    assert mc.main(out.append, fetch=world(), as_json=True) == 0
    import json
    assert json.loads(out[0])["passed"] == 11


def test_nothing_deployed_fails_plainly():
    f = mc.Fetch(account=lambda a: None, verified=lambda n: None, program_checks=lambda: (False, "none"),
                 get=lambda u: None, audit=lambda: (False, "none"))
    got = {n: (ok, d) for n, ok, d in mc.run(f, ids=IDS, env={})}
    assert got["knos_oidc: no upgrade authority (immutable)"] == (False, f"{IDS['knos_oidc']}: not deployed")
    assert sum(ok for ok, _ in got.values()) == 1          # only "mainnet: locked"
