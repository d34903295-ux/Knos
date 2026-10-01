"""The Solana escrow program (`programs/knos_escrow`), from Python: instruction builders, addresses and the job layout.

Layouts are the program's own (programs/knos_escrow/src/lib.rs):

    config PDA ["config"]: admin(32) mint(32) fee_token(32) fee_bps(u16) bump(u8)
    vault authority PDA ["vault"]: owns the one vault token account per mint
    job PDA ["job", id(32)]: state(u8) buyer(32) worker(32) amount(u64) deadline(i64) review(i64) brief(32) result(32)

    0 Init     admin(s,w) config(w) fee_token system                     data: fee_bps u16
    1 Post     buyer(s,w) job(w) buyer_token(w) vault_token(w) config token system
               data: id[32] amount u64 work i64 review i64 brief[32]
    2 Claim    worker(s) job(w)
    3 Deliver  worker(s) job(w)                                          data: result[32]
    4 Accept   buyer(s) job(w) vault_token(w) vault_auth worker_token(w) fee_token(w) config token
    5 Release  anyone(s), same accounts as Accept, after the review window
    6 Reject   buyer(s) job(w) vault_token(w) vault_auth buyer_token(w) token   (inside the review window)
    7 Refund   same accounts as Reject                                    (open or claimed, past the work deadline)
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
SYSTEM = Pubkey.from_string("11111111111111111111111111111111")
# The program deployed on devnet (overridable for a local validator or LiteSVM).
DEVNET_PROGRAM = os.environ.get("KNOS_ESCROW_PROGRAM", "GwmbMFvyHHwHug5em9dv26oXz2zTgXKGsNdrBxPayRPq")

STATES = {0: "none", 1: "open", 2: "claimed", 3: "delivered", 4: "released", 5: "refunded"}
JOB_LEN = 153


def program_id(pid: str | Pubkey | None = None) -> Pubkey:
    if isinstance(pid, Pubkey):
        return pid
    got = pid or DEVNET_PROGRAM or os.environ.get("KNOS_ESCROW_PROGRAM", "")
    if not got:
        raise LookupError("no escrow program id: set KNOS_ESCROW_PROGRAM or pass one")
    return Pubkey.from_string(got)


def config_pda(pid: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"config"], pid)[0]


def vault_authority(pid: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"vault"], pid)[0]


def job_pda(pid: Pubkey, job_id: bytes) -> Pubkey:
    return Pubkey.find_program_address([b"job", job_id], pid)[0]


def _m(k: Pubkey, s: bool, w: bool) -> AccountMeta:
    return AccountMeta(k, is_signer=s, is_writable=w)


def init(pid: Pubkey, admin: Pubkey, fee_token: Pubkey, fee_bps: int = 500) -> Instruction:
    return Instruction(pid, bytes([0]) + struct.pack("<H", fee_bps),
                       [_m(admin, True, True), _m(config_pda(pid), False, True), _m(fee_token, False, False),
                        _m(SYSTEM, False, False)])


def post(pid: Pubkey, buyer: Pubkey, job_id: bytes, amount: int, work_s: int, review_s: int, brief_hash: bytes,
         buyer_token: Pubkey, vault_token: Pubkey) -> Instruction:
    if len(job_id) != 32 or len(brief_hash) != 32:
        raise ValueError("job id and brief hash are 32 bytes")
    data = bytes([1]) + job_id + struct.pack("<Qqq", amount, work_s, review_s) + brief_hash
    return Instruction(pid, data, [_m(buyer, True, True), _m(job_pda(pid, job_id), False, True),
                                   _m(buyer_token, False, True), _m(vault_token, False, True),
                                   _m(config_pda(pid), False, False), _m(TOKEN, False, False),
                                   _m(SYSTEM, False, False)])


def claim(pid: Pubkey, worker: Pubkey, job_id: bytes) -> Instruction:
    return Instruction(pid, bytes([2]), [_m(worker, True, False), _m(job_pda(pid, job_id), False, True)])


def deliver(pid: Pubkey, worker: Pubkey, job_id: bytes, result_hash: bytes) -> Instruction:
    if len(result_hash) != 32:
        raise ValueError("result hash is 32 bytes")
    return Instruction(pid, bytes([3]) + result_hash, [_m(worker, True, False), _m(job_pda(pid, job_id), False, True)])


def settle(pid: Pubkey, signer: Pubkey, job_id: bytes, vault_token: Pubkey, worker_token: Pubkey, fee_token: Pubkey,
           release: bool = False) -> Instruction:
    """Accept (the buyer) or, with release=True, release by anyone after the review window: worker paid, fee taken."""
    return Instruction(pid, bytes([5 if release else 4]),
                       [_m(signer, True, False), _m(job_pda(pid, job_id), False, True), _m(vault_token, False, True),
                        _m(vault_authority(pid), False, False), _m(worker_token, False, True),
                        _m(fee_token, False, True), _m(config_pda(pid), False, False), _m(TOKEN, False, False)])


def accept(pid, buyer, job_id, vault_token, worker_token, fee_token) -> Instruction:
    return settle(pid, buyer, job_id, vault_token, worker_token, fee_token)


def release(pid, anyone, job_id, vault_token, worker_token, fee_token) -> Instruction:
    return settle(pid, anyone, job_id, vault_token, worker_token, fee_token, release=True)


def _refundish(tag: int, pid: Pubkey, buyer: Pubkey, job_id: bytes, vault_token: Pubkey,
               buyer_token: Pubkey) -> Instruction:
    return Instruction(pid, bytes([tag]), [_m(buyer, True, False), _m(job_pda(pid, job_id), False, True),
                                           _m(vault_token, False, True), _m(vault_authority(pid), False, False),
                                           _m(buyer_token, False, True), _m(TOKEN, False, False)])


def reject(pid, buyer, job_id, vault_token, buyer_token) -> Instruction:
    return _refundish(6, pid, buyer, job_id, vault_token, buyer_token)


def refund(pid, buyer, job_id, vault_token, buyer_token) -> Instruction:
    return _refundish(7, pid, buyer, job_id, vault_token, buyer_token)


@dataclass(frozen=True)
class Job:
    address: Pubkey
    state: str
    buyer: Pubkey
    worker: Pubkey | None
    amount: int
    deadline: int
    review: int
    brief: bytes
    result: bytes | None


def parse_job(address: Pubkey, raw: bytes) -> Job:
    if len(raw) < JOB_LEN:
        raise ValueError("not a knos_escrow job account")
    state, = struct.unpack_from("<B", raw, 0)
    amount, deadline, review = struct.unpack_from("<Qqq", raw, 65)
    worker = Pubkey.from_bytes(raw[33:65])
    result = bytes(raw[121:153])
    return Job(address, STATES.get(state, "unknown"), Pubkey.from_bytes(raw[1:33]),
               None if worker == Pubkey.default() else worker, amount, deadline, review, bytes(raw[89:121]),
               None if result == bytes(32) else result)


def parse_config(raw: bytes) -> dict:
    fee_bps, = struct.unpack_from("<H", raw, 96)
    return {"admin": Pubkey.from_bytes(raw[0:32]), "mint": Pubkey.from_bytes(raw[32:64]),
            "fee_token": Pubkey.from_bytes(raw[64:96]), "fee_bps": fee_bps}
