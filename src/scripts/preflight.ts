import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LAMPORTS_PER_SOL, type Keypair } from '@solana/web3.js';
import { connection } from '../rpc.js';
import { config } from '../config.js';
import { log } from '../logger.js';
import { loadWallet } from '../wallet.js';
import { getQuote, priceImpactBps } from '../exec/jupiter.js';
import { priorityFeeLamports } from '../exec/executor.js';
import { WSOL_MINT } from '../types.js';

const USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';

export interface PreflightReport {
  ok: boolean;
  failures: string[];
  balanceSol: number;
}

/**
 * Validates the whole path the bot depends on before it is allowed to trade:
 * RPC reachability, the key we will sign with, the balance we will spend, and
 * a real Jupiter quote round trip. A bot that discovers a broken RPC or a
 * malformed key mid-trade discovers it with money in flight.
 */
export async function preflight(wallet: Keypair): Promise<PreflightReport> {
  const failures: string[] = [];
  let balanceSol = 0;

  log.info('── preflight ──');

  try {
    const version = await connection.getVersion();
    log.info(`RPC reachable (solana-core ${version['solana-core']})`);
  } catch (err) {
    failures.push(`RPC unreachable at ${config.RPC_HTTP_URL}: ${String(err)}`);
  }

  try {
    const slot = await connection.getSlot('confirmed');
    log.info(`current slot ${slot}`);
  } catch (err) {
    failures.push(`could not read slot: ${String(err)}`);
  }

  log.info(`trading wallet ${wallet.publicKey.toBase58()}`);
  log.info(`copying target ${config.targetWallet.toBase58()}`);

  if (wallet.publicKey.equals(config.targetWallet)) {
    failures.push('trading wallet and target wallet are the same address — the bot would copy itself');
  }

  try {
    const lamports = await connection.getBalance(wallet.publicKey, 'confirmed');
    balanceSol = lamports / LAMPORTS_PER_SOL;
    log.info(`balance ${balanceSol.toFixed(6)} SOL`);

    const needed = config.MIN_SOL_RESERVE + config.COPY_FIXED_SOL;
    if (balanceSol < needed) {
      failures.push(
        `balance ${balanceSol.toFixed(4)} SOL cannot fund one trade plus the fee reserve (needs ~${needed.toFixed(4)} SOL)`,
      );
    }
  } catch (err) {
    failures.push(`could not read wallet balance: ${String(err)}`);
  }

  try {
    const sigs = await connection.getSignaturesForAddress(config.targetWallet, { limit: 1 }, 'confirmed');
    if (sigs.length === 0) {
      failures.push('target wallet has no transaction history — check the address is right');
    } else {
      const age = sigs[0]!.blockTime ? Date.now() / 1000 - sigs[0]!.blockTime! : null;
      log.info(
        `target last active ${age === null ? 'unknown' : `${(age / 3600).toFixed(1)}h ago`}`,
      );
    }
  } catch (err) {
    failures.push(`could not read target history: ${String(err)}`);
  }

  try {
    const probe = await getQuote({
      inputMint: WSOL_MINT,
      outputMint: USDC,
      amount: BigInt(Math.floor(0.01 * LAMPORTS_PER_SOL)),
    });
    if (!probe) failures.push('Jupiter returned no route for a SOL→USDC probe — API may be down or blocked');
    else log.info(`Jupiter reachable (probe impact ${priceImpactBps(probe).toFixed(0)}bps)`);
  } catch (err) {
    failures.push(`Jupiter quote API failed: ${String(err)}`);
  }

  log.info(
    `priority fee ${priorityFeeLamports()} lamports/tx | slippage ${config.SLIPPAGE_BPS}bps | mode ${config.COPY_MODE}`,
  );
  log.info(
    `caps: ${config.MAX_TRADE_SOL} SOL/trade, ${config.DAILY_SPEND_LIMIT_SOL} SOL/day spend, ${config.DAILY_LOSS_LIMIT_SOL} SOL/day loss, ${config.MAX_OPEN_POSITIONS} open`,
  );

  if (failures.length === 0) log.info('── preflight passed ──');
  return { ok: failures.length === 0, failures, balanceSol };
}

// Runnable on its own via `npm run preflight`.
const invokedDirectly =
  process.argv[1] !== undefined &&
  resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  const report = await preflight(loadWallet());
  if (!report.ok) {
    log.error('preflight FAILED:');
    for (const f of report.failures) log.error(`  - ${f}`);
    process.exit(1);
  }
  process.exit(0);
}
