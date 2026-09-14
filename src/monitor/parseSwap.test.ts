import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import type { TargetSwap } from '../types.js';

// config.ts validates the environment at import time, so it must be populated
// before the module graph is pulled in.
process.env.RPC_HTTP_URL ??= 'https://example.invalid';
process.env.RPC_WS_URL ??= 'wss://example.invalid';
process.env.TARGET_WALLET ??= 'GDfnEsia2WLAW5t8yx2X5j2mkfA74i5kwGdDuZHt7XmG';
process.env.WALLET_PRIVATE_KEY ??= '4'.repeat(88);
process.env.LOG_LEVEL ??= 'error';

const TARGET = 'GDfnEsia2WLAW5t8yx2X5j2mkfA74i5kwGdDuZHt7XmG';
const STRANGER = 'HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH';
const MEME = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';
const OTHER_TOKEN = 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263';
const SOL = 1_000_000_000;
const FEE = 5_000;

let parseSwap: (tx: never, sig: string, owner: string) => TargetSwap | null;
let WSOL_MINT: string;

before(async () => {
  ({ parseSwap } = await import('./parseSwap.js'));
  ({ WSOL_MINT } = await import('../types.js'));
});

function bal(mint: string, owner: string, amount: number, decimals = 6, accountIndex = 1) {
  return {
    accountIndex,
    mint,
    owner,
    programId: 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
    uiTokenAmount: {
      amount: String(Math.round(amount * 10 ** decimals)),
      decimals,
      uiAmount: amount,
      uiAmountString: String(amount),
    },
  };
}

type Bal = ReturnType<typeof bal>;

function tx(opts: {
  keys: string[];
  preBalances: number[];
  postBalances: number[];
  fee?: number;
  pre?: Bal[];
  post?: Bal[];
  err?: unknown;
}) {
  return {
    slot: 1000,
    blockTime: 1_700_000_000,
    transaction: {
      message: { staticAccountKeys: opts.keys.map((k) => ({ toBase58: () => k })) },
    },
    meta: {
      err: opts.err ?? null,
      fee: opts.fee ?? FEE,
      preBalances: opts.preBalances,
      postBalances: opts.postBalances,
      preTokenBalances: opts.pre ?? [],
      postTokenBalances: opts.post ?? [],
      loadedAddresses: { writable: [], readonly: [] },
    },
  } as never;
}

describe('parseSwap', () => {
  test('detects a SOL-funded buy and excludes the fee from the amount spent', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [2 * SOL, 0],
        postBalances: [1.5 * SOL - FEE, 0],
        post: [bal(MEME, TARGET, 1_000_000)],
      }),
      'sig-buy',
      TARGET,
    );

    assert.ok(r, 'expected a swap');
    assert.equal(r.direction, 'buy');
    assert.equal(r.mint, MEME);
    assert.equal(r.tokenAmount, 1_000_000);
    // 0.5 SOL, not 0.500005 — the network fee is not part of the position cost.
    assert.ok(Math.abs(r.solAmount - 0.5) < 1e-6, `got ${r.solAmount}`);
  });

  test('detects a full exit and reports sellFraction 1', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [1 * SOL, 0],
        postBalances: [1.8 * SOL - FEE, 0],
        pre: [bal(MEME, TARGET, 1_000_000)],
        post: [bal(MEME, TARGET, 0)],
      }),
      'sig-sell',
      TARGET,
    );

    assert.ok(r);
    assert.equal(r.direction, 'sell');
    assert.equal(r.sellFraction, 1);
    assert.ok(Math.abs(r.solAmount - 0.8) < 1e-6, `got ${r.solAmount}`);
  });

  test('reports the correct fraction on a partial exit', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [1 * SOL, 0],
        postBalances: [1.2 * SOL - FEE, 0],
        pre: [bal(MEME, TARGET, 1_000_000)],
        post: [bal(MEME, TARGET, 750_000)],
      }),
      'sig-partial',
      TARGET,
    );

    assert.ok(r);
    assert.equal(r.direction, 'sell');
    assert.ok(Math.abs((r.sellFraction ?? 0) - 0.25) < 1e-9, `got ${r.sellFraction}`);
  });

  test('ignores balance changes belonging to other wallets in the same transaction', () => {
    const r = parseSwap(
      tx({
        keys: [STRANGER, TARGET],
        preBalances: [5 * SOL, 1 * SOL],
        postBalances: [4 * SOL, 1 * SOL],
        post: [bal(MEME, STRANGER, 999)],
      }),
      'sig-stranger',
      TARGET,
    );

    assert.equal(r, null);
  });

  test('counts a WSOL leg as the SOL side when native balance barely moves', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [1 * SOL, 0],
        postBalances: [1 * SOL - FEE, 0],
        pre: [bal(WSOL_MINT, TARGET, 2.0, 9)],
        post: [bal(WSOL_MINT, TARGET, 1.7, 9), bal(MEME, TARGET, 500_000)],
      }),
      'sig-wsol',
      TARGET,
    );

    assert.ok(r);
    assert.equal(r.direction, 'buy');
    assert.equal(r.mint, MEME, 'WSOL must not be reported as the position');
    assert.ok(Math.abs(r.solAmount - 0.3) < 1e-6, `got ${r.solAmount}`);
  });

  test('ignores a plain SOL transfer with no token leg', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'friend'],
        preBalances: [2 * SOL, 0],
        postBalances: [1 * SOL - FEE, 1 * SOL],
      }),
      'sig-transfer',
      TARGET,
    );

    assert.equal(r, null);
  });

  test('skips token-to-token swaps rather than misreading the SOL side', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [1 * SOL, 0],
        postBalances: [1 * SOL - FEE, 0],
        pre: [bal(MEME, TARGET, 1_000_000)],
        post: [bal(MEME, TARGET, 0), bal(OTHER_TOKEN, TARGET, 42)],
      }),
      'sig-t2t',
      TARGET,
    );

    assert.equal(r, null);
  });

  test('never produces a signal from a failed transaction', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [2 * SOL, 0],
        postBalances: [1.5 * SOL, 0],
        post: [bal(MEME, TARGET, 1_000_000)],
        err: { InstructionError: [0, 'Custom'] },
      }),
      'sig-failed',
      TARGET,
    );

    assert.equal(r, null);
  });

  test('treats a rent-sized SOL movement as noise, not a trade', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [1 * SOL, 0],
        postBalances: [1 * SOL - FEE - 200_000, 0],
        post: [bal(MEME, TARGET, 100)],
      }),
      'sig-dust',
      TARGET,
    );

    assert.equal(r, null);
  });

  test('sums the same mint held across multiple token accounts', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [2 * SOL, 0],
        postBalances: [1.5 * SOL - FEE, 0],
        post: [bal(MEME, TARGET, 600_000, 6, 1), bal(MEME, TARGET, 400_000, 6, 2)],
      }),
      'sig-multi-account',
      TARGET,
    );

    assert.ok(r);
    assert.equal(r.tokenAmount, 1_000_000);
  });

  test('picks the largest token leg when a transaction touches several', () => {
    const r = parseSwap(
      tx({
        keys: [TARGET, 'pool'],
        preBalances: [2 * SOL, 0],
        postBalances: [1.5 * SOL - FEE, 0],
        post: [bal(MEME, TARGET, 1_000_000), bal(OTHER_TOKEN, TARGET, 5)],
      }),
      'sig-multi-leg',
      TARGET,
    );

    assert.ok(r);
    assert.equal(r.mint, MEME);
  });
});
