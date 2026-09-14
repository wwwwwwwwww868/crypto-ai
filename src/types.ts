export const WSOL_MINT = 'So11111111111111111111111111111111111111112';
export const LAMPORTS_PER_SOL_N = 1_000_000_000;

/** A swap we detected in the target wallet's transaction history. */
export interface TargetSwap {
  signature: string;
  slot: number;
  blockTime: number | null;
  direction: 'buy' | 'sell';
  /** The non-SOL token involved. */
  mint: string;
  /** Token decimals, as reported by the balance record. */
  decimals: number;
  /** SOL the target spent (buy) or received (sell), absolute value. */
  solAmount: number;
  /** Token units the target received (buy) or disposed of (sell), absolute value. */
  tokenAmount: number;
  /** Fraction of their pre-trade token holding they sold. 1 = full exit. Sells only. */
  sellFraction?: number;
}

export interface FilterResult {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Position {
  mint: string;
  decimals: number;
  /** Base units of the token we currently hold from copying. */
  tokenAmountRaw: string;
  /** SOL we put in, cumulative. */
  solSpent: number;
  /** SOL we have taken back out, cumulative. */
  solReceived: number;
  openedAt: number;
  lastUpdatedAt: number;
  entrySignatures: string[];
}

export interface DayLedger {
  /** UTC date, YYYY-MM-DD. */
  date: string;
  solSpent: number;
  solReceived: number;
  realizedPnl: number;
  buys: number;
  sells: number;
}

export interface PersistedState {
  version: 1;
  /** Signatures already handled, so a restart does not re-copy old trades. */
  seenSignatures: string[];
  positions: Record<string, Position>;
  day: DayLedger;
  lastBuyAtByMint: Record<string, number>;
}
