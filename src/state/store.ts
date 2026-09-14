import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { randomBytes } from 'node:crypto';
import { config } from '../config.js';
import { log } from '../logger.js';
import type { DayLedger, PersistedState, Position } from '../types.js';

const MAX_SEEN = 5_000;

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function emptyDay(): DayLedger {
  return { date: today(), solSpent: 0, solReceived: 0, realizedPnl: 0, buys: 0, sells: 0 };
}

function emptyState(): PersistedState {
  return { version: 1, seenSignatures: [], positions: {}, day: emptyDay(), lastBuyAtByMint: {} };
}

/**
 * Durable bot state. Persistence is not a nicety here: without it, a restart
 * re-copies trades it already copied and forgets positions it is holding.
 */
export class Store {
  private state: PersistedState = emptyState();
  private seen = new Set<string>();
  private writeQueue: Promise<void> = Promise.resolve();

  async load(): Promise<void> {
    try {
      const raw = await readFile(config.STATE_FILE, 'utf8');
      const parsed = JSON.parse(raw) as PersistedState;
      if (parsed.version !== 1) throw new Error(`unsupported state version ${parsed.version}`);
      this.state = { ...emptyState(), ...parsed };
      this.seen = new Set(this.state.seenSignatures);
      this.rolloverDay();
      log.info(
        `loaded state: ${Object.keys(this.state.positions).length} open position(s), ${this.seen.size} seen signature(s)`,
      );
    } catch (err) {
      const code = (err as { code?: string }).code;
      if (code === 'ENOENT') {
        log.info('no existing state file, starting fresh');
        return;
      }

      // Refuse to continue rather than silently starting with no positions.
      // Forgetting a held token means we never mirror its exit, which loses
      // money quietly; failing to boot loses nothing and is fixable.
      const backup = `${config.STATE_FILE}.corrupt-${Date.now()}`;
      try {
        await rename(config.STATE_FILE, backup);
        log.error(`state file was unreadable; preserved a copy at ${backup}`);
      } catch {
        log.error('state file was unreadable and could not be moved aside');
      }
      throw new Error(
        `could not load state from ${config.STATE_FILE}: ${String(err)}. ` +
          'Inspect the preserved copy for open positions before restarting — ' +
          'starting fresh means the bot will not mirror exits for tokens it already holds.',
      );
    }
  }

  /** Serialized atomic write — write to a temp file, then rename over the target. */
  save(): Promise<void> {
    this.writeQueue = this.writeQueue.then(async () => {
      this.state.seenSignatures = [...this.seen].slice(-MAX_SEEN);
      const path = config.STATE_FILE;
      // A unique temp name per write. A shared one lets two writers overwrite
      // each other's partial file and then rename it into place, which
      // produces a corrupt state file rather than a stale one.
      const tmp = `${path}.${process.pid}.${randomBytes(4).toString('hex')}.tmp`;
      await mkdir(dirname(path), { recursive: true });
      await writeFile(tmp, JSON.stringify(this.state, null, 2), 'utf8');
      await rename(tmp, path);
    }).catch((err) => {
      log.error('failed to persist state', String(err));
    });
    return this.writeQueue;
  }

  isSeen(sig: string): boolean {
    return this.seen.has(sig);
  }

  markSeen(sig: string): void {
    this.seen.add(sig);
    if (this.seen.size > MAX_SEEN * 2) {
      this.seen = new Set([...this.seen].slice(-MAX_SEEN));
    }
  }

  /** Resets the daily ledger when the UTC date changes. */
  rolloverDay(): void {
    if (this.state.day.date !== today()) {
      log.info(
        `daily ledger rollover (${this.state.day.date}: ${this.state.day.buys} buys, ${this.state.day.sells} sells, pnl ${this.state.day.realizedPnl.toFixed(4)} SOL)`,
      );
      this.state.day = emptyDay();
    }
  }

  get day(): DayLedger {
    this.rolloverDay();
    return this.state.day;
  }

  get positions(): Record<string, Position> {
    return this.state.positions;
  }

  getPosition(mint: string): Position | undefined {
    return this.state.positions[mint];
  }

  openCount(): number {
    return Object.keys(this.state.positions).length;
  }

  lastBuyAt(mint: string): number {
    return this.state.lastBuyAtByMint[mint] ?? 0;
  }

  /**
   * Records a fill and does not resolve until it is on disk. Persisting
   * before the caller continues matters: a position held in memory only is a
   * position the bot forgets on restart, and a forgotten position never gets
   * its exit mirrored.
   */
  async recordBuy(args: {
    mint: string;
    decimals: number;
    solSpent: number;
    tokenAmountRaw: bigint;
    signature: string;
  }): Promise<Position> {
    const now = Date.now();
    const existing = this.state.positions[args.mint];
    const position: Position = existing
      ? {
          ...existing,
          tokenAmountRaw: (BigInt(existing.tokenAmountRaw) + args.tokenAmountRaw).toString(),
          solSpent: existing.solSpent + args.solSpent,
          lastUpdatedAt: now,
          entrySignatures: [...existing.entrySignatures, args.signature],
        }
      : {
          mint: args.mint,
          decimals: args.decimals,
          tokenAmountRaw: args.tokenAmountRaw.toString(),
          solSpent: args.solSpent,
          solReceived: 0,
          openedAt: now,
          lastUpdatedAt: now,
          entrySignatures: [args.signature],
        };

    this.state.positions[args.mint] = position;
    this.state.lastBuyAtByMint[args.mint] = now;
    this.day.solSpent += args.solSpent;
    this.day.buys += 1;
    await this.save();
    return position;
  }

  async recordSell(args: { mint: string; solReceived: number; tokenAmountRaw: bigint }): Promise<void> {
    const position = this.state.positions[args.mint];
    if (!position) return;

    const held = BigInt(position.tokenAmountRaw);
    const sold = args.tokenAmountRaw > held ? held : args.tokenAmountRaw;
    const remaining = held - sold;

    // Attribute cost basis proportionally to the fraction sold.
    const fraction = held > 0n ? Number(sold) / Number(held) : 1;
    const costBasis = position.solSpent * fraction;
    const pnl = args.solReceived - costBasis;

    this.day.solReceived += args.solReceived;
    this.day.realizedPnl += pnl;
    this.day.sells += 1;

    if (remaining === 0n) {
      log.info(
        `position closed ${args.mint.slice(0, 8)} — in ${position.solSpent.toFixed(4)} SOL, out ${(
          position.solReceived + args.solReceived
        ).toFixed(4)} SOL, pnl ${pnl >= 0 ? '+' : ''}${pnl.toFixed(4)} SOL`,
      );
      delete this.state.positions[args.mint];
    } else {
      this.state.positions[args.mint] = {
        ...position,
        tokenAmountRaw: remaining.toString(),
        solSpent: position.solSpent - costBasis,
        solReceived: position.solReceived + args.solReceived,
        lastUpdatedAt: Date.now(),
      };
      log.info(
        `partial exit ${args.mint.slice(0, 8)} — realized ${pnl >= 0 ? '+' : ''}${pnl.toFixed(4)} SOL, ${remaining} base units left`,
      );
    }

    await this.save();
  }
}
