import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const SRC = join(import.meta.dirname, "..", "src");
const HOST = /api\.alpaca\.markets|paper-api\.alpaca\.markets/;

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (/\.(tsx?|css)$/.test(name)) out.push(path);
  }
  return out;
}

test("web/src does not hardcode an Alpaca broker host", () => {
  const hits = [];
  for (const file of walk(SRC)) {
    HOST.lastIndex = 0;
    if (HOST.test(readFileSync(file, "utf8"))) hits.push(file.slice(SRC.length + 1));
  }
  assert.deepEqual(hits, []);
});
