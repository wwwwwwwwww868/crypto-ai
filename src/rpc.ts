import { Connection } from '@solana/web3.js';
import { config } from './config.js';

export const connection = new Connection(config.RPC_HTTP_URL, {
  commitment: 'confirmed',
  wsEndpoint: config.RPC_WS_URL,
  confirmTransactionInitialTimeout: config.CONFIRM_TIMEOUT_MS,
});

/** Retry wrapper for RPC reads. Providers rate-limit; a miss should not kill the bot. */
export async function withRetry<T>(
  label: string,
  fn: () => Promise<T>,
  attempts = 3,
  baseDelayMs = 400,
): Promise<T> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (i === attempts - 1) break;
      await new Promise((r) => setTimeout(r, baseDelayMs * 2 ** i));
    }
  }
  throw new Error(`${label} failed after ${attempts} attempts: ${String(lastError)}`);
}
