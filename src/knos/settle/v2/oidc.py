"""Client for the second deployment of knos-oidc (programs-v2/knos_oidc): the Solana program that verifies an RS256
OIDC token from GitHub Actions or GitLab CI on chain. Pure instruction builders and account readers; no network.

Writing, stepping and closing a token are the first deployment's instructions, byte for byte, sent to this program;
the same names are here. What is new is how a signing key lives:

    register_key_ix(payer, issuer, n, attest)        # a key GitHub's signature names; it waits KEY_DELAY and the guardian
    refresh_ix(payer, issuer, n, attest)             # the same attestation again: the key lives KEY_TTL from now
    approve_ix(guardian, issuer, n)                  # the guardian lets an attested key be used
    revoke_ix(guardian, issuer, n)                   # the guardian ends a key, for ever
    ok, why = key_usable(read_key(account_data), now)   # what Step will say about this key, before a fee is spent
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from .. import oidc as _first
from ..oidc import (CHUNK, GITHUB, GITLAB, ISSUERS, JWKS, LATE, MAX_JWT, SYSTEM, T_JWT, Token, jwks_keys, key_hash, key_params,  # noqa: F401
                    modulus_bytes, read_token, rotate_audience, step_plan, token_id)

IDS = json.loads(resources.files(__package__).joinpath("program_ids.json").read_text())
OIDC_ID = Pubkey.from_string(IDS["knos_oidc"])
GUARDIAN = Pubkey.from_string(IDS["guardian"])    # a multisig vault: it approves attested keys and revokes keys, nothing else

KEY_DELAY = 86_400          # a key admitted by an attestation verifies nothing for a day
KEY_TTL = 30 * 86_400       # a key expires this long after it was last attested
K_HDR = 40                  # the key account's header; n and r2 follow
APPROVED, REVOKED, GENESIS = 1, 2, 4


def token_pda(payer: Pubkey, tid: bytes, program: Pubkey = OIDC_ID) -> Pubkey:
    return _first.token_pda(payer, tid, program)


def key_pda(issuer: int, n: int, program: Pubkey = OIDC_ID) -> Pubkey:
    return _first.key_pda(issuer, n, program)


def write_ixs(payer: Pubkey, tid: bytes, jwt: str | bytes, program: Pubkey = OIDC_ID) -> list[Instruction]:
    return _first.write_ixs(payer, tid, jwt, program)


def step_ix(payer: Pubkey, tid: bytes, key: Pubkey, squarings: int, program: Pubkey = OIDC_ID) -> Instruction:
    return _first.step_ix(payer, tid, key, squarings, program)


def close_ix(payer: Pubkey, tid: bytes, program: Pubkey = OIDC_ID) -> Instruction:
    return _first.close_ix(payer, tid, program)


def register_key_ix(payer: Pubkey, issuer: int, n: int, attest: Pubkey | None = None, program: Pubkey = OIDC_ID) -> Instruction:
    """A genesis key (one of GitHub's four) needs no `attest`. Any other key needs a VERIFIED token account whose
    audience is rotate_audience(issuer, n), from the pinned rotate workflow run in the attester's repository."""
    return _first.register_key_ix(payer, issuer, n, attest, program)


def key_params_ix(payer: Pubkey, issuer: int, n: int, program: Pubkey = OIDC_ID) -> Instruction:
    return _first.key_params_ix(payer, issuer, n, program)


def refresh_ix(payer: Pubkey, issuer: int, n: int, attest: Pubkey, program: Pubkey = OIDC_ID) -> Instruction:
    """The key's expiry becomes now + KEY_TTL if that is later. `attest` as for register_key_ix. Anyone may send it."""
    return Instruction(program, b"\x05", [AccountMeta(payer, True, False), AccountMeta(key_pda(issuer, n, program), False, True),
                                          AccountMeta(attest, False, False)])


def approve_ix(guardian: Pubkey, issuer: int, n: int, program: Pubkey = OIDC_ID) -> Instruction:
    return Instruction(program, b"\x06", [AccountMeta(guardian, True, False), AccountMeta(key_pda(issuer, n, program), False, True)])


def revoke_ix(guardian: Pubkey, issuer: int, n: int, program: Pubkey = OIDC_ID) -> Instruction:
    """For ever: no instruction undoes it, and the same modulus cannot be registered for that issuer again."""
    return Instruction(program, b"\x07", [AccountMeta(guardian, True, False), AccountMeta(key_pda(issuer, n, program), False, True)])


@dataclass
class Key:
    state: int          # 0 created, 1 ready (its Montgomery constants are on chain and checked)
    issuer: int
    bits: int           # 2048 or 4096
    active_at: int      # the key verifies nothing before this time
    expires_at: int     # nor from this time on
    approved: bool      # by the guardian (a genesis key is created approved)
    revoked: bool
    genesis: bool


def read_key(data: bytes | None) -> Key | None:
    """A key account's header; None for anything that is not a key account of this layout."""
    if not data or len(data) < K_HDR or data[2] not in (64, 128) or len(data) != K_HDR + 8 * data[2]:
        return None
    flags = data[24]
    return Key(state=data[0], issuer=data[1], bits=32 * data[2], active_at=int.from_bytes(data[8:16], "little", signed=True),
               expires_at=int.from_bytes(data[16:24], "little", signed=True),
               approved=bool(flags & APPROVED), revoked=bool(flags & REVOKED), genesis=bool(flags & GENESIS))


def _when(t: int) -> str:
    try:
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return f"time {t}"


def key_usable(key: Key | None, now: int) -> tuple[bool, str]:
    """Whether Step accepts this key at `now` (the chain's clock), and if not, why and what to do. The same checks,
    in the same order, as the program's."""
    if key is None:
        return False, "this signing key is not on chain yet. Send RegisterKey with an attestation from the rotate workflow."
    if key.state != 1:
        return False, "this signing key is registered but not ready. Send KeyParams for it (anyone can)."
    if key.revoked:
        return False, "the guardian revoked this signing key. It cannot be used again."
    if not (key.genesis or key.approved):
        wait = f" Its {KEY_DELAY // 3600}-hour wait ends {_when(key.active_at)}." if now < key.active_at else ""
        return False, f"this signing key is new and the guardian has not approved it yet.{wait} Try again after the approval."
    if now < key.active_at:
        return False, f"this signing key is new: it can be used from {_when(key.active_at)}. Try again then."
    if now >= key.expires_at:
        return False, (f"this signing key expired {_when(key.expires_at)}: nothing attested it for {KEY_TTL // 86_400} days. "
                       "Run the rotate workflow and send Refresh with its token.")
    return True, ""
