import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const SRC = join(import.meta.dirname, "..", "src");

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (/\.(tsx?|css)$/.test(name)) out.push(path);
  }
  return out;
}

test("web/src has no leftover purple hex or copy", () => {
  const leftover = /purple|#7c3aed|#c4b5fd|#f8f2ff|#a78bfa|#8b5cf6|#9333ea|#a855f7|#6d28d9/i;
  const hits = [];
  for (const file of walk(SRC)) {
    const text = readFileSync(file, "utf8");
    leftover.lastIndex = 0;
    if (leftover.test(text)) hits.push(file.slice(SRC.length + 1));
  }
  assert.deepEqual(hits, []);
});

test("heatmap legend names amber, Strategy Catalog uses tokens not violet hex", () => {
  const heatmap = readFileSync(join(SRC, "components/panels/HeatmapGrid.tsx"), "utf8");
  const catalog = readFileSync(join(SRC, "components/panels/StrategyCatalog.tsx"), "utf8");
  const scanner = readFileSync(join(SRC, "components/panels/SetupScanner.tsx"), "utf8");
  assert.match(heatmap, /Positive exposure · amber/);
  assert.match(heatmap, /`--accent` \(amber\)/);
  assert.match(catalog, /overflow-x-auto rounded-\[8px\]/);
  assert.match(catalog, /var\(--accent-foreground\)/);
  assert.match(catalog, /blocked — not scored/);
  assert.match(scanner, /color: "var\(--accent-foreground\)"/);
});
