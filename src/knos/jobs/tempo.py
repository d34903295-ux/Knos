"""The Tempo escrow (contracts/KnosEscrow.sol) from Python: deploy, post, claim, deliver, accept, reject, release, refund.

Plain JSON-RPC and signed legacy transactions (eth_account), so the same code runs on Tempo Moderato, Tempo mainnet
(locked until the audit, capped at $500 per job in the contract itself), and a local anvil for tests and the bench.
On Tempo a job id is the sha256 of its brief, so the brief on any relay is checked against the id itself.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ARTIFACT = Path(__file__).with_name("KnosEscrow.json")
TEST_TOKEN = Path(__file__).with_name("TestToken.json")
MODERATO = "https://rpc.moderato.tempo.xyz"
PATH_USD = "0x20C0000000000000000000000000000000000000"
STATES = {0: "none", 1: "open", 2: "claimed", 3: "delivered", 4: "released", 5: "refunded"}


class TempoError(Exception):
    pass


def _sel(sig: str) -> bytes:
    from eth_utils import keccak
    return keccak(text=sig)[:4]


@dataclass
class TempoJob:
    id: bytes
    buyer: str
    worker: str | None
    amount: int
    deadline: int
    state: str
    result: bytes | None


class Chain:
    def __init__(self, url: str, timeout: float = 30.0):
        self.url, self.timeout, self._id = url, timeout, 0
        self._chain_id = None
        self._gas_price = None

    def rpc(self, method: str, params: list):
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
        for attempt in range(5):   # Moderato's public RPC sometimes answers with an empty body
            try:
                req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json",
                                                                         "User-Agent": "knos"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - configured RPC
                    got = json.loads(r.read())
                break
            except (OSError, ValueError):
                if attempt == 4:
                    raise
                time.sleep(0.3 * (attempt + 1))
        if "error" in got:
            raise TempoError(got["error"].get("message", str(got["error"])))
        return got["result"]

    @property
    def chain_id(self) -> int:
        if self._chain_id is None:
            self._chain_id = int(self.rpc("eth_chainId", []), 16)
        return self._chain_id

    def call(self, to: str, sig: str, types: list, args: list) -> bytes:
        from eth_abi import encode
        data = "0x" + (_sel(sig) + encode(types, args)).hex()
        return bytes.fromhex(self.rpc("eth_call", [{"to": to, "data": data}, "latest"])[2:])

    def send(self, key, to: str | None, data: bytes, gas: int | None = None) -> dict:
        from eth_account import Account
        from eth_utils import to_checksum_address
        if self._gas_price is None:
            self._gas_price = int(self.rpc("eth_gasPrice", []), 16)
        nonce = int(self.rpc("eth_getTransactionCount", [key.address, "pending"]), 16)
        if gas is None:   # Tempo prices new state far above Ethereum, so estimate rather than assume
            q = {"from": key.address, "data": "0x" + data.hex()}
            if to:
                q["to"] = to
            gas = int(self.rpc("eth_estimateGas", [q]), 16) * 13 // 10
        tx = {"to": to_checksum_address(to) if to else b"", "data": data, "value": 0, "gas": gas, "gasPrice": self._gas_price, "nonce": nonce,
              "chainId": self.chain_id}
        raw = Account.sign_transaction(tx, key.key).raw_transaction
        h = self.rpc("eth_sendRawTransaction", ["0x" + raw.hex()])
        end = time.monotonic() + 60
        while time.monotonic() < end:
            rec = self.rpc("eth_getTransactionReceipt", [h])
            if rec:
                if int(rec["status"], 16) != 1:
                    raise TempoError(f"reverted: {h}")
                return rec
            time.sleep(0.1)
        raise TempoError(f"{h} not mined within 60 s")

    def deploy(self, key, artifact: Path, types: list, args: list) -> str:
        from eth_abi import encode
        art = json.loads(artifact.read_text(encoding="utf-8"))
        code = bytes.fromhex(art["bytecode"].removeprefix("0x")) + encode(types, args)
        from eth_utils import to_checksum_address
        return to_checksum_address(self.send(key, None, code)["contractAddress"])

    def balance(self, token: str, who: str) -> int:
        from eth_abi import decode
        return decode(["uint256"], self.call(token, "balanceOf(address)", ["address"], [who]))[0]

    def now(self) -> int:
        return int(self.rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16)


class Escrow:
    def __init__(self, chain: Chain, address: str, token: str):
        self.chain, self.address, self.token = chain, address, token

    @classmethod
    def deploy(cls, chain: Chain, key, token: str, fee_to: str, fee_bps: int = 500, review_s: int = 86_400,
               guardian: str | None = None, cap_units: int = 0) -> "Escrow":
        addr = chain.deploy(key, ARTIFACT, ["address", "address", "uint16", "uint64", "address", "uint128"],
                            [token, fee_to, fee_bps, review_s, guardian or key.address, cap_units])
        return cls(chain, addr, token)

    def _tx(self, key, sig: str, types: list, args: list) -> dict:
        from eth_abi import encode
        return self.chain.send(key, self.address, _sel(sig) + encode(types, args))

    def post(self, buyer, job_id: bytes, amount: int, work_s: int) -> dict:
        from eth_abi import encode
        self.chain.send(buyer, self.token, _sel("approve(address,uint256)") + encode(["address", "uint256"],
                                                                                     [self.address, amount]))
        return self._tx(buyer, "post(bytes32,uint128,uint64)", ["bytes32", "uint128", "uint64"],
                        [job_id, amount, work_s])

    def claim(self, worker, job_id: bytes) -> dict:
        return self._tx(worker, "claim(bytes32)", ["bytes32"], [job_id])

    def deliver(self, worker, job_id: bytes, result: bytes) -> dict:
        return self._tx(worker, "deliver(bytes32,bytes32)", ["bytes32", "bytes32"], [job_id, result])

    def accept(self, buyer, job_id: bytes) -> dict:
        return self._tx(buyer, "accept(bytes32)", ["bytes32"], [job_id])

    def reject(self, buyer, job_id: bytes) -> dict:
        return self._tx(buyer, "reject(bytes32)", ["bytes32"], [job_id])

    def release(self, anyone, job_id: bytes) -> dict:
        return self._tx(anyone, "release(bytes32)", ["bytes32"], [job_id])

    def refund(self, buyer, job_id: bytes) -> dict:
        return self._tx(buyer, "refund(bytes32)", ["bytes32"], [job_id])

    def job(self, job_id: bytes) -> TempoJob:
        from eth_abi import decode
        b, w, amount, deadline, state, result = decode(
            ["address", "address", "uint128", "uint64", "uint8", "bytes32"],
            self.chain.call(self.address, "jobs(bytes32)", ["bytes32"], [job_id]))
        zero = "0x" + "0" * 40
        return TempoJob(job_id, b, None if w == zero else w, amount, deadline, STATES[state],
                        None if result == bytes(32) else result)
