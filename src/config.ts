import 'dotenv/config';
import { z } from 'zod';
import { PublicKey } from '@solana/web3.js';

const bool = (def: boolean) =>
  z
    .string()
    .optional()
    .transform((v) => (v === undefined || v === '' ? def : v.toLowerCase() === 'true'));

const num = (def: number) =>
  z
    .string()
    .optional()
    .transform((v) => (v === undefined || v === '' ? def : Number(v)))
    .pipe(z.number().finite());

const base58Address = z.string().refine(
  (v) => {
    try {
      new PublicKey(v);
      return true;
    } catch {
      return false;
    }
  },
  { message: 'not a valid Solana address' },
);

const schema = z.object({
  RPC_HTTP_URL: z.string().url(),
  RPC_WS_URL: z.string().url(),
  TARGET_WALLET: base58Address,

  WALLET_PRIVATE_KEY: z.string().optional(),
  WALLET_MNEMONIC: z.string().optional(),
  WALLET_DERIVATION_PATH: z.string().optional().default("m/44'/501'/0'/0'"),

  DRY_RUN: bool(false),

  COPY_MODE: z.enum(['fixed', 'proportional']).optional().default('fixed'),
  COPY_FIXED_SOL: num(0.05),
  COPY_RATIO: num(1.0),

  JUPITER_BASE_URL: z.string().url().optional().default('https://lite-api.jup.ag/swap/v1'),

  SLIPPAGE_BPS: num(150),
  PRIORITY_FEE_MICROLAMPORTS: num(200_000),
  MAX_PRIORITY_FEE_SOL: num(0.002),
  CONFIRM_TIMEOUT_MS: num(45_000),

  MAX_TRADE_SOL: num(0.1),
  DAILY_SPEND_LIMIT_SOL: num(1.0),
  DAILY_LOSS_LIMIT_SOL: num(0.5),
  MAX_OPEN_POSITIONS: num(5),
  MIN_SOL_RESERVE: num(0.03),
  MINT_COOLDOWN_MS: num(60_000),

  MAX_PRICE_IMPACT_BPS: num(300),
  MIN_TARGET_BUY_SOL: num(0.05),
  MAX_TOP10_HOLDER_PCT: num(70),
  REQUIRE_NULL_MINT_AUTHORITY: bool(true),
  REQUIRE_NULL_FREEZE_AUTHORITY: bool(true),
  REQUIRE_EXIT_ROUTE: bool(true),
  BLOCKLIST_MINTS: z.string().optional().default(''),

  MIRROR_EXITS: bool(true),

  LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).optional().default('info'),
  STATE_FILE: z.string().optional().default('./data/state.json'),
});

function fail(lines: string[]): never {
  console.error('\nConfiguration error:\n' + lines.map((l) => `  - ${l}`).join('\n') + '\n');
  console.error('Copy .env.example to .env and fill it in.\n');
  process.exit(1);
}

const parsed = schema.safeParse(process.env);
if (!parsed.success) {
  fail(
    parsed.error.issues.map(
      (i) => `${i.path.join('.') || '(root)'}: ${i.message}`,
    ),
  );
}

const raw = parsed.data;

if (!raw.WALLET_PRIVATE_KEY && !raw.WALLET_MNEMONIC) {
  fail(['set either WALLET_PRIVATE_KEY or WALLET_MNEMONIC (run `npm run new-wallet` to create one)']);
}

// Cross-field sanity. A misconfigured cap is worse than no cap, because it
// reads like protection while allowing everything through.
const problems: string[] = [];
if (raw.COPY_FIXED_SOL > raw.MAX_TRADE_SOL) {
  problems.push(
    `COPY_FIXED_SOL (${raw.COPY_FIXED_SOL}) exceeds MAX_TRADE_SOL (${raw.MAX_TRADE_SOL}) — every trade would be clamped`,
  );
}
if (raw.MAX_TRADE_SOL > raw.DAILY_SPEND_LIMIT_SOL) {
  problems.push(
    `MAX_TRADE_SOL (${raw.MAX_TRADE_SOL}) exceeds DAILY_SPEND_LIMIT_SOL (${raw.DAILY_SPEND_LIMIT_SOL})`,
  );
}
if (raw.SLIPPAGE_BPS > 2_000) {
  problems.push(`SLIPPAGE_BPS ${raw.SLIPPAGE_BPS} (>20%) will get you sandwiched on every fill`);
}
if (raw.MAX_OPEN_POSITIONS < 1) problems.push('MAX_OPEN_POSITIONS must be at least 1');
if (raw.MIN_SOL_RESERVE < 0.01) {
  problems.push('MIN_SOL_RESERVE below 0.01 risks being unable to afford the fee on your own exit');
}
if (problems.length) fail(problems);

export const config = {
  ...raw,
  targetWallet: new PublicKey(raw.TARGET_WALLET),
  blocklist: new Set(
    raw.BLOCKLIST_MINTS.split(',')
      .map((s) => s.trim())
      .filter(Boolean),
  ),
} as const;

export type Config = typeof config;
