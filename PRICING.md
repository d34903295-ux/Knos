# Pricing

## Jobs

**5% of the price, only when the buyer accepts** (or the review window passes in silence). Nothing on a rejection or a
refund, nothing to post, nothing to work. The fee is set in the escrow program and contract, and is the same for
everyone. Details and measured chain fees: [docs/ECONOMICS.md](docs/ECONOMICS.md). Every $12 of fees a buyer pays buys
them a month of Sibyl Pro (simulated in 0.3.1).

## Coding-team plans (unchanged)

| plan | who | Knos part | Sibyl Pro part (paid to Sibyl directly, at Sibyl's list price; skipped if you already have Pro) | total per 30 days / per year |
|---|---|---|---|---|
| **Free** (MIT), forever | solo, offline; team registries of up to 3 keys; any public open-source repo | 0 | Sibyl's free tier (5 MB) | 0 |
| **Pro** | individuals | 10 / 100 USDC | 12 / 108 USD | **22 / 208** (10 / 100 if you already have Sibyl Pro) |
| **Team** | private registries with 4+ keys | 20 USDC per seat | 12 USD per seat | **32 per seat** (20 if the seat already has Sibyl Pro) |

What each plan includes:

- **Free:**
  - shared Sibyl memory built from your agents' past sessions and your commits;
  - file claims and the edit guard in every host, and the commit guard;
  - a team registry on Solana of up to 3 keys, with no server;
  - the Python SDK.
- **Pro adds:**
  - the spend meter across Claude Code and Codex, and one cap across agents;
  - agent budgets enforced by the chain (Tempo Keychain limits per period, Solana delegates);
  - agent wallets for MPP and x402 payments.
- **Team adds:**
  - chain budgets for every agent;
  - private registries past 3 keys, and a records policy;
  - priority fixes.
  - `knos serve` (0.2's self-hosted server) is still included for teams that prefer one.

Sibyl Pro is what unlocks `knos learn` and `knos lint` (Sibyl's self-learning and memory linter) and removes Sibyl's
5 MB cap. **Works with Sibyl Pro, bought in the same command:**

1. `knos pro buy` takes the Knos payment.
2. It asks Sibyl for your tier (Sibyl's own `sibyl status`, or, with your consent, Sibyl's access endpoint with your
   own credentials).
3. If Sibyl says you are on the free tier, it runs Sibyl's own `sibyl upgrade`, which opens Sibyl's checkout (card,
   or USDC on Base).

Two payments, one command. Pro or Staker means the Sibyl part is skipped, at purchase and at every Knos renewal, so
nobody pays Sibyl twice. Sibyl also grants Pro to holders of 100,000 $SIBYL (see Sibyl's docs); Knos detects that
tier and does not charge for Sibyl.

For comparison (read 30 Sep 2026): Sibyl Pro alone is $12, Conductor Teams $60 per user (a different product),
Portkey $49 and Helicone $79 a month.

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
