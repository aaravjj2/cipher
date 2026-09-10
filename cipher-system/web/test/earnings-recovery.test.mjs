import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import test from "node:test";
import ts from "typescript";

test("Earnings Radar recovers from a fetch error and retains data on refresh failure", async () => {
  const states = [];
  let cursor = 0, effect, refresh, fail = true;
  const require = createRequire(import.meta.url);
  const radar = { status: "current", count: 0, cards: [], as_of: "2026-09-07T12:19:59Z" };
  const componentModule = { exports: {} };
  const source = readFileSync(new URL("../src/components/panels/EarningsRadar.tsx", import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS } }).outputText;
  runInNewContext(js, {
    exports: componentModule.exports, module: componentModule, AbortController,
    window: { setInterval(fn) { refresh = fn; }, clearInterval() {} },
    require(name) {
      if (name === "react") return {
        useState(initial) { const i = cursor++; if (!(i in states)) states[i] = initial; return [states[i], (v) => { states[i] = v; }]; },
        useEffect(fn) { effect = fn; },
      };
      if (name === "@/lib/api") return { async fetchEarningsRadar() { if (fail) throw new Error("provider unavailable"); return radar; } };
      if (name === "@/components/ui/skeleton") return { LoadingStatus: "span" };
      return require(name);
    },
  });
  const render = () => { cursor = 0; return componentModule.exports.EarningsRadar(); };
  render();
  const cleanup = effect();
  await new Promise(setImmediate);
  assert.match(JSON.stringify(render()), /retrying automatically/);
  fail = false;
  refresh();
  await new Promise(setImmediate);
  assert.equal(states[1], "");
  assert.equal(states[0], radar);
  assert.match(JSON.stringify(render()), /Earnings Radar/);
  fail = true;
  refresh();
  await new Promise(setImmediate);
  assert.equal(states[0], radar);
  assert.match(JSON.stringify(render()), /Showing the last successful radar/);
  cleanup();
});
