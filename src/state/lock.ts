import { mkdir, open, readFile, unlink } from 'node:fs/promises';
import { dirname } from 'node:path';
import { hostname } from 'node:os';
import { log } from '../logger.js';

interface LockRecord {
  pid: number;
  host: string;
  startedAt: string;
}

function isAlive(pid: number): boolean {
  try {
    // Signal 0 tests for existence without delivering anything.
    process.kill(pid, 0);
    return true;
  } catch (err) {
    // EPERM means the process exists but belongs to another user.
    return (err as { code?: string }).code === 'EPERM';
  }
}

export class InstanceLockError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'InstanceLockError';
  }
}

/**
 * Guarantees a single running bot per state file.
 *
 * Two instances sharing one state file each enforce the daily caps against
 * their own view of it, so the real spend is double what either believes it
 * allowed. That is a money-losing failure mode, so it is worth refusing to
 * start over.
 */
export async function acquireLock(stateFile: string): Promise<() => Promise<void>> {
  const lockPath = `${stateFile}.lock`;
  await mkdir(dirname(lockPath), { recursive: true });

  const record: LockRecord = {
    pid: process.pid,
    host: hostname(),
    startedAt: new Date().toISOString(),
  };

  const write = async () => {
    // 'wx' fails if the file already exists, making creation atomic.
    const handle = await open(lockPath, 'wx');
    try {
      await handle.writeFile(JSON.stringify(record, null, 2), 'utf8');
    } finally {
      await handle.close();
    }
  };

  try {
    await write();
  } catch (err) {
    if ((err as { code?: string }).code !== 'EEXIST') throw err;

    let existing: LockRecord | null = null;
    try {
      existing = JSON.parse(await readFile(lockPath, 'utf8')) as LockRecord;
    } catch {
      existing = null;
    }

    if (existing && existing.host === record.host && isAlive(existing.pid)) {
      throw new InstanceLockError(
        `another instance is already running (pid ${existing.pid}, started ${existing.startedAt}). ` +
          'Two bots sharing one state file would each enforce the daily caps separately, ' +
          `so the real spend would be double. Stop it first, or point STATE_FILE elsewhere.`,
      );
    }

    if (existing && existing.host !== record.host) {
      throw new InstanceLockError(
        `state file is locked by host ${existing.host} (pid ${existing.pid}). ` +
          'If that instance is definitely stopped, delete ' +
          `${lockPath} and restart.`,
      );
    }

    log.warn(
      `removing stale lock from pid ${existing?.pid ?? 'unknown'} (process is gone)`,
    );
    await unlink(lockPath).catch(() => {});
    await write();
  }

  let released = false;
  return async () => {
    if (released) return;
    released = true;
    await unlink(lockPath).catch(() => {});
  };
}
