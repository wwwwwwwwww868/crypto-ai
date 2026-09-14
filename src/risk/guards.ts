import { LAMPORTS_PER_SOL, type PublicKey } from '@solana/web3.js';
import { connection, withRetry } from '../rpc.js';
import { config } from '../config.js';
import type { Store } from '../state/store.js';
import type { TargetSwap } from '../types.js';

export interface SizingDecision {
  allowed: boolean;
  /** SOL to spend, after every cap has been applied. */
  solAmount: number;
  lamports: bigint;
  reason: string;
}

/**
 * Decides whether we may buy at all, and for how much.
 *
 * Every limit here is applied as a clamp on the way down, never as a reason
 * to scale up. The order matters: we reject outright before we size, so a
 * tripped loss limit cannot be turned into a smaller trade.
 */
export async function sizeBuy(args: {
  swap: TargetSwap;
  store: Store;
  wallet: PublicKey;
}): Promise<SizingDecision> {
  const { swap, store, wallet } = args;
  const deny = (reason: string): SizingDecision => ({
    allowed: false,
    solAmount: 0,
    lamports: 0n,
    reason,
  });

  const day = store.day;

  if (day.realizedPnl <= -config.DAILY_LOSS_LIMIT_SOL) {
    return deny(
      `daily loss limit hit (${day.realizedPnl.toFixed(4)} SOL <= -${config.DAILY_LOSS_LIMIT_SOL}) — halted until UTC rollover`,
    );
  }
  if (day.solSpent >= config.DAILY_SPEND_LIMIT_SOL) {
    return deny(
      `daily spend limit reached (${day.solSpent.toFixed(4)} / ${config.DAILY_SPEND_LIMIT_SOL} SOL)`,
    );
  }

  const alreadyHeld = store.getPosition(swap.mint);
  if (!alreadyHeld && store.openCount() >= config.MAX_OPEN_POSITIONS) {
    return deny(`already holding ${store.openCount()} positions (max ${config.MAX_OPEN_POSITIONS})`);
  }

  const sinceLastBuy = Date.now() - store.lastBuyAt(swap.mint);
  if (sinceLastBuy < config.MINT_COOLDOWN_MS) {
    return deny(
      `cooldown active for this mint (${Math.ceil((config.MINT_COOLDOWN_MS - sinceLastBuy) / 1000)}s left)`,
    );
  }

  const balanceLamports = await withRetry('getBalance', () => connection.getBalance(wallet, 'confirmed'));
  const balance = balanceLamports / LAMPORTS_PER_SOL;
  const spendable = balance - config.MIN_SOL_RESERVE;
  if (spendable <= 0) {
    return deny(
      `balance ${balance.toFixed(4)} SOL is at or below the ${config.MIN_SOL_RESERVE} SOL fee reserve`,
    );
  }

  let desired =
    config.COPY_MODE === 'proportional'
      ? swap.solAmount * config.COPY_RATIO
      : config.COPY_FIXED_SOL;

  const clamps: string[] = [];
  const clamp = (limit: number, label: string) => {
    if (desired > limit) {
      desired = limit;
      clamps.push(label);
    }
  };

  clamp(config.MAX_TRADE_SOL, 'per-trade cap');
  clamp(config.DAILY_SPEND_LIMIT_SOL - day.solSpent, 'remaining daily budget');
  clamp(spendable, 'available balance minus reserve');

  // Below this a swap costs more in fees and impact than it can plausibly return.
  const MIN_VIABLE_SOL = 0.005;
  if (desired < MIN_VIABLE_SOL) {
    return deny(`sized down to ${desired.toFixed(5)} SOL, below the ${MIN_VIABLE_SOL} SOL viable minimum`);
  }

  const lamports = BigInt(Math.floor(desired * LAMPORTS_PER_SOL));
  const reason = clamps.length ? `sized to ${desired.toFixed(4)} SOL (limited by ${clamps.join(', ')})` : `sized to ${desired.toFixed(4)} SOL`;

  return { allowed: true, solAmount: desired, lamports, reason };
}

/** How much of a held position to sell when the target sells theirs. */
export function sizeSell(args: { swap: TargetSwap; heldRaw: bigint }): bigint {
  const fraction = args.swap.sellFraction ?? 1;
  if (fraction >= 0.995) return args.heldRaw; // treat a near-full exit as a full one
  const scaled = (args.heldRaw * BigInt(Math.round(fraction * 10_000))) / 10_000n;
  return scaled > args.heldRaw ? args.heldRaw : scaled;
}
