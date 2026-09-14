import { Keypair, VersionedTransaction, LAMPORTS_PER_SOL } from '@solana/web3.js';
import { connection, withRetry } from '../rpc.js';
import { config } from '../config.js';
import { log } from '../logger.js';
import { buildSwapTransaction, getQuote, priceImpactBps, routeLabels, type JupiterQuote } from './jupiter.js';
import { parseSwap } from '../monitor/parseSwap.js';
import { WSOL_MINT } from '../types.js';

/** Typical compute units for a Jupiter route; used to price the priority fee. */
const ESTIMATED_COMPUTE_UNITS = 200_000;

export interface FillResult {
  signature: string;
  /** SOL actually spent (buy) or received (sell), measured from the landed tx. */
  solAmount: number;
  /** Token base units actually received (buy) or sent (sell). */
  tokenAmountRaw: bigint;
  slot: number;
}

export function priorityFeeLamports(): number {
  const fromRate = (config.PRIORITY_FEE_MICROLAMPORTS * ESTIMATED_COMPUTE_UNITS) / 1_000_000;
  const cap = config.MAX_PRIORITY_FEE_SOL * LAMPORTS_PER_SOL;
  return Math.max(0, Math.floor(Math.min(fromRate, cap)));
}

export class DryRunStop extends Error {
  constructor(readonly quote: JupiterQuote) {
    super('DRY_RUN: stopped before broadcast');
    this.name = 'DryRunStop';
  }
}

/**
 * Quote, sign, broadcast and confirm one swap, then read the landed
 * transaction back to learn what actually filled. We record measured amounts
 * rather than quoted ones — slippage means the quote is a prediction and the
 * confirmed transaction is the fact.
 */
export async function executeSwap(args: {
  wallet: Keypair;
  inputMint: string;
  outputMint: string;
  amountIn: bigint;
  label: string;
}): Promise<FillResult> {
  const { wallet, inputMint, outputMint, amountIn, label } = args;

  const quote = await getQuote({ inputMint, outputMint, amount: amountIn });
  if (!quote) throw new Error(`${label}: no route ${inputMint} -> ${outputMint}`);

  const impact = priceImpactBps(quote);
  log.info(
    `${label}: route ${routeLabels(quote).join(' > ')} | impact ${impact.toFixed(0)}bps | out ${quote.outAmount}`,
  );

  if (config.DRY_RUN) {
    log.warn(`${label}: DRY_RUN set, not broadcasting`);
    throw new DryRunStop(quote);
  }

  const built = await buildSwapTransaction({
    quote,
    userPublicKey: wallet.publicKey.toBase58(),
    priorityFeeLamports: priorityFeeLamports(),
  });

  const tx = VersionedTransaction.deserialize(Buffer.from(built.swapTransaction, 'base64'));
  tx.sign([wallet]);

  const signature = await withRetry(
    `${label} send`,
    () =>
      connection.sendRawTransaction(tx.serialize(), {
        skipPreflight: false,
        maxRetries: 3,
        preflightCommitment: 'confirmed',
      }),
    2,
  );
  log.info(`${label}: sent ${signature}`);

  const blockhash = tx.message.recentBlockhash;
  const lastValidBlockHeight =
    built.lastValidBlockHeight ?? (await connection.getBlockHeight('confirmed')) + 150;

  const confirmation = await connection.confirmTransaction(
    { signature, blockhash, lastValidBlockHeight },
    'confirmed',
  );
  if (confirmation.value.err) {
    throw new Error(`${label}: transaction failed on chain: ${JSON.stringify(confirmation.value.err)}`);
  }

  const landed = await withRetry(`${label} fetch landed tx`, async () => {
    const t = await connection.getTransaction(signature, {
      maxSupportedTransactionVersion: 0,
      commitment: 'confirmed',
    });
    if (!t) throw new Error('not yet indexed');
    return t;
  }, 5, 700);

  const measured = parseSwap(landed, signature, wallet.publicKey.toBase58());
  const nonSolMint = inputMint === WSOL_MINT ? outputMint : inputMint;

  if (!measured || measured.mint !== nonSolMint) {
    // The swap confirmed, so we own the outcome; we just could not measure it
    // from balance deltas. Fall back to the quote so the position is still
    // tracked instead of silently lost.
    log.warn(`${label}: could not measure fill from ${signature}, falling back to quote amounts`);
    const quotedIn = BigInt(quote.inAmount);
    const quotedOut = BigInt(quote.outAmount);
    return {
      signature,
      slot: landed.slot,
      solAmount:
        inputMint === WSOL_MINT
          ? Number(quotedIn) / LAMPORTS_PER_SOL
          : Number(quotedOut) / LAMPORTS_PER_SOL,
      tokenAmountRaw: inputMint === WSOL_MINT ? quotedOut : quotedIn,
    };
  }

  const tokenAmountRaw = BigInt(
    Math.round(measured.tokenAmount * 10 ** measured.decimals),
  );

  return {
    signature,
    slot: landed.slot,
    solAmount: measured.solAmount,
    tokenAmountRaw,
  };
}
