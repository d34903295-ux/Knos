"""Where the escrow lives: one interface over a real cluster (JSON-RPC) and an in-process runtime (LiteSVM).

Everything above this (the CLI, the worker, the Actions endpoint, the bench) talks to a `Ledger`, so the same code
runs against devnet and inside a test in milliseconds.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from . import sol


@dataclass
class Ledger:
    """A cluster reached over JSON-RPC."""
    url: str
    program: Pubkey
    commitment: str = "confirmed"
    _config: dict | None = field(default=None, repr=False)

    def send(self, ixs, payer: Keypair, signers: list[Keypair] | None = None) -> str:
        from ..team import rpc
        sig = rpc.send(self.url, list(ixs), payer, [s for s in (signers or []) if s.pubkey() != payer.pubkey()],
                       confirm=False)
        rpc.wait(self.url, sig, 60.0, commitment=self.commitment)   # reads at this commitment then see the write
        return sig

    def account(self, address: Pubkey) -> bytes | None:
        from ..team import rpc
        return rpc.account_data(self.url, address, commitment=self.commitment)[1]

    def jobs(self, state: str | None = None) -> list[sol.Job]:
        """Every job account of the program (optionally only one state), straight from the chain."""
        from ..team import rpc
        filters: list[dict] = [{"dataSize": sol.JOB_LEN}]
        if state:
            code = {v: k for k, v in sol.STATES.items()}[state]
            filters.append({"memcmp": {"offset": 0, "bytes": _b58(bytes([code]))}})
        got = rpc.call(self.url, "getProgramAccounts",
                       [str(self.program), {"encoding": "base64", "commitment": self.commitment, "filters": filters}],
                       timeout=30) or []
        out = []
        for item in got:
            try:
                out.append(sol.parse_job(Pubkey.from_string(item["pubkey"]),
                                         base64.b64decode(item["account"]["data"][0])))
            except (ValueError, KeyError, IndexError):
                continue
        return out

    def blockhash(self):
        from ..team import rpc
        return rpc.latest_blockhash(self.url)

    def now(self) -> int:
        from ..team import protocol
        return protocol.chain_time(self.url)

    def config(self) -> dict:
        if self._config is None:   # set once at init; the program has no instruction that changes it
            raw = self.account(sol.config_pda(self.program))
            if raw is None:
                raise LookupError("the escrow is not initialised on this cluster")
            self._config = sol.parse_config(raw)
        return self._config


@dataclass
class LocalLedger:
    """The escrow inside LiteSVM (knos.jobs.localsvm.Escrow), behind the same interface."""
    env: object
    program: Pubkey = field(init=False)

    def __post_init__(self):
        self.program = self.env.pid

    def send(self, ixs, payer: Keypair, signers: list[Keypair] | None = None) -> str:
        everyone = {bytes(k.pubkey()): k for k in [payer, *(signers or [])]}
        if not self.env.send(list(ixs), payer, list(everyone.values())):
            raise RuntimeError("transaction failed")
        return "local"

    def account(self, address: Pubkey) -> bytes | None:
        acc = self.env.svm.get_account(address)
        return bytes(acc.data) if acc is not None else None

    def jobs(self, state: str | None = None) -> list[sol.Job]:
        out = []
        for jid in getattr(self.env, "known_jobs", []):
            got = self.env.job(jid)
            if got and (state is None or got.state == state):
                out.append(got)
        return out

    def blockhash(self):
        return self.env.svm.latest_blockhash()

    def now(self) -> int:
        return int(self.env.svm.get_clock().unix_timestamp)

    def config(self) -> dict:
        return sol.parse_config(self.account(sol.config_pda(self.program)))


def _b58(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out
