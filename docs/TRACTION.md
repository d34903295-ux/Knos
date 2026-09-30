# Traction

Written by `python scripts/traction.py` on 2026-09-29 21:59 UTC from public chain data only. Re-run it to check.

Knos receives at `CVhqj6hcugFDKZxzSkiUG64cRbHn2kguZy9n6zv47FqL` (Solana, USDC `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`) and `0x580C2842ec71C432D5d2fc4dDCDf37CE493FbB3A` (Tempo, USDC.e and pathUSD). A payment counts when it carries a `knos:` memo.

**0 paid licence(s), 0 in stablecoins.**

No paying users yet. This page says so rather than leave the number out.

## Test payments (test networks, faucet funds; not revenue)

| network | what | transaction |
|---|---|---|
| Solana devnet | `knos pro buy --network devnet`, paid with USDC carrying the Solana Pay reference and the `knos:` memo, found by its reference with `knos pro check`, Pro activated | [5RMh9rdVLA...](https://explorer.solana.com/tx/5RMh9rdVLAGgusVnD9xtUmGyawk19KSr3sB5o16FfFpKK2UAHcPWcndhiG6TNbou7vSCGqqBtM8RpGQ1QwfZ3ZZs?cluster=devnet) |
| Tempo Moderato testnet | `knos pro buy --chain tempo --network testnet`, paid with transferWithMemo, found on chain by `knos pro check`, Pro activated | [0x51fcead0...](https://explore.testnet.tempo.xyz/tx/0x51fcead0856fa21b4543ef344422687c2d7d0e74fd0e1932c229c8ecfe95e51a) |
| Tempo Moderato testnet | an agent's own budget wallet paying an MPP API (0.01 each, cap 0.05) with `knos pay`; 5 paid, the 6th refused by Knos before signing | [0x78914d64...](https://explore.testnet.tempo.xyz/tx/0x78914d6468910afc5604c985cf98df2c66ec0438ae2f24c0e8346a79abf8fefa) |
