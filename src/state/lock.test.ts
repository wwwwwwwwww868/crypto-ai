import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir, hostname } from 'node:os';
import { join } from 'node:path';

process.env.RPC_HTTP_URL ??= 'https://example.invalid';
process.env.RPC_WS_URL ??= 'wss://example.invalid';
process.env.TARGET_WALLET ??= 'GDfnEsia2WLAW5t8yx2X5j2mkfA74i5kwGdDuZHt7XmG';
process.env.WALLET_PRIVATE_KEY ??= '4'.repeat(88);
process.env.LOG_LEVEL ??= 'error';

const { acquireLock, InstanceLockError } = await import('./lock.js');

describe('acquireLock', () => {
  let stateFile: string;

  beforeEach(() => {
    stateFile = join(mkdtempSync(join(tmpdir(), 'copybot-lock-')), 'state.json');
  });

  test('acquires and releases a lock', async () => {
    const release = await acquireLock(stateFile);
    assert.equal(existsSync(`${stateFile}.lock`), true);
    await release();
    assert.equal(existsSync(`${stateFile}.lock`), false);
  });

  test('refuses a second instance while the first holds the lock', async () => {
    const release = await acquireLock(stateFile);
    try {
      await assert.rejects(() => acquireLock(stateFile), InstanceLockError);
    } finally {
      await release();
    }
  });

  test('takes over a stale lock left by a dead process', async () => {
    // PID 1 exists, so use an unused high PID that is very unlikely to be live.
    writeFileSync(
      `${stateFile}.lock`,
      JSON.stringify({ pid: 999_999, host: hostname(), startedAt: new Date().toISOString() }),
    );

    const release = await acquireLock(stateFile);
    assert.equal(existsSync(`${stateFile}.lock`), true);
    await release();
  });

  test('refuses a lock held by a different host', async () => {
    writeFileSync(
      `${stateFile}.lock`,
      JSON.stringify({ pid: 4242, host: 'some-other-machine', startedAt: new Date().toISOString() }),
    );

    await assert.rejects(() => acquireLock(stateFile), InstanceLockError);
  });

  test('recovers from an unparseable lock file', async () => {
    writeFileSync(`${stateFile}.lock`, 'not json at all');
    const release = await acquireLock(stateFile);
    await release();
  });

  test('release is idempotent', async () => {
    const release = await acquireLock(stateFile);
    await release();
    await release();
    assert.equal(existsSync(`${stateFile}.lock`), false);
  });
});
