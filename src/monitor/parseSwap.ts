import type {
  ParsedTransactionWithMeta,
  TokenBalance,
  VersionedTransactionResponse,
} from '@solana/web3.js';
import { LAMPORTS_PER_SOL } from '@solana/web3.js';
import { WSOL_MINT, type TargetSwap } from '../types.js';
import { log } from '../logger.js';

/** Below this, a SOL movement is rent/fee noise rather than a trade leg. */
const SOL_DUST = 0.001;

interface MintDelta {
  mint: string;
  decimals: number;
  pre: number;
  post: number;
  delta: number;
}

function collectMintDeltas(
  pre: TokenBalance[] | null | undefined,
  post: TokenBalance[] | null | undefined,
  owner: string,
): Map<string, MintDelta> {
  const byMint = new Map<string, MintDelta>();

  const touch = (mint: string, decimals: number): MintDelta => {
    let entry = byMint.get(mint);
    if (!entry) {
      entry = { mint, decimals, pre: 0, post: 0, delta: 0 };
      byMint.set(mint, entry);
    }
    return entry;
  };

  // A wallet can hold the same mint across several token accounts, so sum
  // rather than assuming one account per mint.
  for (const b of pre ?? []) {
    if (b.owner !== owner) continue;
    touch(b.mint, b.uiTokenAmount.decimals).pre += b.uiTokenAmount.uiAmount ?? 0;
  }
  for (const b of post ?? []) {
    if (b.owner !== owner) continue;
    touch(b.mint, b.uiTokenAmount.decimals).post += b.uiTokenAmount.uiAmount ?? 0;
  }
  for (const entry of byMint.values()) entry.delta = entry.post - entry.pre;

  return byMint;
}

function nativeSolDelta(
  tx: VersionedTransactionResponse | ParsedTransactionWithMeta,
  owner: string,
): number {
  const meta = tx.meta;
  if (!meta) return 0;

  let keys: string[];
  try {
    const message = tx.transaction.message as {
      getAccountKeys?: (a: unknown) => { get(i: number): { toBase58(): string } | undefined; length: number };
      accountKeys?: unknown[];
      staticAccountKeys?: { toBase58(): string }[];
    };
    if (typeof message.getAccountKeys === 'function') {
      const ak = message.getAccountKeys({ accountKeysFromLookups: meta.loadedAddresses });
      keys = Array.from({ length: ak.length }, (_, i) => ak.get(i)?.toBase58() ?? '');
    } else if (message.staticAccountKeys) {
      keys = message.staticAccountKeys.map((k) => k.toBase58());
    } else {
      keys = (message.accountKeys ?? []).map((k: unknown) => {
        const entry = k as { pubkey?: { toBase58(): string }; toBase58?: () => string };
        return entry.pubkey?.toBase58() ?? entry.toBase58?.() ?? '';
      });
    }
  } catch (err) {
    log.debug('could not resolve account keys for native delta', String(err));
    return 0;
  }

  const idx = keys.indexOf(owner);
  if (idx < 0) return 0;

  const pre = meta.preBalances[idx];
  const post = meta.postBalances[idx];
  if (pre === undefined || post === undefined) return 0;

  // The fee comes out of the payer's balance and is not part of the trade,
  // so add it back to avoid overstating what they spent.
  const feeAdjustment = idx === 0 ? meta.fee : 0;
  return (post - pre + feeAdjustment) / LAMPORTS_PER_SOL;
}

/**
 * Reconstruct a swap from balance deltas rather than by decoding program
 * instructions. This is deliberate: it works for Raydium, Orca, Meteora,
 * pump.fun, Jupiter and anything that ships next week, because all of them
 * ultimately move tokens in and out of the wallet.
 */
export function parseSwap(
  tx: VersionedTransactionResponse | ParsedTransactionWithMeta,
  signature: string,
  owner: string,
): TargetSwap | null {
  const meta = tx.meta;
  if (!meta || meta.err) return null;

  const deltas = collectMintDeltas(meta.preTokenBalances, meta.postTokenBalances, owner);

  const wsol = deltas.get(WSOL_MINT);
  const solSide = nativeSolDelta(tx, owner) + (wsol?.delta ?? 0);

  const tokenLegs = [...deltas.values()].filter(
    (d) => d.mint !== WSOL_MINT && Math.abs(d.delta) > 0,
  );
  if (tokenLegs.length === 0) return null;

  // Token-for-token swaps have no SOL leg to size a copy against. Skipping
  // them is a real limitation, not an oversight — see README.
  if (Math.abs(solSide) < SOL_DUST) {
    log.debug(`skipping ${signature.slice(0, 8)}: no SOL leg (token-to-token or transfer)`);
    return null;
  }

  const direction: 'buy' | 'sell' = solSide < 0 ? 'buy' : 'sell';

  // Pick the leg that moved in the direction consistent with the SOL side.
  const candidates = tokenLegs.filter((d) => (direction === 'buy' ? d.delta > 0 : d.delta < 0));
  if (candidates.length === 0) return null;

  candidates.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  const leg = candidates[0]!;

  if (candidates.length > 1) {
    log.debug(
      `${signature.slice(0, 8)}: ${candidates.length} token legs, using largest (${leg.mint})`,
    );
  }

  const swap: TargetSwap = {
    signature,
    slot: tx.slot,
    blockTime: tx.blockTime ?? null,
    direction,
    mint: leg.mint,
    decimals: leg.decimals,
    solAmount: Math.abs(solSide),
    tokenAmount: Math.abs(leg.delta),
  };

  if (direction === 'sell' && leg.pre > 0) {
    swap.sellFraction = Math.min(1, Math.abs(leg.delta) / leg.pre);
  }

  return swap;
}
