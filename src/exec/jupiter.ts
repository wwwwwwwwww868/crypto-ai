import { config } from '../config.js';
import { log } from '../logger.js';

export interface JupiterQuote {
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;
  otherAmountThreshold: string;
  priceImpactPct: string;
  slippageBps: number;
  routePlan: { swapInfo: { label?: string } }[];
  [k: string]: unknown;
}

class JupiterError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'JupiterError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${config.JUPITER_BASE_URL}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    signal: AbortSignal.timeout(15_000),
  });
  const body = await res.text();
  if (!res.ok) {
    throw new JupiterError(`${init?.method ?? 'GET'} ${path} -> ${res.status}: ${body.slice(0, 400)}`, res.status);
  }
  try {
    return JSON.parse(body) as T;
  } catch {
    throw new JupiterError(`${path} returned non-JSON: ${body.slice(0, 200)}`);
  }
}

/**
 * Fetch a route. Returns null when no route exists, which is meaningful
 * rather than exceptional — an unroutable token is a token we cannot exit.
 */
export async function getQuote(args: {
  inputMint: string;
  outputMint: string;
  /** Base units of inputMint. */
  amount: bigint;
  slippageBps?: number;
}): Promise<JupiterQuote | null> {
  const params = new URLSearchParams({
    inputMint: args.inputMint,
    outputMint: args.outputMint,
    amount: args.amount.toString(),
    slippageBps: String(args.slippageBps ?? config.SLIPPAGE_BPS),
    swapMode: 'ExactIn',
    onlyDirectRoutes: 'false',
  });

  try {
    const quote = await request<JupiterQuote>(`/quote?${params.toString()}`);
    if (!quote.outAmount || quote.outAmount === '0') return null;
    return quote;
  } catch (err) {
    if (err instanceof JupiterError && err.status === 400) {
      // Jupiter answers "no route" with a 400.
      log.debug(`no route ${args.inputMint} -> ${args.outputMint}`);
      return null;
    }
    throw err;
  }
}

export interface BuiltSwap {
  /** Base64 versioned transaction, unsigned. */
  swapTransaction: string;
  lastValidBlockHeight: number | null;
}

/** Builds the unsigned swap transaction for us to sign locally. */
export async function buildSwapTransaction(args: {
  quote: JupiterQuote;
  userPublicKey: string;
  priorityFeeLamports: number;
}): Promise<BuiltSwap> {
  const res = await request<{ swapTransaction: string; lastValidBlockHeight?: number }>('/swap', {
    method: 'POST',
    body: JSON.stringify({
      quoteResponse: args.quote,
      userPublicKey: args.userPublicKey,
      wrapAndUnwrapSol: true,
      dynamicComputeUnitLimit: true,
      prioritizationFeeLamports: args.priorityFeeLamports,
    }),
  });
  if (!res.swapTransaction) throw new JupiterError('/swap returned no transaction');
  return {
    swapTransaction: res.swapTransaction,
    lastValidBlockHeight: res.lastValidBlockHeight ?? null,
  };
}

export function priceImpactBps(quote: JupiterQuote): number {
  const pct = Number(quote.priceImpactPct);
  return Number.isFinite(pct) ? Math.abs(pct) * 10_000 : Number.POSITIVE_INFINITY;
}

export function routeLabels(quote: JupiterQuote): string[] {
  return quote.routePlan.map((r) => r.swapInfo.label ?? '?');
}
