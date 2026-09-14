import { connection, withRetry } from '../rpc.js';
import { config } from '../config.js';
import { log } from '../logger.js';
import { parseSwap } from './parseSwap.js';
import type { TargetSwap } from '../types.js';

const BACKFILL_INTERVAL_MS = 20_000;
const BACKFILL_LIMIT = 20;

export interface Watcher {
  stop(): Promise<void>;
}

/**
 * Watches the target wallet for swaps.
 *
 * Two independent paths feed the same handler:
 *   1. A logs subscription, for latency — this is how we see trades fast.
 *   2. A periodic signature backfill, for correctness — WebSocket
 *      subscriptions on Solana die quietly, and a copy bot that silently
 *      stopped seeing trades is worse than one that crashed.
 *
 * `isSeen`/`markSeen` deduplicate across both paths and across restarts.
 */
export async function watchTarget(opts: {
  onSwap: (swap: TargetSwap) => Promise<void>;
  isSeen: (signature: string) => boolean;
  markSeen: (signature: string) => void;
}): Promise<Watcher> {
  const target = config.targetWallet;
  const targetStr = target.toBase58();
  let stopped = false;

  const handleSignature = async (signature: string, source: 'ws' | 'backfill') => {
    if (stopped || opts.isSeen(signature)) return;
    // Mark before awaiting so the other path cannot pick up the same signature.
    opts.markSeen(signature);

    try {
      const tx = await withRetry(`getTransaction ${signature.slice(0, 8)}`, () =>
        connection.getTransaction(signature, {
          maxSupportedTransactionVersion: 0,
          commitment: 'confirmed',
        }),
      );
      if (!tx) {
        log.debug(`${signature.slice(0, 8)}: transaction not available`);
        return;
      }

      const swap = parseSwap(tx, signature, targetStr);
      if (!swap) return;

      log.info(
        `[${source}] target ${swap.direction.toUpperCase()} ${swap.tokenAmount.toLocaleString(
          undefined,
          { maximumFractionDigits: 4 },
        )} of ${swap.mint} for ${swap.solAmount.toFixed(4)} SOL`,
        { signature },
      );
      await opts.onSwap(swap);
    } catch (err) {
      log.error(`failed handling ${signature.slice(0, 8)}`, String(err));
    }
  };

  let subscriptionId: number | null = null;

  const subscribe = async () => {
    subscriptionId = connection.onLogs(
      target,
      (logs) => {
        if (logs.err) return;
        void handleSignature(logs.signature, 'ws');
      },
      'confirmed',
    );
    log.info(`subscribed to logs for ${targetStr} (id ${subscriptionId})`);
  };

  await subscribe();

  // Backfill sweep. Also acts as a liveness check on the subscription.
  const backfill = setInterval(() => {
    if (stopped) return;
    void (async () => {
      try {
        const sigs = await withRetry('getSignaturesForAddress', () =>
          connection.getSignaturesForAddress(target, { limit: BACKFILL_LIMIT }, 'confirmed'),
        );
        // Oldest first, so positions open before the matching exit is seen.
        for (const s of sigs.reverse()) {
          if (s.err) {
            opts.markSeen(s.signature);
            continue;
          }
          await handleSignature(s.signature, 'backfill');
        }
      } catch (err) {
        log.warn('backfill sweep failed', String(err));
      }
    })();
  }, BACKFILL_INTERVAL_MS);

  return {
    async stop() {
      stopped = true;
      clearInterval(backfill);
      if (subscriptionId !== null) {
        try {
          await connection.removeOnLogsListener(subscriptionId);
        } catch {
          /* connection may already be gone */
        }
      }
      log.info('watcher stopped');
    },
  };
}

export interface PrimeSummary {
  scanned: number;
  markedSeen: number;
  replayedExits: number;
}

/**
 * Seeds the seen-signature set so a fresh start does not copy trades that
 * already happened hours ago.
 *
 * The subtlety is restarts. Blindly marking recent history as seen would also
 * swallow a sell that happened while the bot was down — leaving us holding a
 * token the target has already exited, with nothing left to trigger our own
 * exit. So when we hold positions, recent history is inspected first and any
 * exit from a mint we hold is replayed instead of suppressed.
 */
export async function primeSeen(opts: {
  markSeen: (sig: string) => void;
  isSeen: (sig: string) => boolean;
  heldMints: string[];
  onMissedExit: (swap: TargetSwap) => Promise<void>;
}): Promise<PrimeSummary> {
  const targetStr = config.targetWallet.toBase58();
  const sigs = await withRetry('prime getSignaturesForAddress', () =>
    connection.getSignaturesForAddress(config.targetWallet, { limit: 100 }, 'confirmed'),
  );

  const held = new Set(opts.heldMints);
  const summary: PrimeSummary = { scanned: sigs.length, markedSeen: 0, replayedExits: 0 };

  // Nothing at risk means nothing to reconcile, so skip the per-signature
  // fetches entirely — this is the common cold-start path.
  if (held.size === 0) {
    for (const s of sigs) {
      opts.markSeen(s.signature);
      summary.markedSeen++;
    }
    return summary;
  }

  log.info(`holding ${held.size} position(s) — scanning recent history for exits missed while down`);

  // Oldest first so a partial sell is applied before a later one.
  for (const s of [...sigs].reverse()) {
    if (opts.isSeen(s.signature)) continue;

    if (s.err) {
      opts.markSeen(s.signature);
      summary.markedSeen++;
      continue;
    }

    let swap: TargetSwap | null = null;
    try {
      const tx = await connection.getTransaction(s.signature, {
        maxSupportedTransactionVersion: 0,
        commitment: 'confirmed',
      });
      if (tx) swap = parseSwap(tx, s.signature, targetStr);
    } catch (err) {
      log.warn(`could not inspect ${s.signature.slice(0, 8)} during reconcile`, String(err));
    }

    opts.markSeen(s.signature);
    summary.markedSeen++;

    if (swap && swap.direction === 'sell' && held.has(swap.mint)) {
      log.warn(
        `replaying missed exit: target sold ${swap.mint.slice(0, 8)} while the bot was down`,
      );
      summary.replayedExits++;
      await opts.onMissedExit(swap);
    }
  }

  return summary;
}
