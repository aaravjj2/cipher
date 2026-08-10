import { parseMarkdown, type Block, type InlineSegment } from "@/lib/markdown";

/**
 * Renders the Markdown subset in lib/markdown.ts as React elements.
 *
 * No `dangerouslySetInnerHTML` anywhere: every segment becomes a text node inside a
 * styled element, so model output cannot inject markup even in principle.
 */

function Inline({ segments }: { segments: InlineSegment[] }) {
  return (
    <>
      {segments.map((segment, i) => {
        if (segment.kind === "bold") {
          return (
            <strong key={i} style={{ fontWeight: 700, color: "var(--text)" }}>
              {segment.text}
            </strong>
          );
        }
        if (segment.kind === "code") {
          return (
            <code
              key={i}
              className="rounded-[4px] px-[4px] py-[1px] text-[0.92em]"
              style={{
                fontFamily: "var(--font-mono)",
                background: "color-mix(in srgb, var(--panel) 70%, transparent)",
                border: "1px solid var(--line)",
              }}
            >
              {segment.text}
            </code>
          );
        }
        return <span key={i}>{segment.text}</span>;
      })}
    </>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "heading":
      return (
        <p
          className="font-bold uppercase"
          style={{
            // Headings inside a chat bubble stay close to body size — a model writing
            // "## Summary" means a label, not a page title.
            fontSize: block.level <= 2 ? "13px" : "12px",
            letterSpacing: "0.05em",
            color: "var(--text)",
          }}
        >
          <Inline segments={block.content} />
        </p>
      );
    case "paragraph":
      return (
        <p className="whitespace-pre-wrap">
          <Inline segments={block.content} />
        </p>
      );
    case "bullets":
      return (
        <ul className="flex flex-col gap-[3px] pl-1">
          {block.items.map((item, i) => (
            <li key={i} className="flex flex-row gap-2">
              <span aria-hidden style={{ color: "var(--text-mute)" }}>
                •
              </span>
              <span className="min-w-0 whitespace-pre-wrap">
                <Inline segments={item} />
              </span>
            </li>
          ))}
        </ul>
      );
    case "ordered":
      return (
        <ul className="flex flex-col gap-[3px] pl-1">
          {block.items.map((item, i) => (
            <li key={i} className="flex flex-row gap-2">
              <span style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}>
                {item.marker}
              </span>
              <span className="min-w-0 whitespace-pre-wrap">
                <Inline segments={item.content} />
              </span>
            </li>
          ))}
        </ul>
      );
    case "code":
      return (
        <pre
          className="overflow-x-auto rounded-[6px] px-3 py-2 text-[12px]"
          style={{
            fontFamily: "var(--font-mono)",
            background: "color-mix(in srgb, var(--panel) 70%, transparent)",
            border: "1px solid var(--line)",
          }}
        >
          {block.text}
        </pre>
      );
  }
}

export function MarkdownText({ children }: { children: string }) {
  const blocks = parseMarkdown(children);
  return (
    <div className="flex flex-col gap-2">
      {blocks.map((block, i) => (
        <BlockView key={i} block={block} />
      ))}
    </div>
  );
}

export default MarkdownText;
