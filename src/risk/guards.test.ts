import { test, describe, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LAMPORTS_PER_SOL, Keypair } from '@solana/web3.js';

const stateDir = mkdtempSync(join(tmpdir(), 'copybot-guards-'));

process.env.RPC_HTTP_URL ??= 'https://example.invalid';
process.env.RPC_WS_URL ??= 'wss://example.invalid';
process.env.TARGET_WALLET ??= 'GDfnEsia2WLAW5t8yx2X5j2mkfA74i5kwGdDuZHt7XmG';
process.env.WALLET_PRIVATE_KEY ??= '4'.repeat(88);
process.env.LOG_LEVEL ??= 'error';
process.env.STATE_FILE = join(stateDir, 'state.json');
process.env.COPY_MODE = 'fixed';
process.env.COPY_FIXED_SOL = '0.05';
process.env.MAX_TRADE_SOL = '0.1';
process.env.DAILY_SPEND_LIMIT_SOL = '0.3';
process.env.DAILY_LOSS_LIMIT_SOL = '0.2';
process.env.MAX_OPEN_POSITIONS = '2';
process.env.MIN_SOL_RESERVE = '0.03';
process.env.MINT_COOLDOWN_MS = '60000';

const MEME = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';
const MEME2 = 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263';

type Guards = typeof import('./guards.js');
type StoreMod = typeof import('../state/store.js');

let guards: Guards;
let StoreClass: StoreMod['Store'];
let setBalance: (sol: number) => void;
const wallet = Keypair.generate().publicKey;

before(async () => {
  const rpc = await import('../rpc.js');
  let balanceSol = 10;
  setBalance = (sol) => {
    balanceSol = sol;
  };
  // Stub the one network read sizeBuy performs, so cap logic is tested in isolation.
  rpc.connection.getBalance = async () => Math.round(balanceSol * LAMPORTS_PER_SOL);

  guards = await import('./guards.js');
  ({ Store: StoreClass } = await import('../state/store.js'));
});

function swap(overrides: Partial<import('../types.js').TargetSwap> = {}) {
  return {
    signature: 'sig',
    slot: 1,
    blockTime: null,
    direction: 'buy' as const,
    mint: MEME,
    decimals: 6,
    solAmount: 1.0,
    tokenAmount: 1_000_000,
    ...overrides,
  };
}

describe('sizeBuy caps', () => {
  let store: InstanceType<StoreMod['Store']>;

  beforeEach(() => {
    store = new StoreClass();
    setBalance(10);
  });

  test('uses the fixed copy size regardless of how much the target spent', async () => {
    const d = await guards.sizeBuy({ swap: swap({ solAmount: 50 }), store, wallet });
    assert.equal(d.allowed, true);
    assert.equal(d.solAmount, 0.05);
    assert.equal(d.lamports, BigInt(0.05 * LAMPORTS_PER_SOL));
  });

  test('clamps to MAX_TRADE_SOL in proportional mode', async () => {
    const { config } = await import('../config.js');
    const original = config.COPY_MODE;
    // @ts-expect-error -- deliberately exercising the clamp path
    config.COPY_MODE = 'proportional';
    try {
      const d = await guards.sizeBuy({ swap: swap({ solAmount: 40 }), store, wallet });
      assert.equal(d.allowed, true);
      assert.equal(d.solAmount, 0.1, 'must not exceed the per-trade cap');
      assert.match(d.reason, /per-trade cap/);
    } finally {
      // @ts-expect-error -- restoring test mutation
      config.COPY_MODE = original;
    }
  });

  test('refuses to trade once the daily loss limit is hit', async () => {
    store.day.realizedPnl = -0.25; // limit is 0.2
    const d = await guards.sizeBuy({ swap: swap(), store, wallet });
    assert.equal(d.allowed, false);
    assert.match(d.reason, /daily loss limit/);
  });

  test('refuses to trade once the daily spend limit is reached', async () => {
    store.day.solSpent = 0.3; // limit is 0.3
    const d = await guards.sizeBuy({ swap: swap(), store, wallet });
    assert.equal(d.allowed, false);
    assert.match(d.reason, /daily spend limit/);
  });

  test('clamps the last trade of the day to the remaining budget', async () => {
    store.day.solSpent = 0.28; // 0.02 left, under the 0.05 fixed size
    const d = await guards.sizeBuy({ swap: swap(), store, wallet });
    assert.equal(d.allowed, true);
    assert.ok(Math.abs(d.solAmount - 0.02) < 1e-9, `got ${d.solAmount}`);
    assert.match(d.reason, /remaining daily budget/);
  });

  test('rejects a new mint once max open positions is reached', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.05, tokenAmountRaw: 1n, signature: 'a' });
    await store.recordBuy({ mint: MEME2, decimals: 6, solSpent: 0.05, tokenAmountRaw: 1n, signature: 'b' });

    const d = await guards.sizeBuy({ swap: swap({ mint: 'NEWMINT1111111111111111111111111111111111111' }), store, wallet });
    assert.equal(d.allowed, false);
    assert.match(d.reason, /max 2/);
  });

  test('still allows adding to a position we already hold at the cap', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.05, tokenAmountRaw: 1n, signature: 'a' });
    await store.recordBuy({ mint: MEME2, decimals: 6, solSpent: 0.05, tokenAmountRaw: 1n, signature: 'b' });
    store.day.solSpent = 0;

    // Take the cooldown out of the picture so the position cap is what is
    // actually under test here.
    const { config } = await import('../config.js');
    const original = config.MINT_COOLDOWN_MS;
    // @ts-expect-error -- deliberately isolating one guard
    config.MINT_COOLDOWN_MS = 0;
    try {
      const d = await guards.sizeBuy({ swap: swap({ mint: MEME }), store, wallet });
      assert.equal(d.allowed, true, `unexpectedly rejected: ${d.reason}`);
    } finally {
      // @ts-expect-error -- restoring test mutation
      config.MINT_COOLDOWN_MS = original;
    }
  });

  test('respects the per-mint cooldown', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.05, tokenAmountRaw: 1n, signature: 'a' });
    const d = await guards.sizeBuy({ swap: swap({ mint: MEME }), store, wallet });
    assert.equal(d.allowed, false);
    assert.match(d.reason, /cooldown/);
  });

  test('never spends into the fee reserve', async () => {
    setBalance(0.06); // reserve is 0.03, so 0.03 spendable
    const d = await guards.sizeBuy({ swap: swap(), store, wallet });
    assert.equal(d.allowed, true);
    assert.ok(d.solAmount <= 0.03 + 1e-9, `got ${d.solAmount}`);
    assert.match(d.reason, /available balance minus reserve/);
  });

  test('refuses to trade when the balance is below the reserve', async () => {
    setBalance(0.02); // under the 0.03 reserve
    const d = await guards.sizeBuy({ swap: swap(), store, wallet });
    assert.equal(d.allowed, false);
    assert.match(d.reason, /fee reserve/);
  });

  test('refuses a trade sized below the viable minimum', async () => {
    setBalance(0.031); // 0.001 spendable, under the 0.005 floor
    const d = await guards.sizeBuy({ swap: swap(), store, wallet });
    assert.equal(d.allowed, false);
    assert.match(d.reason, /viable minimum/);
  });
});

describe('sizeSell', () => {
  test('mirrors a full exit as a full exit', () => {
    const out = guards.sizeSell({ swap: { ...swap(), sellFraction: 1 }, heldRaw: 1_000_000n });
    assert.equal(out, 1_000_000n);
  });

  test('treats a 99.6% exit as a full exit rather than leaving dust', () => {
    const out = guards.sizeSell({ swap: { ...swap(), sellFraction: 0.996 }, heldRaw: 1_000_000n });
    assert.equal(out, 1_000_000n);
  });

  test('mirrors a half exit proportionally against our own holding', () => {
    // Our position size is unrelated to theirs; only the fraction carries over.
    const out = guards.sizeSell({ swap: { ...swap(), sellFraction: 0.5 }, heldRaw: 777n });
    assert.equal(out, 388n);
  });

  test('defaults to a full exit when the fraction is unknown', () => {
    const out = guards.sizeSell({ swap: swap(), heldRaw: 500n });
    assert.equal(out, 500n);
  });

  test('never sells more than we hold', () => {
    const out = guards.sizeSell({ swap: { ...swap(), sellFraction: 0.9 }, heldRaw: 100n });
    assert.ok(out <= 100n);
  });
});
