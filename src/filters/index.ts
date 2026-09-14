import { log } from '../logger.js';
import type { FilterResult, TargetSwap } from '../types.js';
import {
  checkBlocklist,
  checkConviction,
  checkExitRoute,
  checkHolderConcentration,
  checkMintAuthorities,
  checkPriceImpact,
  checkTradeableMint,
} from './checks.js';

export interface FilterVerdict {
  approved: boolean;
  results: FilterResult[];
  rejectedBy?: string;
  reason?: string;
}

/**
 * Runs the checks cheapest-first and stops at the first rejection, so we do
 * not spend RPC calls and API quota proving a trade we already know we are
 * skipping. Latency matters here: every millisecond is worse entry price.
 */
export async function screenBuy(swap: TargetSwap, lamportsIn: bigint): Promise<FilterVerdict> {
  const results: FilterResult[] = [];

  const stages: (() => FilterResult | Promise<FilterResult>)[] = [
    // Local, zero-cost.
    () => checkBlocklist(swap),
    () => checkTradeableMint(swap),
    () => checkConviction(swap),
    // One RPC call each.
    () => checkMintAuthorities(swap),
    () => checkHolderConcentration(swap),
    // Jupiter round trips, most expensive, most informative.
    () => checkPriceImpact(swap, lamportsIn),
    () => checkExitRoute(swap, lamportsIn),
  ];

  for (const stage of stages) {
    let result: FilterResult;
    try {
      result = await stage();
    } catch (err) {
      // An unreadable check is not a passing check. Copy-trading rewards
      // skipping a doubtful entry far more than it punishes missing one.
      result = { name: 'error', passed: false, detail: `check threw: ${String(err)}` };
    }
    results.push(result);
    if (!result.passed) {
      return { approved: false, results, rejectedBy: result.name, reason: result.detail };
    }
  }

  return { approved: true, results };
}

export function logVerdict(swap: TargetSwap, verdict: FilterVerdict): void {
  const mint = swap.mint.slice(0, 8);
  if (verdict.approved) {
    log.info(`✓ ${mint} approved`, verdict.results.map((r) => `${r.name}: ${r.detail}`).join(' | '));
  } else {
    log.info(`✗ ${mint} skipped — ${verdict.rejectedBy}: ${verdict.reason}`);
  }
}
