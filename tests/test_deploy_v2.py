"""scripts/deploy_v2.py, offline, against a fake ledger: the steps of scripts/deploy_v2.sh that are transactions of the two
programs each read the chain first and send only what is missing, so a run that failed half way is finished by running it
again, and a run on a finished deployment sends nothing."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from knos import chain, mainnet_check as mc
from knos.settle.v2 import oidc, pay

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_790_000_000
VAULT = oidc.IDS["upgrade_authority"]


def _script():
    spec = importlib.util.spec_from_file_location("deploy_v2_script", ROOT / "scripts" / "deploy_v2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


d = _script()


class Ledger:
    """What the cluster does with the instructions deploy_v2.py sends, as far as the steps can see it."""

    def __init__(self, fail_on: int | None = None):
        self.accounts: dict[str, bytes] = {}
        self.sent: list[str] = []
        self.fail_on = fail_on          # the n-th send raises, as a dropped connection does

    def account(self, address) -> bytes | None:
        return self.accounts.get(str(address))

    def now(self) -> int:
        return NOW

    def send(self, ixs, payer) -> str:
        assert len(ixs) == 1
        ix = ixs[0]
        if self.fail_on is not None and len(self.sent) + 1 == self.fail_on:
            self.fail_on = None
            raise chain.RpcError("the connection dropped")
        if ix.program_id == pay.PAY_ID and ix.data == b"\x0a":
            self.accounts[str(ix.accounts[1].pubkey)] = b"mint"
            what = "InitFaucet"
        elif ix.program_id == oidc.OIDC_ID and ix.data[:1] == b"\x03":
            head = bytes([0, oidc.GITHUB, 64, 255]) + bytes(4) + NOW.to_bytes(8, "little") + (NOW + oidc.KEY_TTL).to_bytes(8, "little")
            self.accounts[str(ix.accounts[1].pubkey)] = head + bytes([oidc.GENESIS | oidc.APPROVED]) + bytes(15) + bytes(8 * 64)
            what = "RegisterKey"
        elif ix.program_id == oidc.OIDC_ID and ix.data[:1] == b"\x04":
            key = str(ix.accounts[1].pubkey)
            self.accounts[key] = b"\x01" + self.accounts[key][1:]
            what = "KeyParams"
        elif ix.program_id == pay.ATA_PROGRAM:
            self.accounts[str(ix.accounts[1].pubkey)] = b"token account"
            what = "CreateATA"
        else:
            raise AssertionError(f"an instruction this deployment does not send: {ix.program_id} {ix.data[:1].hex()}")
        self.sent.append(what)
        return f"sig{len(self.sent)}"

    def deploy(self, name: str, authority: str | None, elf: bytes = b"\x7fELFcode") -> None:
        head = (3).to_bytes(4, "little") + bytes(8) + (b"\x01" + bytes(Pubkey.from_string(authority)) if authority else bytes(33))
        self.accounts[str(mc.programdata_address(pay.IDS[name]))] = head + elf + bytes(40)


def run_all(ledger, payer=None) -> list[str]:
    payer = payer or Keypair()
    said: list[str] = []
    d.init_faucet(ledger, payer, said.append)
    d.register_keys(ledger, payer, d.genesis_keys(), said.append)
    d.fee_account(ledger, payer, said.append)
    return said


def test_the_four_genesis_keys_are_the_ones_the_verifier_takes_without_an_attestation():
    keys = d.genesis_keys()
    assert len(keys) == 4 and len({n for _, n in keys}) == 4
    pinned = {h for issuer, h in d.genesis(d.PINS.read_text(encoding="utf-8")) if issuer == oidc.GITHUB}
    assert {oidc.key_hash(n).hex() for _, n in keys} == pinned
    with pytest.raises(SystemExit, match="refused: github_jwks_2026-10-02.json has no key whose sha256 is"):
        d.genesis_keys(jwks={"keys": [k for k in json.loads(d.JWKS.read_text(encoding="utf-8"))["keys"]][:2]})


def test_every_step_sends_what_is_missing_and_a_second_run_sends_nothing():
    ledger = Ledger()
    said = run_all(ledger)
    assert ledger.sent == ["InitFaucet", *["RegisterKey", "KeyParams"] * 4, "CreateATA"]
    assert said[0] == "  InitFaucet: sig1" and said[-1].startswith("  the fee owner's token account: sig")
    assert sum("ready, verifies until 2026-10-21" in line for line in said) == 4
    again = run_all(ledger)
    assert len(ledger.sent) == 10, "nothing was sent the second time"
    assert [line for line in again if "exists already" in line] == ["  the faucet mint exists already", "  the fee owner's token account exists already"]


@pytest.mark.parametrize("fail_on", range(1, 11))
def test_a_run_that_failed_anywhere_is_finished_by_running_it_again_without_repeating_anything(fail_on):
    ledger = Ledger(fail_on=fail_on)
    with pytest.raises(chain.RpcError):
        run_all(ledger)
    run_all(ledger)
    assert sorted(ledger.sent) == sorted(["InitFaucet", *["RegisterKey", "KeyParams"] * 4, "CreateATA"]), (fail_on, ledger.sent)


def test_a_cluster_that_does_not_answer_in_time_is_asked_again_and_a_programs_refusal_is_not(monkeypatch):
    monkeypatch.setattr(d.time, "sleep", lambda seconds: None)
    said: list[str] = []
    seen: list[int] = []

    def slow():
        seen.append(1)
        if len(seen) < 3:
            raise TimeoutError("timed out")
        return "done"
    assert d.retrying(slow, say=said.append) == "done" and len(seen) == 3
    assert len(said) == 2 and "try 3 of 4" in said[-1] and "timed out" in said[0]
    seen.clear()

    def refused():
        seen.append(1)
        raise chain.RpcError("custom program error: 0x59", {"err": {}, "logs": ["Program x failed: custom program error: 0x59"]})
    with pytest.raises(chain.RpcError):
        d.retrying(refused, say=said.append)
    assert len(seen) == 1
    seen.clear()

    def down():
        seen.append(1)
        raise OSError("connection refused")
    with pytest.raises(OSError):
        d.retrying(down, say=said.append)
    assert len(seen) == 4, "four tries in all, then the last failure is raised"


def test_the_summary_is_complete_only_when_everything_is_on_chain_and_the_vault_holds_both_programs():
    keys = d.genesis_keys()
    ledger = Ledger()
    ok, line = d.summary(ledger, keys)
    assert not ok and "knos_oidc " + pay.IDS["knos_oidc"] + " is not deployed" in line and "0 of GitHub's 4 genesis keys verify" in line
    run_all(ledger)
    for name in ("knos_oidc", "knos_pay"):
        ledger.deploy(name, str(Keypair().pubkey()))
    ok, line = d.summary(ledger, keys)
    assert not ok and f"NOT the upgrade vault {VAULT}" in line and "4 of GitHub's 4 genesis keys verify" in line
    for name in ("knos_oidc", "knos_pay"):
        ledger.deploy(name, VAULT)
    ok, line = d.summary(ledger, keys)
    assert ok and line.count(f"its upgrade authority is the upgrade vault {VAULT}") == 2 and "(the first expires 2026-10-21" in line
    ledger.deploy("knos_pay", None)         # made immutable: no upgrade authority is not the vault, and the summary says so
    assert d.summary(ledger, keys)[0] is False


def test_the_program_state_is_the_executable_hash_and_the_authority_as_solana_verify_and_the_loader_say():
    ledger = Ledger()
    assert d.program_state(ledger, "knos_pay") is None
    ledger.deploy("knos_pay", VAULT, elf=b"\x7fELFa program")
    assert d.program_state(ledger, "knos_pay") == (mc.elf_hash(b"\x7fELFa program"), VAULT)
    ledger.deploy("knos_pay", None, elf=b"\x7fELFa program")
    assert d.program_state(ledger, "knos_pay") == (mc.elf_hash(b"\x7fELFa program"), None)


def test_every_address_of_the_deployment_is_listed_with_what_it_is():
    found = dict((address, what) for what, address in d.addresses(d.genesis_keys()))
    ids = oidc.IDS
    for name in ("knos_oidc", "knos_pay", "upgrade_multisig", "upgrade_authority", "guardian_multisig", "guardian", "fee_owner"):
        assert ids[name] in found, name
    assert found[ids["upgrade_authority"]].startswith("upgrade vault") and found[ids["guardian"]].startswith("guardian vault")
    assert found[str(pay.faucet_mint())].startswith("test-USDC mint") and found[str(mc.programdata_address(ids["knos_pay"]))].startswith("knos_pay program data")
    assert sum(what.startswith("GitHub key") for what in found.values()) == 4 and len(found) == len(d.addresses(d.genesis_keys()))


def test_the_transactions_are_listed_oldest_first_once_each_with_every_address_they_touched(monkeypatch):
    rows = {"A": [{"signature": "s2", "slot": 20, "blockTime": 1_790_000_020, "err": None}, {"signature": "s1", "slot": 10, "blockTime": 1_790_000_010, "err": None}],
            "B": [{"signature": "s2", "slot": 20, "blockTime": 1_790_000_020, "err": None}, {"signature": "s3", "slot": 30, "blockTime": None, "err": {"InstructionError": [0, "Custom"]}}],
            "C": []}

    def call(url, method, params, timeout=10.0):
        assert method == "getSignaturesForAddress" and url == "https://rpc.example"
        return rows[params[0]]
    monkeypatch.setattr(d.chain, "call", call)
    found = [("the first", "A"), ("the second", "B"), ("the third", "C")]
    devnet = d.transactions("https://rpc.example", found, "devnet")
    assert devnet == ["2026-09-21 14:13 UTC  s1  ok  touches the first  https://explorer.solana.com/tx/s1?cluster=devnet",
                      "2026-09-21 14:13 UTC  s2  ok  touches the first, the second  https://explorer.solana.com/tx/s2?cluster=devnet",
                      "slot 30  s3  FAILED  touches the second  https://explorer.solana.com/tx/s3?cluster=devnet"]
    assert [line.split("  ")[1] for line in d.transactions("https://rpc.example", found, "local")] == ["s1", "s2", "s3"]
    assert all("explorer" not in line for line in d.transactions("https://rpc.example", found, "local"))
    assert all("/tx/" in line and "cluster" not in line for line in d.transactions("https://rpc.example", found, "mainnet-beta"))
    monkeypatch.setitem(rows, "A", []), monkeypatch.setitem(rows, "B", [])
    assert d.transactions("https://rpc.example", found) == []


def test_hash_prints_the_executable_hash_of_a_file(tmp_path, capsys):
    so = tmp_path / "p.so"
    so.write_bytes(b"\x7fELF" + b"program" + bytes(100))
    assert d.main(["hash", str(so)]) == 0
    assert capsys.readouterr().out.strip() == mc.elf_hash(b"\x7fELFprogram")


def test_the_shell_script_has_seven_steps_no_idl_and_hands_over_last():
    text = (ROOT / "scripts" / "deploy_v2.sh").read_text(encoding="utf-8")
    steps = [line for line in text.splitlines() if line.startswith("step ")]
    assert [line.split()[1] for line in steps] == ["1", "2", "3", "4", "5", "6", "7"] and "handover" in steps[-1]
    assert "idl" not in text.lower() and "Program Metadata" not in text
    assert "--skip-new-upgrade-authority-signer-check" in text and "--max-sign-attempts 60" in text and '--buffer "$buffer"' in text
    assert "mainnet-beta" in text and "knos mainnet-check" in text     # the script refuses a mainnet endpoint and says where to go
