"""The job market on top of the escrow: post, find, claim, deliver, accept, verify_release, reject, release, refund.

A brief is JSON: {"title", "task", "kind", "checks", "buyer", "price_units", "created"}. Its sha256 is on chain; the
bytes are on a relay. `checks` is how the buyer's acceptance is automated where it can be (a Python test file, an
exact expected output, required phrases), so a worker can run them before delivering. A deliverable is sealed to the
buyer's key; its sha256 is the job's result on chain, so the buyer can prove what they received.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from . import sol

KINDS = ("python", "csv", "json", "copy", "text")


@dataclass
class Brief:
    title: str
    task: str
    kind: str = "text"
    checks: dict | None = None
    buyer: str = ""
    price_units: int = 0
    created: int = 0
    job_id: str = ""                     # hex; the job account is the PDA of this id, so a worker can claim it
    preferences: list | None = None      # the buyer's standing preferences, only if they consented for this job
    scripted_answer: str | None = None   # demos and tests only: what the `scripted` model returns
    seal_to: str | None = None           # hex X25519 key to seal the deliverable to (the web app's, from a wallet
                                         # signature); default: the buyer's own Solana key

    def encode(self) -> bytes:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, raw: bytes) -> "Brief":
        d = json.loads(raw)
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def new_job_id() -> bytes:
    return secrets.token_bytes(32)


# ---- token accounts ------------------------------------------------------------------------------------------------

def vault_for(ledger, mint: Pubkey) -> Pubkey:
    env = getattr(ledger, "env", None)
    if env is not None:
        return env.vault
    from ..pro import sol_budget as sb
    return sb.ata(sol.vault_authority(ledger.program), mint)


def token_account_for(ledger, owner: Pubkey, mint: Pubkey) -> Pubkey:
    env = getattr(ledger, "env", None)
    if env is not None:
        return env.accounts[bytes(owner)]
    from ..pro import sol_budget as sb
    return sb.ata(owner, mint)


def stake_account_for(ledger, owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Where a worker's claim stake comes from and goes back to: its ATA on a cluster; in the in-process runtime a
    separate faucet-funded account (localsvm.Escrow.stake_account)."""
    env = getattr(ledger, "env", None)
    if env is not None:
        return env.stake_account(owner)
    from ..pro import sol_budget as sb
    return sb.ata(owner, mint)


def _ensure_ata(ledger, payer: Keypair, owner: Pubkey, mint: Pubkey) -> list:
    if getattr(ledger, "env", None) is not None:
        return []
    from ..pro import sol_budget as sb
    return [sb.create_ata_idempotent(payer.pubkey(), owner, mint)]   # a no-op if it exists: saves a round trip


# ---- the lifecycle ---------------------------------------------------------------------------------------------------

def post(ledger, relay, buyer: Keypair, brief: Brief, price_units: int, work_s: int = 3600,
         review_s: int = 86_400, job_id: bytes | None = None, verifier: Pubkey | None = None) -> bytes:
    """The buyer's one signature: the price moves into escrow; the brief's hash goes on chain, the brief to a relay.
    `verifier` (default none): a key that may release the delivered work on proof (`verify_release`), no buyer step.
    The escrow refuses a price under its minimum job (1 USDC) and over its per-job cap, and new posts while paused."""
    cfg = ledger.config()
    mint = cfg["mint"]
    brief.buyer = str(buyer.pubkey())
    brief.price_units = price_units
    brief.created = brief.created or int(time.time())
    job_id = job_id or new_job_id()
    brief.job_id = job_id.hex()
    raw = brief.encode()
    h = relay.put_brief(raw)
    ix = sol.post(ledger.program, buyer.pubkey(), job_id, price_units, work_s, review_s, bytes.fromhex(h),
                  token_account_for(ledger, buyer.pubkey(), mint), vault_for(ledger, mint), verifier)
    ledger.send([ix], buyer)
    if getattr(ledger, "env", None) is not None:
        ledger.env.known_jobs.append(job_id)
    return job_id


# Every settlement closes the job account (its rent goes back to the buyer), so the chain no longer holds a settled
# job. The outcome is recorded here from the settling transaction this process sent: job address -> the job as it
# settled (state "released" or "refunded", `closed=True` is implied by the account being gone).
_SETTLED: dict[Pubkey, sol.Job] = {}


def _record(ledger, j: sol.Job, state: str, **changes) -> None:
    from dataclasses import replace
    _SETTLED[j.address] = replace(j, state=state, stake=0, **changes)


def job(ledger, job_id: bytes) -> sol.Job | None:
    """The job as the chain holds it; a job this process settled (the account is closed) as it settled; otherwise
    None (never posted, or settled elsewhere: closed)."""
    addr = sol.job_pda(ledger.program, job_id)
    raw = ledger.account(addr)
    if raw and len(raw) in sol.JOB_LENS:
        return sol.parse_job(addr, raw)
    return _SETTLED.get(addr)


def is_closed(ledger, job_id: bytes) -> bool:
    """True when the job's account is gone: it settled (or never existed)."""
    raw = ledger.account(sol.job_pda(ledger.program, job_id))
    return not (raw and len(raw) in sol.JOB_LENS)


def open_jobs(ledger, relay) -> list[tuple[sol.Job, Brief]]:
    """Open jobs whose brief this relay holds and verifies, oldest deadline first."""
    out = []
    now = ledger.now()
    for j in ledger.jobs("open"):
        if j.deadline < now:
            continue
        try:
            out.append((j, Brief.decode(relay.get_brief(j.brief.hex()))))
        except Exception:  # noqa: BLE001 - a brief we cannot verify is skipped, never trusted
            continue
    return sorted(out, key=lambda x: x[0].deadline)


def job_id_for(ledger, job_account: Pubkey, brief: Brief | None = None, relay=None) -> bytes | None:
    """The job id behind a job account: from its brief, checked against the account's address."""
    if brief is None:
        raw = ledger.account(job_account)
        if raw is None or relay is None:
            return None
        brief = Brief.decode(relay.get_brief(sol.parse_job(job_account, raw).brief.hex()))
    try:
        jid = bytes.fromhex(brief.job_id)
    except (TypeError, ValueError):
        return None
    return jid if len(jid) == 32 and sol.job_pda(ledger.program, jid) == job_account else None


def claim(ledger, worker: Keypair, job_id: bytes) -> bool:
    """Exactly one worker: the escrow's state change from open to claimed is the mutex. The worker stakes
    sol.stake_for(price) (10%, at least 0.1 USDC) from its token account; it comes back on delivery, and goes to the
    buyer if the claim times out undelivered. A buyer cannot claim its own job."""
    try:
        mint = ledger.config()["mint"]
        ledger.send([sol.claim(ledger.program, worker.pubkey(), job_id,
                               stake_account_for(ledger, worker.pubkey(), mint), vault_for(ledger, mint))], worker)
        return True
    except Exception:  # noqa: BLE001 - someone else got it, or it closed
        return False


def seal_for(buyer: Pubkey, content: bytes, seal_to: str | None = None) -> bytes:
    if seal_to:
        from nacl.public import PublicKey, SealedBox
        return SealedBox(PublicKey(bytes.fromhex(seal_to))).encrypt(content)
    from ..team.registry import seal_salt  # a sealed box to the buyer's ed25519 key, via curve25519
    return seal_salt(content, buyer)


def open_sealed(buyer: Keypair, blob: bytes) -> bytes:
    from ..team.registry import open_salt
    return open_salt(blob, buyer)


def deliver(ledger, relay, worker: Keypair, job_id: bytes, buyer: Pubkey, content: bytes,
            seal_to: str | None = None) -> str:
    sealed = seal_for(buyer, content, seal_to)
    h = relay.put_delivery(sealed)
    mint = ledger.config()["mint"]
    ledger.send([sol.deliver(ledger.program, worker.pubkey(), job_id, bytes.fromhex(h),
                             stake_account_for(ledger, worker.pubkey(), mint), vault_for(ledger, mint))], worker)
    return h


def fetch_delivery(ledger, relay, buyer: Keypair, job_id: bytes) -> bytes:
    """The deliverable, checked against the hash the worker committed on chain, opened with the buyer's key."""
    j = job(ledger, job_id)
    if j is None or j.result is None:
        raise LookupError("nothing delivered yet")
    blob = relay.get_delivery(j.result.hex())
    if hashlib.sha256(blob).digest() != j.result:
        raise ValueError("the relay's delivery does not match what the worker committed on chain")
    return open_sealed(buyer, blob)


def _need(ledger, job_id: bytes, *states: str) -> sol.Job:
    j = job(ledger, job_id)
    if j is None:
        raise LookupError("No such job on this cluster.")
    if states and j.state not in states:
        raise LookupError(f"The job is {j.state}; this needs it {' or '.join(states)}.")
    return j


def accept(ledger, buyer: Keypair, job_id: bytes) -> str:
    """The buyer pays delivered work. The job closes (its rent back to the buyer). Returns the signature."""
    j = _need(ledger, job_id, "delivered")
    cfg = ledger.config()
    pre = _ensure_ata(ledger, buyer, j.worker, cfg["mint"])
    sig = ledger.send(pre + [sol.accept(ledger.program, buyer.pubkey(), job_id, vault_for(ledger, cfg["mint"]),
                                        token_account_for(ledger, j.worker, cfg["mint"]), cfg["fee_token"])], buyer)
    _record(ledger, j, "released")
    _sibyl_pro(ledger, j)
    return sig


def release(ledger, anyone: Keypair, job_id: bytes) -> str:
    j = _need(ledger, job_id, "delivered")
    cfg = ledger.config()
    pre = _ensure_ata(ledger, anyone, j.worker, cfg["mint"])
    sig = ledger.send(pre + [sol.release(ledger.program, anyone.pubkey(), job_id, vault_for(ledger, cfg["mint"]),
                                         token_account_for(ledger, j.worker, cfg["mint"]), cfg["fee_token"],
                                         j.buyer)], anyone)
    _record(ledger, j, "released")
    _sibyl_pro(ledger, j)
    return sig


def verify_release(ledger, verifier: Keypair, job_id: bytes, proof_root: bytes,
                   result_hash: bytes | None = None) -> str:
    """Paid on proof: the job's verifier releases the delivered work and records `proof_root` (in the transaction;
    the job account closes). Pass the `result_hash` the proof actually covers; the escrow refuses it unless it is
    exactly the hash the worker committed ("verifier releases unproven work"). Without one, the job's committed hash
    is used (the verifier checked the delivery fetched under that hash). Returns the signature."""
    j = _need(ledger, job_id, "delivered")
    if j.verifier is None or j.verifier != verifier.pubkey():
        raise LookupError("This job does not name you as its verifier.")
    cfg = ledger.config()
    pre = _ensure_ata(ledger, verifier, j.worker, cfg["mint"])
    sig = ledger.send(pre + [sol.verify_release(ledger.program, verifier.pubkey(), job_id, result_hash or j.result,
                                                proof_root, vault_for(ledger, cfg["mint"]),
                                                token_account_for(ledger, j.worker, cfg["mint"]), cfg["fee_token"],
                                                j.buyer)], verifier)
    _record(ledger, j, "released", proof=proof_root)
    _sibyl_pro(ledger, j)
    return sig


def verify_reject(ledger, verifier: Keypair, job_id: bytes, proof_root: bytes) -> str:
    """The job's verifier fails the delivered work (inside the review window): the buyer is refunded and the job
    closes. `proof_root` (32 bytes) is the verifier's evidence, carried in the transaction. Returns the signature."""
    j = _need(ledger, job_id, "delivered")
    if j.verifier is None or j.verifier != verifier.pubkey():
        raise LookupError("This job does not name you as its verifier.")
    cfg = ledger.config()
    sig = ledger.send([sol.verify_reject(ledger.program, verifier.pubkey(), job_id, proof_root,
                                         vault_for(ledger, cfg["mint"]),
                                         token_account_for(ledger, j.buyer, cfg["mint"]), j.buyer)], verifier)
    _record(ledger, j, "refunded", proof=proof_root)
    return sig


def settle(ledger, payer: Keypair, job_id: bytes) -> str:
    """The deadline crank, by anyone (`payer` signs and pays the fee): delivered work past its review deadline with
    no verdict pays the worker; open or claimed work past its work deadline refunds the buyer (a timed-out claim's
    stake too). The job closes; its rent goes to the buyer. Returns the signature."""
    j = _need(ledger, job_id, "open", "claimed", "delivered")
    if ledger.now() <= j.deadline:
        raise LookupError("The job is not past its deadline yet.")
    cfg = ledger.config()
    mint = cfg["mint"]
    buyer_tok = token_account_for(ledger, j.buyer, mint)
    pre = []
    if j.state == "delivered":
        pre = _ensure_ata(ledger, payer, j.worker, mint)
        worker_tok = token_account_for(ledger, j.worker, mint)
    else:
        worker_tok = buyer_tok       # unused on a refund
    sig = ledger.send(pre + [sol.crank(ledger.program, payer.pubkey(), job_id, vault_for(ledger, mint), worker_tok,
                                       cfg["fee_token"], buyer_tok, j.buyer)], payer)
    _record(ledger, j, "released" if j.state == "delivered" else "refunded")
    if j.state == "delivered":
        _sibyl_pro(ledger, j)
    return sig


def _sibyl_pro(ledger, j: sol.Job) -> None:
    """The buyer just paid Knos's fee: Sibyl Pro for them for the next 30 days (knos.sibyl_pro). Never blocks
    the payment, which has already happened on chain."""
    try:
        from .. import sibyl_pro
        sibyl_pro.from_job(str(j.buyer), str(j.address), getattr(ledger, "network", "devnet"))
    except Exception:  # noqa: BLE001 - mainnet is locked; the record is a convenience, the chain is the truth
        pass


def reject(ledger, buyer: Keypair, job_id: bytes) -> str:
    """The buyer rejects delivered work inside the review window: refunded, the job closes. Refused by the escrow
    for a job that names a verifier (only the verifier or the deadline settles that one)."""
    j = _need(ledger, job_id, "delivered")
    if j.verifier is not None:
        raise LookupError("This job names a verifier: only the verifier or the review deadline can settle it.")
    cfg = ledger.config()
    sig = ledger.send([sol.reject(ledger.program, buyer.pubkey(), job_id, vault_for(ledger, cfg["mint"]),
                                  token_account_for(ledger, buyer.pubkey(), cfg["mint"]))], buyer)
    _record(ledger, j, "refunded")
    return sig


def refund(ledger, buyer: Keypair, job_id: bytes) -> str:
    j = _need(ledger, job_id, "open", "claimed")
    cfg = ledger.config()
    sig = ledger.send([sol.refund(ledger.program, buyer.pubkey(), job_id, vault_for(ledger, cfg["mint"]),
                                  token_account_for(ledger, buyer.pubkey(), cfg["mint"]))], buyer)
    _record(ledger, j, "refunded")
    return sig
