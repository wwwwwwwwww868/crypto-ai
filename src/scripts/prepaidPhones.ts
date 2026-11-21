/**
 * Prepaid phone generator — produces MOCK prepaid phone accounts for testing,
 * demos and seeding databases.
 *
 * Every number uses the North American 555-0100..555-0199 block, which is
 * reserved for fictional use and is never assigned to a real subscriber.
 * Top-up PINs are random and are NOT valid with any carrier.
 *
 * Usage:
 *   npm run prepaid-phones -- [--count 10] [--format table|json|csv] [--seed 42]
 */
import { pathToFileURL } from "node:url";

export interface PrepaidPhone {
  number: string; // E.164, e.g. +12125550142
  display: string; // (212) 555-0142
  carrier: string;
  plan: string;
  balanceUsd: number;
  dataGbRemaining: number;
  activatedOn: string; // YYYY-MM-DD
  expiresOn: string; // YYYY-MM-DD
  mockTopUpPin: string;
}

const CARRIERS = ["DemoMobile", "SampleTel", "TestWireless", "MockCell"] as const;
const PLANS = [
  { name: "Basic 30-day", days: 30, dataGb: 2 },
  { name: "Plus 30-day", days: 30, dataGb: 10 },
  { name: "Unlimited 30-day", days: 30, dataGb: 50 },
  { name: "Pay-as-you-go", days: 90, dataGb: 0 },
] as const;
// Real, commonly used area codes — paired with the fictional 555-01XX block.
const AREA_CODES = ["212", "213", "312", "415", "512", "617", "702", "801", "907", "305"];

/** Small deterministic PRNG (mulberry32) so runs can be reproduced with --seed. */
export function makeRng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = <T>(rng: () => number, arr: readonly T[]): T => arr[Math.floor(rng() * arr.length)]!;
const isoDate = (d: Date) => d.toISOString().slice(0, 10);

export function generatePrepaidPhones(
  count: number,
  rng: () => number = Math.random,
  now: Date = new Date(),
): PrepaidPhone[] {
  // 10 area codes x 100 line numbers = 1000 unique fictional numbers available.
  const maxUnique = AREA_CODES.length * 100;
  if (!Number.isInteger(count) || count < 1 || count > maxUnique) {
    throw new Error(`count must be an integer between 1 and ${maxUnique}`);
  }

  const used = new Set<string>();
  const out: PrepaidPhone[] = [];
  while (out.length < count) {
    const area = pick(rng, AREA_CODES);
    const line = String(100 + Math.floor(rng() * 100)).padStart(4, "0"); // 0100..0199
    const key = area + line;
    if (used.has(key)) continue;
    used.add(key);

    const plan = pick(rng, PLANS);
    const daysAgo = Math.floor(rng() * plan.days);
    const activated = new Date(now.getTime() - daysAgo * 86_400_000);
    const expires = new Date(activated.getTime() + plan.days * 86_400_000);
    const pin = Array.from({ length: 4 }, () =>
      String(Math.floor(rng() * 10_000)).padStart(4, "0"),
    ).join("-");

    out.push({
      number: `+1${area}555${line}`,
      display: `(${area}) 555-${line}`,
      carrier: pick(rng, CARRIERS),
      plan: plan.name,
      balanceUsd: Math.round(rng() * 5000) / 100,
      dataGbRemaining: Math.round(rng() * plan.dataGb * 10) / 10,
      activatedOn: isoDate(activated),
      expiresOn: isoDate(expires),
      mockTopUpPin: `MOCK-${pin}`,
    });
  }
  return out;
}

export function toCsv(rows: PrepaidPhone[]): string {
  const headers = Object.keys(rows[0] ?? {}) as (keyof PrepaidPhone)[];
  const esc = (v: unknown) => {
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [headers.join(","), ...rows.map((r) => headers.map((h) => esc(r[h])).join(","))].join("\n");
}

function parseArgs(argv: string[]) {
  const opts = { count: 10, format: "table", seed: undefined as number | undefined };
  for (let i = 0; i < argv.length; i++) {
    const [flag, val] = [argv[i], argv[i + 1]];
    if (flag === "--count") (opts.count = Number(val)), i++;
    else if (flag === "--format") (opts.format = String(val)), i++;
    else if (flag === "--seed") (opts.seed = Number(val)), i++;
    else if (flag === "--help" || flag === "-h") {
      console.log("Usage: prepaid-phones [--count N] [--format table|json|csv] [--seed N]");
      process.exit(0);
    }
  }
  if (!["table", "json", "csv"].includes(opts.format)) {
    throw new Error("--format must be table, json or csv");
  }
  return opts;
}

function main() {
  const { count, format, seed } = parseArgs(process.argv.slice(2));
  const phones = generatePrepaidPhones(count, seed === undefined ? Math.random : makeRng(seed));
  if (format === "json") console.log(JSON.stringify(phones, null, 2));
  else if (format === "csv") console.log(toCsv(phones));
  else {
    console.table(
      phones.map(({ display, carrier, plan, balanceUsd, dataGbRemaining, expiresOn }) => ({
        number: display,
        carrier,
        plan,
        balance: `$${balanceUsd.toFixed(2)}`,
        data: `${dataGbRemaining} GB`,
        expires: expiresOn,
      })),
    );
    console.log("All numbers are fictional (555-01XX) and PINs are mock values.");
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    main();
  } catch (err) {
    console.error((err as Error).message);
    process.exit(1);
  }
}
