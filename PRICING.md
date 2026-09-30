# Pricing

| plan | price | what you get |
|---|---|---|
| **Free** (MIT), forever | $0 | Shared Sibyl memory built from your agents' past sessions and your commits; file claims and the edit guard; every host, every repo; local only |
| **Pro** | **10 USDC per 30 days**, or **100 USDC per year**, per developer | Everything in Free, plus: the spend meter across Claude Code and Codex (`knos spend`); one hard cap across tokens and agents' API payments (`knos budget`); agent budget wallets on Solana and Tempo (`knos budget fund`, `knos pay`); spend on `knos board`. 14-day trial, no signup. 30-day money-back guarantee |
| **Team** | 20 USDC per seat per 30 days, 3 seats minimum | Pro for each seat, plus `knos serve`: a server you host that shares claims, notes and one pooled spend cap across every machine and cloud agent that joins (`knos init --remote <url> --token <seat token>`). A claim on one machine blocks an edit on another |

## Paying

No account and no card. `knos pro buy` prints a payment request and watches the chain for it:

- **Solana**: a Solana Pay link for USDC, payable from any Solana wallet (Phantom, Solflare, Backpack), from any
  country.
- **Tempo**: a `transferWithMemo` of USDC.e or pathUSD to `0x580C2842ec71C432D5d2fc4dDCDf37CE493FbB3A`, with the
  memo `knos pro buy` prints: `knos pro buy --chain tempo`.

The CLI checks the payment against the chain itself, over a public RPC:
- it succeeded;
- it carries this purchase's reference or memo;
- the Knos address received at least the price in an accepted stablecoin.

It then writes `~/.knos/licence.json`. Every payment is public, and [TRACTION.md](docs/TRACTION.md) counts them from
the chain.

Paying another way (card, bank)? Write to the author for a signed licence code: `knos pro activate <code>`.

## Money-back

Within 30 days, send the transaction and the address to refund to. The refund goes back on the same chain.

## Source

Everything outside `src/knos/pro/` is MIT. `src/knos/pro/` is source-available under
[FSL-1.1-MIT](src/knos/pro/LICENSE): read it, run it, change it, just don't sell a competing product with it. Each
release becomes MIT two years after it ships.
