# Solana copy-trading bot

Watches one Solana wallet and mirrors its swaps from a wallet you control,
filtering out trades that fail a set of safety checks. Execution goes through
Jupiter, so it routes across Raydium, Orca, Meteora, pump.fun and the rest.

It copies. It does not originate trades, pick tokens, or predict anything.

---

## Read this before you run it

**Trust Wallet has no trading API.** It is non-custodial, so the only way any
bot can trade for you is by holding a private key and signing transactions
itself. There is no read-only or permissioned mode.

**So do not put your main Trust Wallet seed phrase into this bot.** A seed
phrase in a `.env` file can move every token in that wallet, and it is one
`cat`, one screenshot, one stray `git add -A` away from being somewhere you
did not intend. Instead:

```bash
npm run new-wallet          # generates a fresh dedicated keypair
```

Fund that address from Trust Wallet with an amount you are willing to lose in
full, and keep everything else in the wallet the bot cannot touch. The bot's
worst case is then capped at what you deliberately sent it.

### What copy-trading actually is

You are always later than the person you copy:

- They transact first. You see it once it is confirmed, then you send your own
  transaction. You buy into the price their buy already moved.
- The same applies to exits — they are out, then you sell into a book their
  sell already thinned.
- If someone's wallet is well known, late copiers are the liquidity that
  wallet's exits are sold into. That is a business model, and copy bots are
  the customer.

None of this is fixed by better code, and this bot does not claim to fix it.
What the filters do is narrow it to trades that are at least structurally
survivable. Run it small, for long enough to have real data, before you decide
the target wallet is worth copying at all.

---

## Setup

```bash
npm install
cp .env.example .env
```

Fill in `.env`. The three that must be right:

| Variable | What it is |
| --- | --- |
| `RPC_HTTP_URL` / `RPC_WS_URL` | Your RPC provider. Public endpoints are rate-limited and **will** make you miss trades. Helius, Triton and QuickNode all work. |
| `TARGET_WALLET` | The address you are copying. Public data, read-only. |
| `WALLET_PRIVATE_KEY` | Your dedicated bot wallet, base58. From `npm run new-wallet`. |

Then check everything is wired up before it can trade:

```bash
npm run preflight
```

That validates RPC reachability, your key, your balance, that the target
actually has history, and a live Jupiter quote round trip. Run it, get a clean
pass, then:

```bash
npm run dev          # or: npm run build && npm start
```

`Ctrl-C` shuts down cleanly and reports open positions.

---

## How a trade is handled

```
target wallet transaction
        │
        ▼
  parse balance deltas ────────► not a swap → ignore
        │
        ▼
   buy or sell?
        │
   buy ─┴─ sell → do we hold it? ──no──► ignore
        │              │
        │             yes → mirror the same % exit
        ▼
    risk guards ──────────► over a cap → skip, logged
        │
        ▼
   filter pipeline ───────► fails a check → skip, logged with reason
        │
        ▼
   Jupiter swap → confirm → read the landed tx → record the real fill
```

### Detection

Swaps are reconstructed from **token balance deltas**, not by decoding
individual DEX program instructions. Every venue ultimately moves tokens in and
out of the wallet, so this works across all of them — including ones that
launch after this was written. Native SOL and wrapped SOL are treated as the
same side of the trade.

Two independent paths feed detection: a logs subscription for speed, and a
signature sweep every 20s for correctness. Solana WebSocket subscriptions die
quietly, and a copy bot that silently stopped seeing trades is worse than one
that crashed.

### Filters

Ordered cheapest-first, stopping at the first rejection — latency here is
entry price.

| Check | Rejects |
| --- | --- |
| `blocklist` | Mints you listed in `BLOCKLIST_MINTS` |
| `tradeable-mint` | SOL/USDC/USDT/mSOL/jitoSOL — treasury moves, not positions |
| `target-conviction` | Their buy was under `MIN_TARGET_BUY_SOL`; likely a test buy |
| `mint-authorities` | Mint authority live (supply can be inflated) or freeze authority live (your account can be frozen so you can never sell) |
| `holder-concentration` | Top 10 holders exceed `MAX_TOP10_HOLDER_PCT` |
| `price-impact` | Impact on **your** size exceeds `MAX_PRICE_IMPACT_BPS` |
| `exit-route` | No route back to SOL, or a round trip returns under 70% — the honeypot / sell-tax check |

A check that throws counts as a rejection, not a pass. Skipping a doubtful
entry costs far less than entering something unsellable.

### Risk guards

Applied before sizing, and only ever downward:

| Variable | Effect |
| --- | --- |
| `MAX_TRADE_SOL` | Hard per-trade ceiling |
| `DAILY_SPEND_LIMIT_SOL` | Total SOL deployable per UTC day |
| `DAILY_LOSS_LIMIT_SOL` | Realized losses past this halt buying until UTC rollover |
| `MAX_OPEN_POSITIONS` | Concurrent position cap (adding to a held position is still allowed) |
| `MIN_SOL_RESERVE` | Never spent, so you can always afford the fee on your own exit |
| `MINT_COOLDOWN_MS` | Per-mint cooldown, so one frantic wallet cannot chain-buy the same token |

Signals are processed strictly one at a time. Handling them concurrently would
let two trades each read the same remaining daily budget and both pass — which
is how a cap gets exceeded while appearing to hold.

### Sizing

- `COPY_MODE=fixed` — always `COPY_FIXED_SOL`, whatever they spent. Predictable.
- `COPY_MODE=proportional` — `their SOL × COPY_RATIO`, then clamped by every
  cap above. A whale's 400 SOL buy becomes your `MAX_TRADE_SOL`, not your
  whole balance.

Exits mirror their **fraction**, not their amount: they sell 40% of their bag,
you sell 40% of yours. A sell of 99.5%+ is treated as a full exit so you are
not left holding dust.

---

## State and restarts

State lives in `STATE_FILE` (default `./data/state.json`): open positions,
handled signatures, and the day's ledger.

- A buy is persisted to disk **before** the bot moves on. A position held only
  in memory is one it forgets on restart, and a forgotten position never gets
  its exit mirrored.
- On startup, if positions are open, recent target history is inspected rather
  than blindly skipped — an exit that happened while the bot was down gets
  **replayed**, so you are not left holding a bag the target already sold.
- An unreadable state file stops startup rather than silently starting empty.
  The corrupt file is preserved as `.corrupt-<timestamp>` so you can recover
  which positions were open.
- One instance per state file, enforced with a lock. Two bots sharing one state
  file each enforce the daily caps against their own view, so real spend would
  be double what either believes it allowed.

---

## Known limits

Things this does not do, stated plainly:

- **Token-to-token swaps are skipped.** With no SOL leg there is nothing to
  size a copy against. Logged at `debug`.
- **`holder-concentration` counts AMM pool accounts**, which legitimately hold
  a large share. It catches "one wallet owns 90% and can dump on you", not fine
  distinctions. Keep the threshold lenient.
- **No MEV protection.** Transactions go through the public mempool and can be
  sandwiched. A private relay (Jito) would help and is not wired up.
- **No stop-loss or take-profit.** Exits happen when the target exits. If they
  stop trading while you hold their token, you hold it until you sell manually.
- **`exit-route` is a point-in-time check.** Liquidity that exists when you buy
  can be pulled afterwards.
- **Latency is not competitive with colocated bots.** A logs subscription plus
  `getTransaction` is seconds behind a Geyser/Yellowstone feed.

## Commands

```bash
npm run new-wallet   # generate a dedicated trading keypair
npm run preflight    # validate config, RPC, key, balance, Jupiter
npm run dev          # run from source
npm run build        # compile to dist/
npm start            # run compiled
npm test             # 44 tests: parser, risk caps, accounting, locking
npm run typecheck
```

Setting `DRY_RUN=true` runs the full pipeline — detection, filters, sizing,
routing — and stops before broadcasting. Useful for watching what it *would*
do against a target you are still evaluating.

## Layout

```
src/
  index.ts              wiring, signal handling, graceful shutdown
  config.ts             env parsing, validation, cross-field sanity checks
  wallet.ts             keypair loading (base58 or BIP39 mnemonic)
  monitor/
    watcher.ts          logs subscription + backfill sweep + restart reconcile
    parseSwap.ts        swap reconstruction from balance deltas
  filters/
    checks.ts           individual safety checks
    index.ts            cheapest-first pipeline
  exec/
    jupiter.ts          quote and swap API client
    executor.ts         sign, broadcast, confirm, measure the real fill
  risk/
    guards.ts           caps and position sizing
  state/
    store.ts            durable positions, ledger, dedupe
    lock.ts             single-instance enforcement
  scripts/
    preflight.ts        pre-arm validation
    newWallet.ts        dedicated keypair generator
```

## Licence / disclaimer

This is trading software that spends real money automatically. It is provided
as-is, with no guarantee of profit, correctness, or uptime. You are responsible
for the keys you give it, the wallet you point it at, and every trade it makes.
