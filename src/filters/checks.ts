import { PublicKey } from '@solana/web3.js';
import { connection, withRetry } from '../rpc.js';
import { config } from '../config.js';
import { getQuote, priceImpactBps } from '../exec/jupiter.js';
import { WSOL_MINT, type FilterResult, type TargetSwap } from '../types.js';

/** Mints we never treat as a "trade" — moving between these is treasury management. */
export const QUOTE_MINTS = new Set<string>([
  WSOL_MINT,
  'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', // USDC
  'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', // USDT
  'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So', // mSOL
  'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn', // jitoSOL
]);

const ok = (name: string, detail: string): FilterResult => ({ name, passed: true, detail });
const no = (name: string, detail: string): FilterResult => ({ name, passed: false, detail });

export function checkBlocklist(swap: TargetSwap): FilterResult {
  const name = 'blocklist';
  return config.blocklist.has(swap.mint)
    ? no(name, 'mint is blocklisted')
    : ok(name, 'not blocklisted');
}

export function checkTradeableMint(swap: TargetSwap): FilterResult {
  const name = 'tradeable-mint';
  return QUOTE_MINTS.has(swap.mint)
    ? no(name, 'quote/stable asset, not a position')
    : ok(name, 'is a position asset');
}

export function checkConviction(swap: TargetSwap): FilterResult {
  const name = 'target-conviction';
  if (swap.solAmount < config.MIN_TARGET_BUY_SOL) {
    return no(
      name,
      `target risked only ${swap.solAmount.toFixed(4)} SOL (< ${config.MIN_TARGET_BUY_SOL}) — likely a test buy`,
    );
  }
  return ok(name, `target risked ${swap.solAmount.toFixed(4)} SOL`);
}

/**
 * A live mint authority means the deployer can print more supply and dilute
 * you to zero; a live freeze authority means they can freeze your token
 * account so you can never sell. Both are cheap, decisive checks.
 */
export async function checkMintAuthorities(swap: TargetSwap): Promise<FilterResult> {
  const name = 'mint-authorities';
  if (!config.REQUIRE_NULL_MINT_AUTHORITY && !config.REQUIRE_NULL_FREEZE_AUTHORITY) {
    return ok(name, 'check disabled');
  }

  const info = await withRetry(`getParsedAccountInfo ${swap.mint}`, () =>
    connection.getParsedAccountInfo(new PublicKey(swap.mint)),
  );
  const data = info.value?.data;
  if (!data || !('parsed' in data)) return no(name, 'could not read mint account');

  const parsedInfo = (data.parsed as { info?: Record<string, unknown> }).info ?? {};
  const mintAuthority = parsedInfo['mintAuthority'] as string | null | undefined;
  const freezeAuthority = parsedInfo['freezeAuthority'] as string | null | undefined;

  if (config.REQUIRE_NULL_MINT_AUTHORITY && mintAuthority) {
    return no(name, `mint authority still live (${mintAuthority.slice(0, 8)}…) — supply can be inflated`);
  }
  if (config.REQUIRE_NULL_FREEZE_AUTHORITY && freezeAuthority) {
    return no(name, `freeze authority still live (${freezeAuthority.slice(0, 8)}…) — your account can be frozen`);
  }
  return ok(name, 'mint and freeze authorities revoked');
}

/**
 * Top-holder concentration. Note this counts AMM pool accounts, which
 * legitimately hold a large share, so the threshold should stay lenient —
 * this catches "one wallet owns 90% and can dump on you", not fine detail.
 */
export async function checkHolderConcentration(swap: TargetSwap): Promise<FilterResult> {
  const name = 'holder-concentration';
  if (config.MAX_TOP10_HOLDER_PCT >= 100) return ok(name, 'check disabled');

  const mint = new PublicKey(swap.mint);
  const [largest, supply] = await Promise.all([
    withRetry(`getTokenLargestAccounts ${swap.mint}`, () => connection.getTokenLargestAccounts(mint)),
    withRetry(`getTokenSupply ${swap.mint}`, () => connection.getTokenSupply(mint)),
  ]);

  const total = supply.value.uiAmount ?? 0;
  if (total <= 0) return no(name, 'supply reported as zero');

  const top10 = largest.value
    .slice(0, 10)
    .reduce((sum, a) => sum + (a.uiAmount ?? 0), 0);
  const pct = (top10 / total) * 100;

  return pct > config.MAX_TOP10_HOLDER_PCT
    ? no(name, `top 10 hold ${pct.toFixed(1)}% (> ${config.MAX_TOP10_HOLDER_PCT}%)`)
    : ok(name, `top 10 hold ${pct.toFixed(1)}%`);
}

/**
 * Price impact on the size *we* intend to trade — not the target's size. A
 * route that was fine for their 0.5 SOL can still be fine for our 0.05, but
 * a thin pool shows up here before we commit.
 */
export async function checkPriceImpact(swap: TargetSwap, lamportsIn: bigint): Promise<FilterResult> {
  const name = 'price-impact';
  const quote = await getQuote({
    inputMint: WSOL_MINT,
    outputMint: swap.mint,
    amount: lamportsIn,
  });
  if (!quote) return no(name, 'no buy route available');

  const impact = priceImpactBps(quote);
  return impact > config.MAX_PRICE_IMPACT_BPS
    ? no(name, `${impact.toFixed(0)}bps impact (> ${config.MAX_PRICE_IMPACT_BPS}bps)`)
    : ok(name, `${impact.toFixed(0)}bps impact`);
}

/**
 * The honeypot check: ask Jupiter to route the position back out to SOL
 * before we buy in. A token you can enter but not exit is the single most
 * expensive thing a copy bot can walk into, and it is cheap to rule out.
 */
export async function checkExitRoute(swap: TargetSwap, lamportsIn: bigint): Promise<FilterResult> {
  const name = 'exit-route';
  if (!config.REQUIRE_EXIT_ROUTE) return ok(name, 'check disabled');

  const inbound = await getQuote({
    inputMint: WSOL_MINT,
    outputMint: swap.mint,
    amount: lamportsIn,
  });
  if (!inbound) return no(name, 'no buy route, so no exit to test');

  const outbound = await getQuote({
    inputMint: swap.mint,
    outputMint: WSOL_MINT,
    amount: BigInt(inbound.outAmount),
  });
  if (!outbound) return no(name, 'position cannot be routed back to SOL — treat as unsellable');

  // Round-tripping always loses money to fees and spread. A catastrophic
  // round-trip loss is the signature of a high sell-tax or trap token.
  const backOut = Number(outbound.outAmount);
  const wentIn = Number(lamportsIn);
  const retained = (backOut / wentIn) * 100;

  return retained < 70
    ? no(name, `round trip returns only ${retained.toFixed(1)}% — likely sell tax or trap`)
    : ok(name, `round trip returns ${retained.toFixed(1)}%`);
}
