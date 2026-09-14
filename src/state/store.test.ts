import { test, describe, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const stateDir = mkdtempSync(join(tmpdir(), 'copybot-store-'));

process.env.RPC_HTTP_URL ??= 'https://example.invalid';
process.env.RPC_WS_URL ??= 'wss://example.invalid';
process.env.TARGET_WALLET ??= 'GDfnEsia2WLAW5t8yx2X5j2mkfA74i5kwGdDuZHt7XmG';
process.env.WALLET_PRIVATE_KEY ??= '4'.repeat(88);
process.env.LOG_LEVEL ??= 'error';
process.env.STATE_FILE = join(stateDir, 'state.json');

const MEME = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';

type StoreMod = typeof import('./store.js');
let StoreClass: StoreMod['Store'];

before(async () => {
  ({ Store: StoreClass } = await import('./store.js'));
});

describe('Store position accounting', () => {
  let store: InstanceType<StoreMod['Store']>;

  beforeEach(() => {
    store = new StoreClass();
  });

  test('records a buy as an open position', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });

    const p = store.getPosition(MEME);
    assert.ok(p);
    assert.equal(p.tokenAmountRaw, '1000000');
    assert.equal(p.solSpent, 0.1);
    assert.equal(store.openCount(), 1);
    assert.equal(store.day.buys, 1);
    assert.equal(store.day.solSpent, 0.1);
  });

  test('averages into an existing position on a second buy', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.2, tokenAmountRaw: 500_000n, signature: 'b' });

    const p = store.getPosition(MEME);
    assert.ok(p);
    assert.equal(p.tokenAmountRaw, '1500000');
    assert.ok(Math.abs(p.solSpent - 0.3) < 1e-9);
    assert.deepEqual(p.entrySignatures, ['a', 'b']);
    assert.equal(store.openCount(), 1, 'adding to a position must not open a second one');
  });

  test('closes the position and books profit on a full exit', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });
    await store.recordSell({ mint: MEME, solReceived: 0.25, tokenAmountRaw: 1_000_000n });

    assert.equal(store.getPosition(MEME), undefined);
    assert.equal(store.openCount(), 0);
    assert.ok(Math.abs(store.day.realizedPnl - 0.15) < 1e-9, `got ${store.day.realizedPnl}`);
    assert.equal(store.day.sells, 1);
  });

  test('books a loss on a full exit below cost', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });
    await store.recordSell({ mint: MEME, solReceived: 0.04, tokenAmountRaw: 1_000_000n });

    assert.ok(Math.abs(store.day.realizedPnl - -0.06) < 1e-9, `got ${store.day.realizedPnl}`);
  });

  test('attributes cost basis proportionally on a partial exit', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });
    // Sell a quarter for 0.05 SOL. Basis on that quarter is 0.025, so +0.025.
    await store.recordSell({ mint: MEME, solReceived: 0.05, tokenAmountRaw: 250_000n });

    const p = store.getPosition(MEME);
    assert.ok(p, 'position must stay open after a partial exit');
    assert.equal(p.tokenAmountRaw, '750000');
    assert.ok(Math.abs(p.solSpent - 0.075) < 1e-9, `remaining basis ${p.solSpent}`);
    assert.ok(Math.abs(store.day.realizedPnl - 0.025) < 1e-9, `pnl ${store.day.realizedPnl}`);
  });

  test('two partial exits sum to the same pnl as one full exit', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });
    await store.recordSell({ mint: MEME, solReceived: 0.06, tokenAmountRaw: 500_000n });
    await store.recordSell({ mint: MEME, solReceived: 0.06, tokenAmountRaw: 500_000n });

    assert.equal(store.getPosition(MEME), undefined);
    assert.ok(Math.abs(store.day.realizedPnl - 0.02) < 1e-9, `got ${store.day.realizedPnl}`);
  });

  test('clamps an oversized sell to what we actually hold', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });
    await store.recordSell({ mint: MEME, solReceived: 0.2, tokenAmountRaw: 9_999_999n });

    assert.equal(store.getPosition(MEME), undefined, 'should close, not go negative');
    assert.ok(Math.abs(store.day.realizedPnl - 0.1) < 1e-9);
  });

  test('ignores a sell for a position we never held', async () => {
    await store.recordSell({ mint: MEME, solReceived: 5, tokenAmountRaw: 1n });

    assert.equal(store.day.sells, 0);
    assert.equal(store.day.realizedPnl, 0, 'phantom sells must not inflate pnl');
  });

  test('deduplicates signatures across restarts', async () => {
    store.markSeen('sig-1');
    assert.equal(store.isSeen('sig-1'), true);
    assert.equal(store.isSeen('sig-2'), false);

    await store.save();

    const reloaded = new StoreClass();
    await reloaded.load();
    assert.equal(reloaded.isSeen('sig-1'), true, 'seen signatures must survive a restart');
  });

  test('restores open positions after a restart', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1_000_000n, signature: 'a' });
    await store.save();

    const reloaded = new StoreClass();
    await reloaded.load();
    const p = reloaded.getPosition(MEME);
    assert.ok(p, 'a restart must not orphan a position we are holding');
    assert.equal(p.tokenAmountRaw, '1000000');
  });

  test('resets the daily ledger when the UTC date changes', async () => {
    await store.recordBuy({ mint: MEME, decimals: 6, solSpent: 0.1, tokenAmountRaw: 1n, signature: 'a' });
    assert.equal(store.day.buys, 1);

    // Simulate yesterday's ledger still being in memory at rollover.
    store.day.date = '2000-01-01';
    const day = store.day;
    assert.equal(day.buys, 0, 'a new day starts with fresh limits');
    assert.equal(day.solSpent, 0);
    assert.equal(day.realizedPnl, 0);
    assert.equal(store.openCount(), 1, 'rollover must not discard open positions');
  });
});
