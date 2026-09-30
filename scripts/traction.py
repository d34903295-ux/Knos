"""Count Knos licence payments from the chain, and write docs/TRACTION.md. Anyone can run it; public data only.

    python scripts/traction.py                 mainnet, both chains
    python scripts/traction.py --exclude <address> [--exclude ...]   leave out wallets the founder owns

A payment counts when it paid the Knos receiving address in an accepted stablecoin AND carries a `knos:<plan>:<id>`
memo (Solana: the memo program; Tempo: transferWithMemo). Nothing is typed in: every row links to its transaction.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knos.pro import solana, tempo  # noqa: E402

# The first Tempo mainnet block worth scanning: the Knos receiving address did not exist before it.
TEMPO_START_BLOCK = 41_800_000
TEMPO_CHUNK = 99_000  # the public RPC answers at most 100,000 blocks per eth_getLogs

# End-to-end test payments made while building 0.2.0, on test networks with faucet funds (not revenue).
TEST_PAYMENTS = [
    ("Solana devnet", "`knos pro buy --network devnet`, paid with USDC carrying the Solana Pay reference and the "
     "`knos:` memo, found by its reference with `knos pro check`, Pro activated",
     "5RMh9rdVLAGgusVnD9xtUmGyawk19KSr3sB5o16FfFpKK2UAHcPWcndhiG6TNbou7vSCGqqBtM8RpGQ1QwfZ3ZZs",
     "https://explorer.solana.com/tx/5RMh9rdVLAGgusVnD9xtUmGyawk19KSr3sB5o16FfFpKK2UAHcPWcndhiG6TNbou7vSCGqqBtM8RpGQ1QwfZ3ZZs"
     "?cluster=devnet"),
    ("Tempo Moderato testnet", "`knos pro buy --chain tempo --network testnet`, paid with transferWithMemo, found on "
     "chain by `knos pro check`, Pro activated", "0x51fcead0856fa21b4543ef344422687c2d7d0e74fd0e1932c229c8ecfe95e51a",
     "https://explore.testnet.tempo.xyz/tx/0x51fcead0856fa21b4543ef344422687c2d7d0e74fd0e1932c229c8ecfe95e51a"),
    ("Tempo Moderato testnet", "an agent's own budget wallet paying an MPP API (0.01 each, cap 0.05) with `knos pay`; "
     "5 paid, the 6th refused by Knos before signing",
     "0x78914d6468910afc5604c985cf98df2c66ec0438ae2f24c0e8346a79abf8fefa",
     "https://explore.testnet.tempo.xyz/tx/0x78914d6468910afc5604c985cf98df2c66ec0438ae2f24c0e8346a79abf8fefa"),
]


def solana_sales(exclude: set[str]) -> list[dict]:
    rows = []
    got = solana.rpc("mainnet", "getTokenAccountsByOwner",
                     [solana.MERCHANT, {"mint": solana.USDC["mainnet"]}, {"encoding": "jsonParsed"}]) or {}
    for acct in (a["pubkey"] for a in got.get("value", [])):
        for sig in solana.signatures_for("mainnet", acct, limit=1000):
            if sig.get("err") is not None:
                continue
            tx = solana.transaction("mainnet", sig["signature"])
            time.sleep(0.15)  # public RPCs rate-limit
            if not tx:
                continue
            memo = solana.memo_of(tx)
            paid = solana._usdc_delta(tx, solana.MERCHANT, solana.USDC["mainnet"])
            payer = str(((tx.get("transaction") or {}).get("message") or {}).get("accountKeys", [{}])[0].get("pubkey", ""))
            if paid <= 0 or not memo.startswith("knos:") or payer in exclude:
                continue
            rows.append({"chain": "Solana", "when": datetime.fromtimestamp(sig.get("blockTime") or 0, timezone.utc)
                         .strftime("%Y-%m-%d"), "amount": paid, "unit": "USDC", "plan": memo.split(":")[1],
                         "link": solana.explorer_tx("mainnet", sig["signature"])})
    return rows


def tempo_sales(exclude: set[str]) -> list[dict]:
    rows = []
    head = tempo.block_number("mainnet")
    tokens = {a.lower(): name for name, a in tempo.TOKENS["mainnet"].items()}
    frm = TEMPO_START_BLOCK
    while frm <= head:
        to = min(frm + TEMPO_CHUNK, head)
        logs = tempo.rpc("mainnet", "eth_getLogs", [{
            "fromBlock": hex(frm), "toBlock": hex(to), "address": list(tempo.TOKENS["mainnet"].values()),
            "topics": [tempo.TOPIC_TRANSFER_WITH_MEMO, None, tempo._topic_addr(tempo.MERCHANT)]}]) or []
        for log in logs:
            memo = tempo.memo_text(log["topics"][3])
            payer = "0x" + log["topics"][1][-40:]
            if not memo.startswith("knos:") or payer.lower() in exclude:
                continue
            block = tempo.rpc("mainnet", "eth_getBlockByNumber", [log["blockNumber"], False]) or {}
            rows.append({"chain": "Tempo", "when": datetime.fromtimestamp(int(block.get("timestamp", "0x0"), 16),
                                                                         timezone.utc).strftime("%Y-%m-%d"),
                         "amount": int(log.get("data") or "0x0", 16) / 10 ** tempo.DECIMALS,
                         "unit": tokens.get(str(log.get("address", "")).lower(), "?"), "plan": memo.split(":")[1],
                         "link": tempo.explorer_tx("mainnet", log["transactionHash"])})
        frm = to + 1
        time.sleep(0.1)
    return rows


def render(rows: list[dict], problems: list[str]) -> str:
    total = sum(r["amount"] for r in rows)
    out = ["# Traction", "",
           f"Written by `python scripts/traction.py` on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
           "from public chain data only. Re-run it to check.", "",
           f"Knos receives at `{solana.MERCHANT}` (Solana, USDC `{solana.USDC['mainnet']}`) and "
           f"`{tempo.MERCHANT}` (Tempo, USDC.e and pathUSD). A payment counts when it carries a `knos:` memo.", "",
           f"**{len(rows)} paid licence(s), {total:g} in stablecoins.**", ""]
    if rows:
        out += ["| date | chain | amount | plan | transaction |", "|---|---|---|---|---|"]
        out += [f"| {r['when']} | {r['chain']} | {r['amount']:g} {r['unit']} | {r['plan']} | [view]({r['link']}) |"
                for r in sorted(rows, key=lambda r: r["when"])]
    else:
        out.append("No paying users yet. This page says so rather than leave the number out.")
    if problems:
        out += ["", "Could not read: " + "; ".join(problems)]
    out += ["", "## Test payments (test networks, faucet funds; not revenue)", "",
            "| network | what | transaction |", "|---|---|---|"]
    out += [f"| {net} | {what} | [{tx[:10]}...]({link}) |" for net, what, tx, link in TEST_PAYMENTS]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude", action="append", default=[], help="a payer address to leave out (repeatable)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "TRACTION.md"))
    a = ap.parse_args()
    exclude = {x.lower() for x in a.exclude} | set(a.exclude)
    rows, problems = [], []
    for name, fn in (("Solana", solana_sales), ("Tempo", tempo_sales)):
        try:
            rows += fn(exclude)
        except (OSError, ValueError) as why:
            problems.append(f"{name} ({why})")
    Path(a.out).write_text(render(rows, problems), encoding="utf-8")
    print(f"{len(rows)} payment(s){'; problems: ' + '; '.join(problems) if problems else ''}; wrote {a.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
