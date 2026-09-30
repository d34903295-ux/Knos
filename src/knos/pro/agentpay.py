# Knos Pro. Licensed under the Functional Source License 1.1 (MIT future licence): see src/knos/pro/LICENSE.
"""An agent pays for an API over HTTP 402, from its own budget wallet, inside its Knos cap.

Two protocols, both through their reference SDKs (the optional `knos[agentpay]` extra):

  MPP on Tempo   pympp (github.com/tempoxyz/pympp): the server answers 402 with `WWW-Authenticate: Payment ...`;
                 the client signs a TIP-20 transfer and retries with `Authorization: Payment ...`.
                 Spec: https://mpp.dev/protocol , https://mpp.dev/payment-methods/tempo/charge
  x402 on Solana x402 (github.com/x402-foundation/x402): the server answers 402 with its payment requirements;
                 the client signs an SPL TransferChecked (the facilitator pays the fee) and retries.

Two limits, in this order, before anything is signed:
  1. Knos's cap for this agent (`knos budget fund` sets it): what it has spent, from the ledger in
     ~/.knos/agentpay.db, plus this payment, must not exceed it. The asset must be the stablecoin the wallet holds.
  2. The chain: the agent signs from its own wallet, which holds only what you funded it with. Even with Knos
     bypassed, it cannot spend more than that balance.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from .. import paths
from . import budget, solana, tempo, wallets

SOLANA_CAIP2 = {"mainnet": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
                "devnet": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"}


class Refused(Exception):
    """Knos refused to sign: no wallet, over the cap, wrong asset or network. Nothing was signed."""


@dataclass
class Paid:
    status: int
    paid: float
    chain: str
    body: str
    receipt: str = ""


# ---- the ledger -------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(paths.home() / "agentpay.db"), timeout=10, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("CREATE TABLE IF NOT EXISTS payments (ts REAL, agent TEXT, chain TEXT, network TEXT, url TEXT, "
                 "amount REAL, status TEXT)")
    return conn


def spent(agent: str | None = None, since: float = 0.0) -> float:
    conn = _db()
    try:
        sql, args = "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status IN ('signed','paid') AND ts >= ?", [since]
        if agent:
            sql += " AND agent = ?"
            args.append(agent)
        return float(conn.execute(sql, args).fetchone()[0])
    finally:
        conn.close()


def _record(agent: str, chain: str, network: str, url: str, amount: float, status: str) -> None:
    conn = _db()
    try:
        conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?)", (time.time(), agent, chain, network, url,
                                                                    amount, status))
    finally:
        conn.close()


# ---- the gate: runs before any signature ------------------------------------------------------

def agent_config(agent: str) -> dict:
    got = (budget.agents() or {}).get(agent)
    if not got:
        raise Refused(f"knos: agent {agent} has no budget wallet. A person sets one up: "
                      f"knos budget fund --agent {agent} 5 --chain tempo")
    return got


def gate(agent: str, chain: str, network: str, asset: str, amount: float, url: str) -> None:
    """Refuse unless this payment fits the agent's Knos cap, on its chain, network and stablecoin."""
    cfg = agent_config(agent)
    if cfg["chain"] != chain:
        raise Refused(f"knos: {agent}'s budget wallet is on {cfg['chain']}; this API asks for {chain}.")
    if cfg.get("network", "mainnet") != network:
        raise Refused(f"knos: {agent}'s wallet is on {cfg.get('network')}; this API asks for {network}.")
    ok_assets = ({a.lower() for a in tempo.TOKENS[network].values()} if chain == "tempo"
                 else {solana.USDC[network].lower()})
    if asset.lower() not in ok_assets:
        raise Refused(f"knos: this API wants to be paid in {asset}, which is not the stablecoin in {agent}'s wallet.")
    used = spent(agent)
    if used + amount > float(cfg["cap"]) + 1e-9:
        raise Refused(f"knos: {agent} has spent {used:g} of its {float(cfg['cap']):g} cap; this payment of "
                      f"{amount:g} would go past it. A person can raise it: knos budget fund --agent {agent} ...")
    over = budget.refusal()
    if over:
        raise Refused(over)
    _record(agent, chain, network, url, amount, "signed")


# ---- the two protocols ------------------------------------------------------------------------

def _need(module: str) -> None:
    import importlib.util

    if importlib.util.find_spec(module) is None:
        raise Refused("knos: agent payments need the optional libraries:  pipx inject knos 'knos[agentpay]'")


async def _pay_tempo(agent: str, url: str, method: str, body: bytes | None, network: str) -> Paid:
    _need("mpp")
    import httpx
    from mpp.client import Client
    from mpp.methods.tempo import ChargeIntent, TempoAccount
    from mpp.methods.tempo import tempo as tempo_method

    chain_id = tempo.CHAINS[network]["id"]
    m = tempo_method(account=TempoAccount.from_key(wallets.tempo_key(agent)), chain_id=chain_id,
                     intents={"charge": ChargeIntent()})
    signed: dict[str, float] = {}
    original = m.create_credential

    async def guarded(challenge: Any) -> Any:
        req = challenge.request or {}
        amount = int(str(req.get("amount", "0"))) / 10 ** tempo.DECIMALS
        gate(agent, "tempo", network, str(req.get("currency", "")), amount, url)
        signed["amount"] = amount
        return await original(challenge)

    m.create_credential = guarded  # type: ignore[method-assign]
    async with Client(methods=[m]) as client:
        try:
            resp: httpx.Response = await client.request(method, url, content=body)
        except Refused:
            raise
        except Exception as why:
            if type(why).__name__ == "PaymentOutcomeUnknownError":
                raise Refused(f"knos: a payment of {signed.get('amount', 0):g} was sent but the API did not answer, so "
                              "whether it went through is unknown. It is counted against the cap. Check the agent "
                              "wallet (knos budget agents) before trying again.") from why
            raise
    return Paid(resp.status_code, signed.get("amount", 0.0), "tempo", resp.text[:4000],
                resp.headers.get("payment-receipt", ""))


async def _pay_x402(agent: str, url: str, method: str, body: bytes | None, network: str) -> Paid:
    _need("x402")
    from x402 import x402Client
    from x402.http.clients import wrapHttpxWithPayment
    from x402.mechanisms.svm.exact.register import register_exact_svm_client
    from x402.mechanisms.svm.signers import KeypairSigner
    from x402.schemas.hooks import AbortResult

    client = x402Client()
    register_exact_svm_client(client, KeypairSigner(wallets.solana_keypair(agent)), networks=[SOLANA_CAIP2[network]],
                              rpc_url=solana.rpc_url(network))
    signed: dict[str, Any] = {}

    def before(ctx: Any) -> Any:
        req = ctx.selected_requirements
        amount = int(str(req.get_amount())) / 10 ** solana.USDC_DECIMALS
        if str(req.network) != SOLANA_CAIP2[network]:
            return AbortResult(reason="network", message=f"knos: this API asks for {req.network}")
        try:
            gate(agent, "solana", network, str(req.asset), amount, url)
        except Refused as why:
            signed["refused"] = str(why)
            return AbortResult(reason="knos_cap", message=str(why))
        signed["amount"] = amount
        return None

    client.on_before_payment_creation(before)
    async with wrapHttpxWithPayment(client) as http:
        try:
            resp = await http.request(method, url, content=body)
        except Exception as why:  # the SDK raises when a hook aborts
            if signed.get("refused"):
                raise Refused(signed["refused"]) from why
            raise
    return Paid(resp.status_code, signed.get("amount", 0.0), "solana", resp.text[:4000],
                resp.headers.get("payment-response", ""))


def protocol_of(resp: Any) -> str | None:
    """Which 402 this is: 'mpp' (WWW-Authenticate: Payment), 'x402', or None when it is not a payment request."""
    if resp.status_code != 402:
        return None
    for h in resp.headers.get_list("www-authenticate") if hasattr(resp.headers, "get_list") else []:
        if h.strip().lower().startswith("payment"):
            return "mpp"
    if resp.headers.get("payment-required"):
        return "x402"
    try:
        body = resp.json()
    except Exception:
        body = {}
    if isinstance(body, dict) and ("accepts" in body or "x402Version" in body):
        return "x402"
    return None


def pay(agent: str, url: str, method: str = "GET", body: bytes | None = None) -> Paid:
    """Fetch `url`; if it asks for payment, pay it from `agent`'s wallet within its cap, and return the answer."""
    import httpx

    cfg = agent_config(agent)
    network = cfg.get("network", "mainnet")
    with httpx.Client(timeout=30) as http:
        first = http.request(method, url, content=body)
    kind = protocol_of(first)
    if kind is None:
        return Paid(first.status_code, 0.0, "", first.text[:4000])
    if kind == "mpp":
        if cfg["chain"] != "tempo":
            raise Refused(f"knos: this API is paid over MPP (Tempo); {agent}'s wallet is on {cfg['chain']}.")
        return asyncio.run(_pay_tempo(agent, url, method, body, network))
    if cfg["chain"] != "solana":
        raise Refused(f"knos: this API is paid over x402 (Solana); {agent}'s wallet is on {cfg['chain']}.")
    return asyncio.run(_pay_x402(agent, url, method, body, network))


def summary() -> list[dict]:
    """Per agent: chain, network, cap and what has been signed."""
    out = []
    for name, cfg in (budget.agents() or {}).items():
        out.append({"agent": name, **cfg, "spent": spent(name)})
    return out
