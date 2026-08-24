import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const terminal = readFileSync(new URL("../src/components/panels/OptionsTerminal.tsx", import.meta.url), "utf8");

test("Options Terminal follows the dense workstation shell without order authority", () => {
  assert.match(terminal, /data-testid="options-terminal"/);
  assert.match(terminal, /Structure research · no order ticket/);
  assert.match(terminal, /RESEARCH STRUCTURE · NO ORDER TICKET/);
  assert.match(terminal, /IV rank:/);
  assert.match(terminal, /Record thesis/);
  assert.match(terminal, /role="region" aria-label="Option chain"/);
  assert.match(terminal, /max-h-\[520px\] overflow-auto/);
  assert.match(terminal, /<table className="w-full min-w-\[1180px\]/);
  assert.match(terminal, /<thead className="sticky top-0/);
  assert.match(terminal, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(terminal, /Missing IV, Greeks, or open interest stay unavailable/);
  assert.match(terminal, /Nothing here can submit an order/);
  assert.doesNotMatch(terminal, /rounded-xl|rounded-lg|rounded-2xl/);
  assert.doesNotMatch(terminal, /submit_order|place_order|create_order|TradingClient|OrderClient|api\.alpaca\.markets/);
  assert.doesNotMatch(terminal, /paper-api\.alpaca\.markets/);
});
