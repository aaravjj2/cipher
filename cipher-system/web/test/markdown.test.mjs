/**
 * Guards for lib/markdown.ts — the Markdown subset Ask Cipher renders model output with.
 *
 * The parser's job is to hide markers, never to change content. A model answer contains
 * numbers the user may act on, so "no character is dropped, added or reordered except a
 * marker that actually matched" is the invariant worth pinning; visibleText() exists so
 * these tests can assert it directly.
 *
 * Run with: node --test web/test/*.test.mjs
 * (the glob form, same as app/test — `node --test web/test/` resolves the directory as
 * a module and fails before any test runs)
 *
 * There is no bundler-aware test runner in this project, so the module under test is
 * compiled with the repo's own tsc into a temp dir and imported as ESM. That keeps the
 * source a normal .ts file (typechecked by `npm run typecheck` like everything else)
 * instead of duplicating the parser in JS just to make it testable.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(join(tmpdir(), "cipher-md-"));

execFileSync(
  join(WEB, "node_modules", ".bin", "tsc"),
  [
    join(WEB, "src", "lib", "markdown.ts"),
    "--outDir", outDir,
    "--module", "esnext",
    "--target", "es2022",
    "--moduleResolution", "bundler",
  ],
  { stdio: "inherit" },
);

const { parseInline, parseMarkdown, visibleText } = await import(
  join(outDir, "markdown.js")
);

test("bold markers are consumed and their text kept", () => {
  assert.deepEqual(parseInline("a **b** c"), [
    { kind: "text", text: "a " },
    { kind: "bold", text: "b" },
    { kind: "text", text: " c" },
  ]);
});

test("inline code is kept verbatim", () => {
  assert.deepEqual(
    parseInline("run `fetchQuote(x)` now").map((s) => s.kind),
    ["text", "code", "text"],
  );
});

test("an unmatched ** stays literal, so arithmetic survives", () => {
  assert.deepEqual(parseInline("2 ** 3 = 8"), [{ kind: "text", text: "2 ** 3 = 8" }]);
});

test("empty bold stays literal", () => {
  assert.deepEqual(parseInline("****"), [{ kind: "text", text: "****" }]);
});

test("numbers are never altered", () => {
  const src = "GEX **-$1.28M** at 307.5, IV 0.4123, ratio 1.0000001";
  assert.equal(
    visibleText(parseMarkdown(src)),
    "GEX -$1.28M at 307.5, IV 0.4123, ratio 1.0000001",
  );
});

test("consecutive bullets group into one list", () => {
  const blocks = parseMarkdown("- one\n- two **bold**\n- three");
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].kind, "bullets");
  assert.equal(blocks[0].items.length, 3);
  assert.equal(visibleText(blocks), "one\ntwo bold\nthree");
});

test("the model's own numbering is preserved, not regenerated", () => {
  const blocks = parseMarkdown("1. a\n2. b\n4. d");
  assert.equal(blocks[0].kind, "ordered");
  assert.deepEqual(
    blocks[0].items.map((i) => i.marker),
    ["1.", "2.", "4."],
  );
});

test("a bare asterisk with no space is not a bullet", () => {
  const blocks = parseMarkdown("*not a bullet");
  assert.equal(blocks[0].kind, "paragraph");
  assert.equal(visibleText(blocks), "*not a bullet");
});

test("headings consume their hashes", () => {
  const blocks = parseMarkdown("## Summary\nbody");
  assert.equal(blocks[0].kind, "heading");
  assert.equal(blocks[0].level, 2);
  assert.equal(visibleText(blocks), "Summary\nbody");
});

test("fenced code keeps markers inside it literal", () => {
  const blocks = parseMarkdown("```\nx = **2**\n- not a bullet\n```");
  assert.equal(blocks[0].kind, "code");
  assert.equal(blocks[0].text, "x = **2**\n- not a bullet");
});

test("an unterminated fence mid-stream stays a code block", () => {
  const blocks = parseMarkdown("text\n```\npartial code");
  assert.deepEqual(
    blocks.map((b) => b.kind),
    ["paragraph", "code"],
  );
  assert.equal(blocks[1].text, "partial code");
});

test("single newlines inside a paragraph survive", () => {
  assert.equal(visibleText(parseMarkdown("line1\nline2")), "line1\nline2");
});

test("fidelity: stripping matched markers reproduces the whole input", () => {
  const src = [
    "### Standing",
    "",
    "You have **2** open registrations:",
    "- SPY `fitted_head` — entered 2026-08-04 at $771.20 (+0.31%)",
    "- QQQ gamma flip at 718.5, delta **-0.42**",
    "",
    "1. Check `/api/standing`",
    "2. Nothing here is advice.",
  ].join("\n");
  const expected = src
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .split("\n")
    .map((line) => line.replace(/^###\s+/, "").replace(/^-\s+/, ""))
    .filter((line) => line !== "")
    .join("\n");
  assert.equal(visibleText(parseMarkdown(src)), expected);
});
