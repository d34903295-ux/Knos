"""The Solana escrow program (`programs/knos_escrow`), from Python: instruction builders, addresses and the job layout.

Layouts are the program's own (programs/knos_escrow/src/lib.rs):

    config PDA ["config2"]: admin(32) mint(32) fee_token(32) fee_bps(u16) min_fee(u64) min_amount(u64)
                            max_amount(u64, 0 = no cap) paused(u8) bump(u8)
    vault authority PDA ["vault"]: owns the one vault token account per mint
    job PDA ["job", id(32)]: state(u8) buyer(32) worker(32) amount(u64) deadline(i64) review(i64) brief(32) result(32)
                             verifier(32, zero = none) proof(32) stake(u64)
                             (0.3.4 jobs: 217 bytes, no stake; jobs before 0.3.4: 153 bytes, no verifier/proof)

    1  Post          buyer(s,w) job(w) buyer_token(w) vault_token(w) config token system
                     data: id[32] amount u64 work i64 review i64 brief[32] verifier[32]
    2  Claim         worker(s) job(w) worker_token(w) vault_token(w) config token   (stakes stake_for(price); not the buyer)
    3  Deliver       worker(s) job(w) worker_token(w) vault_token(w) vault_auth token   data: result[32] (stake back)
    4  Accept        buyer(s) job(w) vault_token(w) vault_auth worker_token(w) fee_token(w) config token buyer(w)
    5  Release       anyone(s), same accounts as Accept, after the review window
    6  Reject        buyer(s,w) job(w) vault_token(w) vault_auth buyer_token(w) token  (inside the review window; not
                     on a job that names a verifier)
    7  Refund        same accounts as Reject       (open or claimed, past the work deadline; a claim's stake included)
    12 VerifyReject  verifier(s) job(w) vault_token(w) vault_auth buyer_token(w) token buyer(w)  data: proof_root[32]
    13 Settle        anyone(s) job(w) vault_token(w) vault_auth worker_token(w) fee_token(w) buyer_token(w) buyer(w)
                     config token   (the deadline crank: delivered -> worker paid; open/claimed -> buyer refunded)

Every settlement (4 5 6 7 11 12 13) closes the job account; its rent lamports go back to the buyer.
    8  Init2         admin(s,w) config(w) fee_token system legacy_config
                     data: fee_bps u16 min_fee u64 min_amount u64 max_amount u64 paused u8
    9  SetPause      admin(s) config(w)                                        data: paused u8 (new posts only)
    10 LowerCap      admin(s) config(w)                                        data: max_amount u64 (only downward)
    11 VerifyRelease verifier(s), same accounts as Accept                      data: result_hash[32] proof_root[32]

The fee on release is max(fee_bps of the price, min_fee); the minimum job is min_amount (1 USDC on devnet).
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
JOB_LEN = 225
V034_JOB_LEN = 217            # jobs posted by 0.3.4 (no stake): still parsed, still settle
LEGACY_JOB_LEN = 153          # jobs posted before 0.3.4 (no verifier, no proof): still parsed, still settle
JOB_LENS = (JOB_LEN, V034_JOB_LEN, LEGACY_JOB_LEN)
STAKE_BPS = 1_000             # the worker's claim stake: 10% of the price ...
MIN_STAKE_UNITS = 100_000     # ... at least 0.1 USDC


def stake_for(amount: int) -> int:
    """The program's claim stake: max(10% of the price, 0.1 USDC), in the job's mint."""
    return max(amount * STAKE_BPS // 10_000, MIN_STAKE_UNITS)
CONFIG_LEN = 124
FEE_BPS = 250                 # 2.5%, with a 0.05 USDC floor (MIN_FEE_UNITS)
MIN_FEE_UNITS = 50_000        # 0.05 USDC
MIN_JOB_UNITS = 1_000_000     # 1 USDC
NO_VERIFIER = Pubkey.default()


def program_id(pid: str | Pubkey | None = None) -> Pubkey:
    if isinstance(pid, Pubkey):
        return pid
    got = pid or DEVNET_PROGRAM or os.environ.get("KNOS_ESCROW_PROGRAM", "")
    if not got:
        raise LookupError("no escrow program id: set KNOS_ESCROW_PROGRAM or pass one")
    return Pubkey.from_string(got)


def config_pda(pid: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"config2"], pid)[0]


def legacy_config_pda(pid: Pubkey) -> Pubkey:
    """The pre-0.3.4 config (["config"]): no longer read by the program; Init2 checks its admin."""
    return Pubkey.find_program_address([b"config"], pid)[0]


def vault_authority(pid: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"vault"], pid)[0]


def job_pda(pid: Pubkey, job_id: bytes) -> Pubkey:
    return Pubkey.find_program_address([b"job", job_id], pid)[0]


def fee_for(amount: int, fee_bps: int = FEE_BPS, min_fee: int = MIN_FEE_UNITS, min_amount: int = MIN_JOB_UNITS) -> int:
    """The program's fee rule: max(fee_bps of the price, min_fee); a (pre-0.3.4) job under the minimum pays the
    percentage alone. Never more than the price."""
    pct = amount * fee_bps // 10_000
    return min(amount, min_fee if amount >= min_amount and pct < min_fee else pct)


def _m(k: Pubkey, s: bool, w: bool) -> AccountMeta:
    return AccountMeta(k, is_signer=s, is_writable=w)


def init2(pid: Pubkey, admin: Pubkey, fee_token: Pubkey, fee_bps: int = FEE_BPS, min_fee: int = MIN_FEE_UNITS,
          min_amount: int = MIN_JOB_UNITS, max_amount: int = 0, paused: bool = False) -> Instruction:
    return Instruction(pid, bytes([8]) + struct.pack("<HQQQB", fee_bps, min_fee, min_amount, max_amount, int(paused)),
                       [_m(admin, True, True), _m(config_pda(pid), False, True), _m(fee_token, False, False),
                        _m(SYSTEM, False, False), _m(legacy_config_pda(pid), False, False)])


def init(pid: Pubkey, admin: Pubkey, fee_token: Pubkey, fee_bps: int = FEE_BPS, **limits) -> Instruction:
    """Initialise the escrow's config (Init2; the pre-0.3.4 Init is retired)."""
    return init2(pid, admin, fee_token, fee_bps, **limits)


def set_pause(pid: Pubkey, admin: Pubkey, paused: bool) -> Instruction:
    return Instruction(pid, bytes([9, int(paused)]), [_m(admin, True, False), _m(config_pda(pid), False, True)])


def lower_cap(pid: Pubkey, admin: Pubkey, max_amount: int) -> Instruction:
    return Instruction(pid, bytes([10]) + struct.pack("<Q", max_amount),
                       [_m(admin, True, False), _m(config_pda(pid), False, True)])


def lower_fee(pid: Pubkey, admin: Pubkey, fee_bps: int) -> Instruction:
    """LowerFee (14): the admin lowers fee_bps; the fee never goes up."""
    return Instruction(pid, bytes([14]) + struct.pack("<H", fee_bps),
                       [_m(admin, True, False), _m(config_pda(pid), False, True)])


def post(pid: Pubkey, buyer: Pubkey, job_id: bytes, amount: int, work_s: int, review_s: int, brief_hash: bytes,
         buyer_token: Pubkey, vault_token: Pubkey, verifier: Pubkey | None = None) -> Instruction:
    """Post a job. `verifier` (default none) may later release the delivered job on proof (VerifyRelease)."""
    if len(job_id) != 32 or len(brief_hash) != 32:
        raise ValueError("job id and brief hash are 32 bytes")
    data = bytes([1]) + job_id + struct.pack("<Qqq", amount, work_s, review_s) + brief_hash + \
        bytes(verifier or NO_VERIFIER)
    return Instruction(pid, data, [_m(buyer, True, True), _m(job_pda(pid, job_id), False, True),
                                   _m(buyer_token, False, True), _m(vault_token, False, True),
                                   _m(config_pda(pid), False, False), _m(TOKEN, False, False),
                                   _m(SYSTEM, False, False)])


def claim(pid: Pubkey, worker: Pubkey, job_id: bytes, worker_token: Pubkey, vault_token: Pubkey) -> Instruction:
    """Claim an open job, staking stake_for(price) from `worker_token` into the vault. Never the job's buyer."""
    return Instruction(pid, bytes([2]), [_m(worker, True, False), _m(job_pda(pid, job_id), False, True),
                                         _m(worker_token, False, True), _m(vault_token, False, True),
                                         _m(config_pda(pid), False, False), _m(TOKEN, False, False)])


def deliver(pid: Pubkey, worker: Pubkey, job_id: bytes, result_hash: bytes, worker_token: Pubkey,
            vault_token: Pubkey) -> Instruction:
    """Commit the result hash; the claim's stake goes back to `worker_token`."""
    if len(result_hash) != 32:
        raise ValueError("result hash is 32 bytes")
    return Instruction(pid, bytes([3]) + result_hash,
                       [_m(worker, True, False), _m(job_pda(pid, job_id), False, True), _m(worker_token, False, True),
                        _m(vault_token, False, True), _m(vault_authority(pid), False, False), _m(TOKEN, False, False)])


def _payout(tag: int, data: bytes, pid: Pubkey, signer: Pubkey, job_id: bytes, vault_token: Pubkey,
            worker_token: Pubkey, fee_token: Pubkey, buyer: Pubkey) -> Instruction:
    return Instruction(pid, bytes([tag]) + data,
                       [_m(signer, True, signer == buyer), _m(job_pda(pid, job_id), False, True),
                        _m(vault_token, False, True), _m(vault_authority(pid), False, False),
                        _m(worker_token, False, True), _m(fee_token, False, True), _m(config_pda(pid), False, False),
                        _m(TOKEN, False, False), _m(buyer, signer == buyer, True)])


def settle(pid: Pubkey, signer: Pubkey, job_id: bytes, vault_token: Pubkey, worker_token: Pubkey, fee_token: Pubkey,
           release: bool = False, buyer: Pubkey | None = None) -> Instruction:
    """Accept (the buyer) or, with release=True, release by anyone after the review window: worker paid, fee taken.
    The job closes; its rent goes to `buyer` (default: the signer, i.e. Accept)."""
    return _payout(5 if release else 4, b"", pid, signer, job_id, vault_token, worker_token, fee_token,
                   buyer or signer)


def accept(pid, buyer, job_id, vault_token, worker_token, fee_token) -> Instruction:
    return settle(pid, buyer, job_id, vault_token, worker_token, fee_token)


def release(pid, anyone, job_id, vault_token, worker_token, fee_token, buyer: Pubkey) -> Instruction:
    return settle(pid, anyone, job_id, vault_token, worker_token, fee_token, release=True, buyer=buyer)


def verify_release(pid: Pubkey, verifier: Pubkey, job_id: bytes, result_hash: bytes, proof_root: bytes,
                   vault_token: Pubkey, worker_token: Pubkey, fee_token: Pubkey, buyer: Pubkey) -> Instruction:
    """Paid on proof: the job's verifier releases a delivered job whose committed result is `result_hash`, recording
    `proof_root`. The program refuses any other hash ("verifier releases unproven work"). The job closes (rent to
    `buyer`)."""
    if len(result_hash) != 32 or len(proof_root) != 32:
        raise ValueError("result hash and proof root are 32 bytes")
    return _payout(11, result_hash + proof_root, pid, verifier, job_id, vault_token, worker_token, fee_token, buyer)


def _refundish(tag: int, pid: Pubkey, buyer: Pubkey, job_id: bytes, vault_token: Pubkey,
               buyer_token: Pubkey) -> Instruction:
    return Instruction(pid, bytes([tag]), [_m(buyer, True, True), _m(job_pda(pid, job_id), False, True),
                                           _m(vault_token, False, True), _m(vault_authority(pid), False, False),
                                           _m(buyer_token, False, True), _m(TOKEN, False, False)])


def reject(pid, buyer, job_id, vault_token, buyer_token) -> Instruction:
    """The buyer rejects delivered work inside the review window (never a job that names a verifier)."""
    return _refundish(6, pid, buyer, job_id, vault_token, buyer_token)


def refund(pid, buyer, job_id, vault_token, buyer_token) -> Instruction:
    return _refundish(7, pid, buyer, job_id, vault_token, buyer_token)


def verify_reject(pid: Pubkey, verifier: Pubkey, job_id: bytes, proof_root: bytes, vault_token: Pubkey,
                  buyer_token: Pubkey, buyer: Pubkey) -> Instruction:
    """The job's verifier fails the delivered work inside the review window: the buyer is refunded, the job closes."""
    if len(proof_root) != 32:
        raise ValueError("proof root is 32 bytes")
    return Instruction(pid, bytes([12]) + proof_root,
                       [_m(verifier, True, False), _m(job_pda(pid, job_id), False, True), _m(vault_token, False, True),
                        _m(vault_authority(pid), False, False), _m(buyer_token, False, True), _m(TOKEN, False, False),
                        _m(buyer, False, True)])


def crank(pid: Pubkey, anyone: Pubkey, job_id: bytes, vault_token: Pubkey, worker_token: Pubkey, fee_token: Pubkey,
          buyer_token: Pubkey, buyer: Pubkey) -> Instruction:
    """Settle (tag 13), the deadline crank, by anyone: delivered past the review deadline -> the worker is paid; open
    or claimed past the work deadline -> the buyer is refunded (with a timed-out claim's stake). The job closes.
    For an unclaimed job `worker_token` is unused: pass any token account (e.g. the buyer's)."""
    return Instruction(pid, bytes([13]),
                       [_m(anyone, True, anyone == buyer), _m(job_pda(pid, job_id), False, True),
                        _m(vault_token, False, True), _m(vault_authority(pid), False, False),
                        _m(worker_token, False, True), _m(fee_token, False, True), _m(buyer_token, False, True),
                        _m(buyer, anyone == buyer, True), _m(config_pda(pid), False, False), _m(TOKEN, False, False)])


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
    verifier: Pubkey | None = None
    proof: bytes | None = None
    stake: int = 0                 # the claim's stake held in the vault (0 once delivered or settled)


def parse_job(address: Pubkey, raw: bytes) -> Job:
    if len(raw) not in JOB_LENS:
        raise ValueError("not a knos_escrow job account")
    state, = struct.unpack_from("<B", raw, 0)
    amount, deadline, review = struct.unpack_from("<Qqq", raw, 65)
    worker = Pubkey.from_bytes(raw[33:65])
    result = bytes(raw[121:153])
    verifier, proof, stake = None, None, 0
    if len(raw) >= V034_JOB_LEN:
        v = Pubkey.from_bytes(raw[153:185])
        verifier = None if v == NO_VERIFIER else v
        proof = None if raw[185:217] == bytes(32) else bytes(raw[185:217])
    if len(raw) == JOB_LEN:
        stake, = struct.unpack_from("<Q", raw, 217)
    return Job(address, STATES.get(state, "unknown"), Pubkey.from_bytes(raw[1:33]),
               None if worker == Pubkey.default() else worker, amount, deadline, review, bytes(raw[89:121]),
               None if result == bytes(32) else result, verifier, proof, stake)


def parse_config(raw: bytes) -> dict:
    if len(raw) < CONFIG_LEN:
        raise ValueError("not a knos_escrow config2 account")
    fee_bps, min_fee, min_amount, max_amount, paused = struct.unpack_from("<HQQQB", raw, 96)
    return {"admin": Pubkey.from_bytes(raw[0:32]), "mint": Pubkey.from_bytes(raw[32:64]),
            "fee_token": Pubkey.from_bytes(raw[64:96]), "fee_bps": fee_bps, "min_fee": min_fee,
            "min_amount": min_amount, "max_amount": max_amount, "paused": bool(paused)}
