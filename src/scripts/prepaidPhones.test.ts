import { test } from "node:test";
import assert from "node:assert/strict";
import { generatePrepaidPhones, makeRng, toCsv } from "./prepaidPhones.js";

test("generates the requested number of unique fictional numbers", () => {
  const phones = generatePrepaidPhones(200, makeRng(1));
  assert.equal(phones.length, 200);
  assert.equal(new Set(phones.map((p) => p.number)).size, 200);
  for (const p of phones) {
    assert.match(p.number, /^\+1\d{3}55501\d{2}$/);
    assert.match(p.mockTopUpPin, /^MOCK-\d{4}-\d{4}-\d{4}-\d{4}$/);
    assert.ok(p.expiresOn > p.activatedOn);
  }
});

test("same seed gives same output", () => {
  const now = new Date("2026-01-01T00:00:00Z");
  assert.deepEqual(
    generatePrepaidPhones(5, makeRng(42), now),
    generatePrepaidPhones(5, makeRng(42), now),
  );
});

test("rejects bad counts", () => {
  assert.throws(() => generatePrepaidPhones(0));
  assert.throws(() => generatePrepaidPhones(1001));
});

test("csv has header plus one line per row", () => {
  const csv = toCsv(generatePrepaidPhones(3, makeRng(7)));
  assert.equal(csv.split("\n").length, 4);
});
