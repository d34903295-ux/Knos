"""`knos mainnet-check`: what must hold before Knos moves real money, each gate with the evidence it was judged on.

    no upgrade authority   knos-oidc and knos-pay are immutable: their program data has no upgrade authority, so
                           nobody (Knos included) can change what verifies a token or what releases money
    verified build         the on-chain bytes hash to the build of this repository (solana-verify, in program.yml)
    security.txt           each binary embeds a security.txt
    rotate pin             the commit of the key-rotation workflow that the verifier pins is in the on-chain binary
                           and exists on GitHub
    issuer keys            every key GitHub and GitLab publish today is one the verifier accepts (a genesis constant,
                           or registered on chain by GitHub's own signature)
    program checks         the last program.yml run on main passed: Wycheproof vectors, the differential test against
                           OpenSSL, the 10,000-step fuzz, cargo-audit
    external audit         an outside audit report is published (docs/AUDIT.md). There is none today: FAILS
    mainnet                locked, by design, until every gate above passes

There is no multisig gate because there is nothing left for a multisig to control: the programs have no admin
instruction and no upgrade authority. Exit 0 only if every gate passes. All I/O goes through `Fetch`, so tests inject
fakes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from solders.pubkey import Pubkey

from .settle import oidc

LOADER = Pubkey.from_string("BPFLoaderUpgradeab1e11111111111111111111111")
SECURITY_TXT = b"=======BEGIN SECURITY.TXT V1======="
PROGRAMDATA_HEADER = 45  # u32 tag | u64 slot | u8 option | [32] authority
ROTATE_REPO = "drexthealpha/knos-oidc-rotate"
PROGRAMS = ("knos_oidc", "knos_pay")


@dataclass
class Fetch:
    """account(addr) -> (owner, data) or None; verified(name) -> {"executable_hash", "source"} or None;
    program_checks() -> (ok, detail); get(url) -> parsed JSON or None; audit() -> (ok, detail)."""

    account: Callable[[str], tuple[str, bytes] | None]
    verified: Callable[[str], dict | None]
    program_checks: Callable[[], tuple[bool, str]]
    get: Callable[[str], dict | list | None]
    audit: Callable[[], tuple[bool, str]]


def elf_hash(elf: bytes) -> str:
    """solana-verify's program hash: sha256 of the ELF with the trailing zero padding stripped."""
    return hashlib.sha256(elf.rstrip(b"\x00")).hexdigest()


def program_data(fetch: Fetch, program: str) -> tuple[bool, str | None, bytes]:
    """(deployed, upgrade authority or None, the program's bytes)."""
    pd = fetch.account(str(Pubkey.find_program_address([bytes(Pubkey.from_string(program))], LOADER)[0]))
    if not pd or pd[1][:4] != (3).to_bytes(4, "little"):
        return False, None, b""
    data = pd[1]
    authority = str(Pubkey.from_bytes(data[13:45])) if data[12] == 1 else None
    return True, authority, data[PROGRAMDATA_HEADER:]


def run(fetch: Fetch, ids: dict | None = None, env: dict | None = None) -> list[tuple[str, bool, str]]:
    env = os.environ if env is None else env
    ids = ids or oidc.IDS
    res: list[tuple[str, bool, str]] = []
    elfs = {}
    for name in PROGRAMS:
        deployed, authority, elf = program_data(fetch, ids[name])
        elfs[name] = elf
        res.append((f"{name}: no upgrade authority (immutable)", deployed and authority is None,
                    f"{ids[name]}: " + ("not deployed" if not deployed else
                                        "immutable" if authority is None else f"upgrade authority {authority}")))
        st = fetch.verified(name) or {}
        want, onchain = st.get("executable_hash"), elf_hash(elf) if elf else None
        res.append((f"{name}: on-chain bytes are this repository's verified build", bool(onchain) and want == onchain,
                    f"on-chain {onchain}, verified build {want or 'none'}" + (f" ({st['source']})" if st.get("source") else "")))
        res.append((f"{name}: security.txt in the on-chain binary", SECURITY_TXT in elf,
                    "present" if SECURITY_TXT in elf else "not found"))

    pin = str(ids.get("rotate_sha", ""))
    commit = fetch.get(f"https://api.github.com/repos/{ROTATE_REPO}/commits/{pin}") if len(pin) == 40 else None
    in_binary = len(pin) == 40 and pin.encode() in elfs["knos_oidc"]
    res.append(("rotate workflow pin is in the verifier and on GitHub",
                in_binary and isinstance(commit, dict) and commit.get("sha") == pin,
                f"{ROTATE_REPO}@{pin or 'unset'}: " + ("in the on-chain binary" if in_binary else "not in the on-chain binary")
                + ("; commit exists" if isinstance(commit, dict) and commit.get("sha") == pin else "; commit not found")))

    missing, seen = [], 0
    for issuer, url in oidc.JWKS.items():
        doc = fetch.get(url)
        if not isinstance(doc, dict):
            missing.append(f"{url} unreadable")
            continue
        for kid, n in oidc.jwks_keys(doc):
            seen += 1
            if not _known(fetch, elfs["knos_oidc"], issuer, n):
                missing.append(f"{oidc.ISSUERS[issuer]} {kid}")
    res.append(("every key the issuers publish today is accepted", seen > 0 and not missing,
                f"{seen} keys published; " + (f"not accepted: {', '.join(missing)}" if missing else "all accepted")))

    ok, detail = fetch.program_checks()
    res.append(("program checks pass (Wycheproof, differential, fuzz, cargo-audit)", ok, detail))
    ok, detail = fetch.audit()
    res.append(("external audit published", ok, detail))
    locked = env.get("KNOS_ALLOW_MAINNET") != "1"
    res.append(("mainnet: locked (by design)" if locked else "mainnet: UNLOCKED (KNOS_ALLOW_MAINNET=1)", locked,
                "the released binaries are devnet builds" if locked else "unset KNOS_ALLOW_MAINNET until the gates above pass"))
    return res


def _known(fetch: Fetch, elf: bytes, issuer: int, n: int) -> bool:
    """A genesis constant (its 32-byte hash is in the binary) or a key account on chain."""
    return oidc.key_hash(n) in elf or fetch.account(str(oidc.key_pda(issuer, n))) is not None


# ---- the real fetchers ----------------------------------------------------------------------------------------------

def _rpc(url: str) -> Callable[[str], tuple[str, bytes] | None]:
    def account(addr: str) -> tuple[str, bytes] | None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                           "params": [addr, {"encoding": "base64", "commitment": "confirmed"}]}).encode()
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - the configured cluster endpoint
            v = json.load(r)["result"]["value"]
        return (v["owner"], base64.b64decode(v["data"][0])) if v else None
    return account


def _get(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "knos", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - fixed https URLs
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


def _last_run() -> tuple[int, list[dict]]:
    runs = json.loads(subprocess.run(
        ["gh", "run", "list", "--workflow", "program.yml", "--branch", "main", "--limit", "1", "--json", "databaseId"],
        capture_output=True, text=True, timeout=30, check=True).stdout)
    rid = runs[0]["databaseId"]
    jobs = json.loads(subprocess.run(["gh", "run", "view", str(rid), "--json", "jobs"], capture_output=True, text=True,
                                     timeout=30, check=True).stdout)["jobs"]
    return rid, jobs


def _ci_verified(name: str) -> dict | None:
    """The verified build is the `<name>-verified.so` artifact (solana-verify docker build) of the last successful
    program.yml run on main; hash it the way solana-verify does. (OtterSec's status API covers mainnet only.)"""
    import tempfile
    try:
        runs = json.loads(subprocess.run(
            ["gh", "run", "list", "--workflow", "program.yml", "--branch", "main", "--status", "success", "--limit", "1",
             "--json", "databaseId"], capture_output=True, text=True, timeout=30, check=True).stdout)
        rid = runs[0]["databaseId"]
        with tempfile.TemporaryDirectory(dir=".") as d:  # relative, so a Windows gh.exe under WSL works too
            d = os.path.relpath(d)
            subprocess.run(["gh", "run", "download", str(rid), "-n", f"{name}-verified.so", "-D", d],
                           capture_output=True, timeout=120, check=True)
            elf = open(os.path.join(d, f"{name}.so"), "rb").read()
        return {"executable_hash": elf_hash(elf), "source": f"program.yml run {rid} verified-build artifact"}
    except Exception:  # noqa: BLE001
        return None


def _program_checks() -> tuple[bool, str]:
    try:
        rid, jobs = _last_run()
    except Exception as why:  # noqa: BLE001
        return False, f"gh could not read the last program.yml run ({type(why).__name__})"
    bad = [f"{j['name']}: {j.get('conclusion')}" for j in jobs if j.get("conclusion") not in ("success", "skipped")]
    return not bad, f"program.yml run {rid}: " + ("every job passed" if not bad else "; ".join(bad))


def _audit() -> tuple[bool, str]:
    for root in (Path.cwd(), Path(__file__).resolve().parents[2]):
        p = root / "docs" / "AUDIT.md"
        if p.is_file():
            return True, str(p)
    return False, "no docs/AUDIT.md: no outside audit has been done"


def live(env: dict | None = None) -> Fetch:
    env = os.environ if env is None else env
    url = env.get("KNOS_RPC") or env.get("KNOS_SOLANA_RPC") or "https://api.devnet.solana.com"
    return Fetch(account=_rpc(url), verified=_ci_verified, program_checks=_program_checks, get=_get, audit=_audit)


def main(say: Callable[[str], None] = print, fetch: Fetch | None = None, as_json: bool = False) -> int:
    got = run(fetch or live())
    passed = sum(ok for _, ok, _ in got)
    if as_json:
        say(json.dumps({"gates": [{"gate": n, "pass": ok, "evidence": d} for n, ok, d in got], "passed": passed,
                        "of": len(got)}, indent=1))
        return 0 if passed == len(got) else 1
    for name, ok, detail in got:
        say(f"{'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    say(f"{passed}/{len(got)} gates pass" + ("" if passed == len(got) else "; mainnet stays locked"))
    return 0 if passed == len(got) else 1
