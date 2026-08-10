/**
 * A deliberately tiny Markdown subset parser, for rendering model output in Ask Cipher.
 *
 * Why not a library: the only thing that reaches this parser is an LLM answer about the
 * user's own research data, and those answers contain numbers the user may act on. A
 * general Markdown renderer brings link/HTML/image handling that we would then have to
 * sanitize, for syntax the model does not emit. So this handles exactly what shows up in
 * practice — bold, inline code, headings, bullet and numbered lists, fenced code — and
 * everything else passes through as literal text.
 *
 * The one invariant that matters: **no character of the input is ever dropped or
 * reordered except a marker that was actually matched.** An unmatched `**` stays a `**`.
 * A number never moves. `visibleText()` exists so a test can assert exactly that.
 *
 * Deliberately NOT supported, and why:
 *  - italic (`*x*`): a lone asterisk is too common in prose and arithmetic to claim.
 *  - links (`[t](url)`): the model rarely emits them, and rendering an arbitrary
 *    model-supplied href as a clickable link is a phishing surface for no benefit —
 *    a URL left as text is still readable and copyable.
 */

export type InlineSegment = {
  kind: "text" | "bold" | "code";
  text: string;
};

export type Block =
  | { kind: "heading"; level: number; content: InlineSegment[] }
  | { kind: "paragraph"; content: InlineSegment[] }
  | { kind: "bullets"; items: InlineSegment[][] }
  | { kind: "ordered"; items: { marker: string; content: InlineSegment[] }[] }
  | { kind: "code"; text: string };

/** `**bold**` (non-empty, non-greedy) or `` `code` ``. Anything unmatched stays text. */
const INLINE_RE = /\*\*([\s\S]+?)\*\*|`([^`\n]+)`/g;

export function parseInline(input: string): InlineSegment[] {
  const out: InlineSegment[] = [];
  let last = 0;
  for (const m of input.matchAll(INLINE_RE)) {
    const at = m.index;
    if (at > last) out.push({ kind: "text", text: input.slice(last, at) });
    if (m[1] !== undefined) out.push({ kind: "bold", text: m[1] });
    else out.push({ kind: "code", text: m[2] });
    last = at + m[0].length;
  }
  if (last < input.length) out.push({ kind: "text", text: input.slice(last) });
  return out;
}

const HEADING_RE = /^(#{1,4})\s+(.*)$/;
/** `-`, `*` or `•` followed by a space. A bare `*` with no space is not a bullet. */
const BULLET_RE = /^\s{0,3}[-*•]\s+(.*)$/;
const ORDERED_RE = /^\s{0,3}(\d{1,3}[.)])\s+(.*)$/;
const FENCE_RE = /^\s*```/;

export function parseMarkdown(source: string): Block[] {
  const lines = source.split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    // Lines within a paragraph keep their own newlines: model answers use single
    // newlines meaningfully (a label per line), and collapsing them would reflow
    // numbers into a wall of text.
    blocks.push({ kind: "paragraph", content: parseInline(paragraph.join("\n")) });
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (FENCE_RE.test(line)) {
      flushParagraph();
      const body: string[] = [];
      i++;
      // An unterminated fence runs to the end of the message rather than swallowing
      // the rest as a paragraph — mid-stream text is genuinely unterminated.
      for (; i < lines.length && !FENCE_RE.test(lines[i]); i++) body.push(lines[i]);
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      flushParagraph();
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        content: parseInline(heading[2]),
      });
      continue;
    }

    const bullet = BULLET_RE.exec(line);
    if (bullet) {
      flushParagraph();
      const items: InlineSegment[][] = [parseInline(bullet[1])];
      while (i + 1 < lines.length) {
        const next = BULLET_RE.exec(lines[i + 1]);
        if (!next) break;
        items.push(parseInline(next[1]));
        i++;
      }
      blocks.push({ kind: "bullets", items });
      continue;
    }

    const ordered = ORDERED_RE.exec(line);
    if (ordered) {
      flushParagraph();
      // The model's own numbering is preserved rather than regenerated: if it wrote
      // "1., 2., 4." that is what it said, and silently renumbering would be us
      // asserting something it did not.
      const items = [{ marker: ordered[1], content: parseInline(ordered[2]) }];
      while (i + 1 < lines.length) {
        const next = ORDERED_RE.exec(lines[i + 1]);
        if (!next) break;
        items.push({ marker: next[1], content: parseInline(next[2]) });
        i++;
      }
      blocks.push({ kind: "ordered", items });
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph();
  return blocks;
}

/**
 * Every character the reader will actually see, in order. Used by the parser's own
 * tests to prove the fidelity invariant: only matched markers and list/heading
 * prefixes disappear, never content.
 */
export function visibleText(blocks: Block[]): string {
  const inline = (segments: InlineSegment[]) => segments.map((s) => s.text).join("");
  return blocks
    .map((block) => {
      switch (block.kind) {
        case "heading":
          return inline(block.content);
        case "paragraph":
          return inline(block.content);
        case "bullets":
          return block.items.map(inline).join("\n");
        case "ordered":
          return block.items.map((it) => `${it.marker} ${inline(it.content)}`).join("\n");
        case "code":
          return block.text;
      }
    })
    .join("\n");
}
