import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const server = readFileSync(new URL("../server.mjs", import.meta.url), "utf8");

test("browser proxy allowlists provider capability status", () => {
  assert.match(server, /"\/api\/provider-capabilities": "\/api\/provider-capabilities"/);
});
