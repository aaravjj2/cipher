"use client";

import { useEffect, useRef, useState } from "react";
import { startAskJob, type AskChatMessage } from "@/lib/api";

/**
 * Ask Cipher — a chat layer grounded strictly in tool calls made this turn
 * against Cipher's own data (core/ask_cipher.py). No general market
 * knowledge, no live orders, no fresh backtests — if a question needs
 * something none of the five tools cover, the assistant says so rather than
 * guessing. First real EventSource consumer in this app; the SSE plumbing
 * (/api/stream?job=&type=chat) has existed since the backtest-job retarget
 * but nothing drove it from the browser until this panel.
 */

const TOOL_LABELS: Record<string, string> = {
  get_evidence_status: "checking evidence status…",
  get_standing: "checking open registrations and positions…",
  get_holdings: "checking your holdings…",
  get_quote: "checking a live quote…",
  list_strategies: "checking the strategy catalog…",
};

type ChatMessage = AskChatMessage;

function MessageBubble({ role, content }: ChatMessage) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className="max-w-[75%] rounded-[10px] px-4 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap"
        style={{
          background: isUser ? "var(--accent)" : "var(--panel-2)",
          color: isUser ? "#fff" : "var(--text)",
          border: isUser ? "none" : "1px solid var(--line)",
        }}
      >
        {content}
      </div>
    </div>
  );
}

export function AskCipher() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [toolCall, setToolCall] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return () => esRef.current?.close();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText, toolCall]);

  async function send() {
    const message = input.trim();
    if (!message || sending) return;
    setError(null);
    setInput("");
    setSending(true);
    setStreamingText("");
    setToolCall(null);
    const history = messages;
    setMessages((prev) => [...prev, { role: "user", content: message }]);

    let job;
    try {
      job = await startAskJob(message, history);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach Cipher");
      setSending(false);
      return;
    }

    const es = new EventSource(`/api/stream?job=${encodeURIComponent(job.job_id)}&type=chat`);
    esRef.current = es;
    let finalText = "";

    es.addEventListener("tool_call", (evt) => {
      const data = JSON.parse((evt as MessageEvent).data);
      setToolCall(TOOL_LABELS[data.name] || `using ${data.name}…`);
    });
    es.addEventListener("text_delta", (evt) => {
      const data = JSON.parse((evt as MessageEvent).data);
      setToolCall(null);
      finalText += data.text;
      setStreamingText((prev) => prev + data.text);
    });
    es.addEventListener("error", (evt) => {
      const raw = (evt as MessageEvent).data;
      if (raw) {
        try {
          const data = JSON.parse(raw);
          setError(data.error || "Ask Cipher hit an error.");
        } catch {
          setError("Ask Cipher hit an error.");
        }
      }
    });
    es.addEventListener("complete", (evt) => {
      const data = JSON.parse((evt as MessageEvent).data);
      if (data.status === "done" && finalText) {
        setMessages((prev) => [...prev, { role: "assistant", content: finalText }]);
      } else if (data.status === "error") {
        setError(data.error || "Ask Cipher hit an error.");
      }
      setStreamingText("");
      setToolCall(null);
      setSending(false);
      es.close();
    });
    es.onerror = () => {
      setSending(false);
      es.close();
    };
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <section className="flex flex-col gap-4 h-full" style={{ fontFamily: "var(--font-sans)", color: "var(--text)" }}>
      <h1 className="text-[22px] sm:text-[24px] font-bold leading-tight">Ask Cipher</h1>

      <p
        className="text-[11.5px] leading-relaxed rounded-[8px] px-3 py-2"
        style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-mute)" }}
      >
        Answers are grounded only in tool calls Cipher makes this turn against its own evidence status, open
        registrations and positions, your holdings, live quotes, and the strategy catalog. No fresh backtests, no
        general market opinions, no buy/sell recommendations — if something isn&rsquo;t covered, it says so.
      </p>

      <div
        className="flex-1 flex flex-col gap-3 overflow-y-auto rounded-[var(--radius)] p-4 min-h-[300px]"
        style={{ background: "var(--panel)", border: "1px solid var(--line)" }}
      >
        {messages.length === 0 && !streamingText && (
          <p className="text-[12.5px] italic" style={{ color: "var(--text-mute)" }}>
            Ask something like &ldquo;what&rsquo;s my portfolio worth&rdquo; or &ldquo;why is the cluster strategy
            blocked&rdquo;.
          </p>
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} content={m.content} />
        ))}
        {toolCall && (
          <div className="flex justify-start">
            <div
              className="rounded-[10px] px-4 py-2.5 text-[12.5px] italic"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-mute)" }}
            >
              {toolCall}
            </div>
          </div>
        )}
        {streamingText && <MessageBubble role="assistant" content={streamingText} />}
        <div ref={bottomRef} />
      </div>

      {error && (
        <p className="text-[12px]" style={{ color: "var(--neg)" }}>
          {error}
        </p>
      )}

      <div className="flex flex-row items-end gap-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask Cipher about your holdings, strategies, or evidence status…"
          rows={2}
          disabled={sending}
          className="flex-1 resize-none rounded-[10px] px-3 py-2.5 text-[13px] outline-none disabled:opacity-60"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}
        />
        <button
          type="button"
          onClick={send}
          disabled={sending || !input.trim()}
          className="rounded-[10px] px-5 py-[10px] text-[13px] font-bold shrink-0 disabled:opacity-50"
          style={{ background: "var(--accent)", color: "#fff" }}
        >
          {sending ? "Asking…" : "Ask"}
        </button>
      </div>
    </section>
  );
}

export default AskCipher;
