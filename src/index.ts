import type { Keypair } from '@solana/web3.js';
import { config } from './config.js';
import { log } from './logger.js';
import { loadWallet } from './wallet.js';
import { Store } from './state/store.js';
import { acquireLock, InstanceLockError } from './state/lock.js';
import { primeSeen, watchTarget, type Watcher } from './monitor/watcher.js';
import { screenBuy, logVerdict } from './filters/index.js';
import { sizeBuy, sizeSell } from './risk/guards.js';
import { DryRunStop, executeSwap } from './exec/executor.js';
import { WSOL_MINT, type TargetSwap } from './types.js';
import { preflight } from './scripts/preflight.js';

const store = new Store();

/**
 * Signals are handled strictly one at a time. Concurrent handling would let
 * two trades each read the same "remaining daily budget" and both pass, which
 * is exactly how a cap gets silently exceeded.
 */
let chain: Promise<void> = Promise.resolve();
function serialize(work: () => Promise<void>): Promise<void> {
  chain = chain.then(work).catch((err) => log.error('handler error', String(err)));
  return chain;
}

async function handleBuy(wallet: Keypair, swap: TargetSwap): Promise<void> {
  const sizing = await sizeBuy({ swap, store, wallet: wallet.publicKey });
  if (!sizing.allowed) {
    log.info(`↷ skip buy ${swap.mint.slice(0, 8)} — ${sizing.reason}`);
    return;
  }
  log.debug(`${swap.mint.slice(0, 8)}: ${sizing.reason}`);

  const verdict = await screenBuy(swap, sizing.lamports);
  logVerdict(swap, verdict);
  if (!verdict.approved) return;

  try {
    const fill = await executeSwap({
      wallet,
      inputMint: WSOL_MINT,
      outputMint: swap.mint,
      amountIn: sizing.lamports,
      label: `BUY ${swap.mint.slice(0, 8)}`,
    });

    await store.recordBuy({
      mint: swap.mint,
      decimals: swap.decimals,
      solSpent: fill.solAmount,
      tokenAmountRaw: fill.tokenAmountRaw,
      signature: fill.signature,
    });

    log.info(
      `✔ BOUGHT ${swap.mint.slice(0, 8)} for ${fill.solAmount.toFixed(4)} SOL — ${fill.signature}`,
    );
  } catch (err) {
    if (err instanceof DryRunStop) return;
    log.error(`buy failed for ${swap.mint.slice(0, 8)}`, String(err));
  }
}

async function handleSell(wallet: Keypair, swap: TargetSwap): Promise<void> {
  if (!config.MIRROR_EXITS) return;

  const position = store.getPosition(swap.mint);
  if (!position) {
    log.debug(`target sold ${swap.mint.slice(0, 8)} but we hold none`);
    return;
  }

  const heldRaw = BigInt(position.tokenAmountRaw);
  if (heldRaw <= 0n) return;

  const sellRaw = sizeSell({ swap, heldRaw });
  if (sellRaw <= 0n) return;

  const pct = ((Number(sellRaw) / Number(heldRaw)) * 100).toFixed(1);
  log.info(`mirroring exit: selling ${pct}% of our ${swap.mint.slice(0, 8)} position`);

  try {
    const fill = await executeSwap({
      wallet,
      inputMint: swap.mint,
      outputMint: WSOL_MINT,
      amountIn: sellRaw,
      label: `SELL ${swap.mint.slice(0, 8)}`,
    });

    await store.recordSell({
      mint: swap.mint,
      solReceived: fill.solAmount,
      tokenAmountRaw: sellRaw,
    });

    log.info(
      `✔ SOLD ${swap.mint.slice(0, 8)} for ${fill.solAmount.toFixed(4)} SOL — ${fill.signature}`,
    );
  } catch (err) {
    if (err instanceof DryRunStop) return;
    // A failed exit is the dangerous case: we still hold the token and the
    // target is already out. Surface it loudly rather than logging at debug.
    log.error(
      `EXIT FAILED for ${swap.mint.slice(0, 8)} — position still open, may need manual sale`,
      String(err),
    );
  }
}

async function main(): Promise<void> {
  log.info(config.DRY_RUN ? 'starting in DRY_RUN mode' : 'starting in LIVE mode — real funds at risk');

  let wallet: Keypair;
  try {
    wallet = loadWallet();
  } catch (err) {
    log.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }

  // Take the lock before doing any work, so a second instance fails fast
  // rather than after a round of network calls.
  let releaseLock: () => Promise<void>;
  try {
    releaseLock = await acquireLock(config.STATE_FILE);
  } catch (err) {
    if (err instanceof InstanceLockError) {
      log.error(err.message);
      process.exit(1);
    }
    throw err;
  }

  const report = await preflight(wallet);
  if (!report.ok) {
    log.error('preflight failed, refusing to start:');
    for (const f of report.failures) log.error(`  - ${f}`);
    await releaseLock();
    process.exit(1);
  }

  await store.load();

  const primed = await primeSeen({
    markSeen: (sig) => store.markSeen(sig),
    isSeen: (sig) => store.isSeen(sig),
    heldMints: Object.keys(store.positions),
    onMissedExit: (swap) => handleSell(wallet, swap),
  });
  await store.save();
  log.info(
    `primed ${primed.markedSeen}/${primed.scanned} historical signature(s)` +
      (primed.replayedExits ? `, replayed ${primed.replayedExits} missed exit(s)` : ''),
  );

  const watcher: Watcher = await watchTarget({
    onSwap: (swap) =>
      serialize(async () => {
        store.rolloverDay();
        if (swap.direction === 'buy') await handleBuy(wallet, swap);
        else await handleSell(wallet, swap);
      }),
    isSeen: (sig) => store.isSeen(sig),
    markSeen: (sig) => store.markSeen(sig),
  });

  log.info('armed — watching for trades');

  let shuttingDown = false;
  const shutdown = async (signal: string) => {
    if (shuttingDown) return;
    shuttingDown = true;
    log.info(`${signal} received, shutting down`);
    await watcher.stop();
    await chain.catch(() => {});
    await store.save();
    await releaseLock();

    const day = store.day;
    log.info(
      `session summary — ${day.buys} buys, ${day.sells} sells, spent ${day.solSpent.toFixed(4)} SOL, realized ${day.realizedPnl >= 0 ? '+' : ''}${day.realizedPnl.toFixed(4)} SOL`,
    );
    const open = Object.values(store.positions);
    if (open.length) {
      log.warn(`${open.length} position(s) still open: ${open.map((p) => p.mint.slice(0, 8)).join(', ')}`);
    }
    process.exit(0);
  };

  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));

  // An unhandled rejection in a trading loop means state and reality may have
  // diverged. Log it with full context rather than dying silently.
  process.on('unhandledRejection', (reason) => {
    log.error('unhandled rejection', String(reason));
  });

  // Periodic heartbeat so a silent bot is distinguishable from a dead one.
  setInterval(() => {
    const day = store.day;
    log.info(
      `heartbeat — ${store.openCount()} open, ${day.buys}B/${day.sells}S today, pnl ${day.realizedPnl >= 0 ? '+' : ''}${day.realizedPnl.toFixed(4)} SOL`,
    );
  }, 300_000).unref();
}

main().catch((err) => {
  log.error('fatal', String(err));
  process.exit(1);
});
