"use client";

import React, { useEffect, useRef, useState } from "react";
import { Bot, RefreshCw, Send, Sparkles, X } from "lucide-react";

interface CopilotWidgetProps {
  apiBase?: string | null;
}

interface FollowUpChip {
  topic: string;
  title: string;
}

interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  topic?: string;
  followups?: FollowUpChip[];
  meta?: "kb" | "swarm" | null;
  error?: boolean;
}

interface CopilotResponse {
  answer: string;
  topic: string;
  title: string;
  followups: FollowUpChip[];
  model?: string | null;
  provider?: string | null;
  used_fallback: boolean;
}

const INTRO: ChatMsg = {
  id: "intro",
  role: "assistant",
  content:
    "Ahlan! I'm the **AegisTel copilot** — grounded in the platform's real playbook. Ask me how to run an audit, what a verdict means, which network checks fire, or how this gets sold. Pick a quick question to start:",
  meta: "kb",
};

const QUICK_QUESTIONS: Array<{ q: string; enhance: boolean }> = [
  { q: "How do I run an audit?", enhance: false },
  { q: "What does BLOCKED mean?", enhance: false },
  { q: "Which tools check the SIM?", enhance: false },
  { q: "How does the drill work?", enhance: false },
  { q: "Can banks call this as an API?", enhance: false },
  { q: "What LLMs power this?", enhance: false },
];

function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*.+?\*\*|`.+?`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="font-bold text-cyan-200">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={i} className="rounded bg-slate-800 border border-slate-700 px-1 py-0.5 text-[10px] text-emerald-300 font-mono">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

function renderRich(text: string): React.ReactNode {
  const lines = text.split("\n").filter((line) => line.trim().length > 0);
  const blocks: React.ReactNode[] = [];
  let bulletList: React.ReactNode[] = [];
  const flushBullets = (keyBase: string) => {
    if (bulletList.length) {
      blocks.push(
        <ul key={keyBase} className="space-y-0.5 pl-1">
          {bulletList}
        </ul>
      );
      bulletList = [];
    }
  };
  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("- ")) {
      bulletList.push(
        <li key={`b${idx}`} className="flex gap-1.5 text-[11px] leading-relaxed text-slate-200">
          <span className="text-cyan-400 select-none">•</span>
          <span>{renderInline(trimmed.slice(2))}</span>
        </li>
      );
      return;
    }
    flushBullets(`u${idx}`);
    const numbered = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (numbered) {
      blocks.push(
        <p key={`n${idx}`} className="flex gap-1.5 text-[11px] leading-relaxed text-slate-200">
          <span className="text-amber-400 font-mono select-none">{numbered[1]}.</span>
          <span>{renderInline(numbered[2])}</span>
        </p>
      );
      return;
    }
    const isHeader = /^[A-Z0-9][A-Z0-9 ]*$/.test(trimmed.replace(/[*`]/g, "")) && trimmed.length < 42;
    blocks.push(
      <p key={`p${idx}`} className={`text-[11px] leading-relaxed text-slate-200 ${isHeader ? "mt-1 font-bold uppercase tracking-[0.15em] text-slate-300 text-[10px]" : ""}`}>
        {renderInline(trimmed)}
      </p>
    );
  });
  flushBullets("final");
  return <div className="space-y-1.5">{blocks}</div>;
}

function typingId() {
  return Math.random().toString(36).slice(2, 10);
}

export default function CopilotWidget({ apiBase }: CopilotWidgetProps) {
  const base = apiBase ?? "";
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMsg[]>([INTRO]);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, typing, open]);

  const history = messages
    .filter((m) => m.role === "user" || m.meta !== undefined)
    .slice(-10)
    .map((m) => ({
      role: m.role,
      content: m.content,
      topic: m.role === "assistant" ? m.topic : undefined,
    }));

  const lastTopic = [...messages].reverse().find((m) => m.role === "assistant" && m.topic)?.topic;

  const ask = async (question: string, enhance: boolean) => {
    const q = question.trim();
    if (!q || typing) return;
    setInput("");
    setMessages((prev) => [...prev, { id: typingId(), role: "user", content: q }]);
    setTyping(true);
    try {
      const res = await fetch(`${base}/api/copilot/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, history, last_topic: lastTopic, enhance }),
      });
      const data: CopilotResponse = await res.json();
      if (!res.ok) {
        throw new Error(typeof data === "object" && data && "detail" in data ? String((data as { detail: unknown }).detail) : `chat failed (${res.status})`);
      }
      setMessages((prev) => [
        ...prev,
        {
          id: typingId(),
          role: "assistant",
          content: data.answer,
          topic: data.topic,
          followups: data.followups,
          meta: enhance && !data.used_fallback ? "swarm" : "kb",
        },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: typingId(),
          role: "assistant",
          content: `I couldn't reach the copilot API (${e instanceof Error ? e.message : "network error"}). The backend may be cold-starting — try again in ten seconds.`,
          error: true,
        },
      ]);
    } finally {
      setTyping(false);
    }
  };

  const resetSession = () => {
    setMessages([INTRO]);
    setInput("");
    setTyping(false);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-pressed={open}
        className="fixed bottom-5 left-5 z-50 flex items-center gap-2 rounded-full border border-violet-800 bg-slate-900/95 backdrop-blur px-4 py-2.5 text-[11px] font-bold text-violet-300 shadow-[0_8px_30px_rgba(2,8,23,0.6)] transition hover:border-violet-600 hover:bg-violet-950 cursor-pointer"
      >
        <Bot className="w-4 h-4" />
        {open ? "CLOSE" : "COPILOT"}
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
      </button>

      {open ? (
        <div className="fixed inset-x-3 bottom-20 sm:inset-x-auto sm:left-5 sm:bottom-20 z-50 w-auto sm:w-[400px] max-w-[calc(100vw-1.5rem)] rounded-2xl border border-slate-700 bg-slate-950/95 backdrop-blur shadow-[0_20px_70px_rgba(2,8,23,0.8)] overflow-hidden flex flex-col">
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-violet-950 border border-violet-800 text-violet-300">
                <Sparkles className="w-3.5 h-3.5" />
              </div>
              <div>
                <div className="text-xs font-bold uppercase tracking-[0.2em] text-slate-100">Copilot</div>
                <div className="text-[9px] text-slate-500">grounded answers · swarm-scored when you type</div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={resetSession}
                aria-label="Reset conversation"
                title="Reset conversation"
                className="rounded p-1 text-slate-500 hover:text-slate-200 hover:bg-slate-900 cursor-pointer"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close copilot"
                className="rounded p-1 text-slate-500 hover:text-slate-200 hover:bg-slate-900 cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 space-y-3 px-4 py-4 max-h-[55vh] overflow-y-auto min-h-[200px]">
            {messages.map((msg) =>
              msg.role === "user" ? (
                <div key={msg.id} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-md border border-cyan-800 bg-cyan-950/60 px-3 py-2 text-[11px] text-cyan-100">
                    {msg.content}
                  </div>
                </div>
              ) : (
                <div key={msg.id} className="space-y-1.5">
                  <div
                    className={`max-w-[90%] rounded-2xl rounded-bl-md border px-3 py-2.5 ${
                      msg.error
                        ? "border-rose-800 bg-rose-950/40 text-rose-200"
                        : "border-slate-700 bg-slate-900/80"
                    }`}
                  >
                    {renderRich(msg.content)}
                    {msg.topic && !msg.error && msg.topic !== "_fallback" ? (
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        {msg.meta === "swarm" ? (
                          <span className="inline-flex items-center gap-1 rounded border border-violet-700 bg-violet-950/50 px-1.5 py-0.5 text-[9px] text-violet-300">
                            <Sparkles className="w-2.5 h-2.5" /> swarm-scored
                          </span>
                        ) : (
                          <span className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 text-[9px] text-slate-400">
                            knowledge base
                          </span>
                        )}
                      </div>
                    ) : null}
                  </div>
                  {msg.followups && msg.followups.length ? (
                    <div className="flex flex-wrap gap-1.5 pl-1">
                      {msg.followups.map((chip) => (
                        <button
                          key={chip.topic}
                          type="button"
                          onClick={() => void ask(chip.title, false)}
                          className="rounded-full border border-violet-800 bg-violet-950/30 px-2.5 py-1 text-[10px] text-violet-300 hover:bg-violet-900/50 cursor-pointer"
                        >
                          {chip.title}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              )
            )}

            {typing ? (
              <div className="flex gap-1 pl-1 pt-1">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="inline-block w-1.5 h-1.5 rounded-full bg-violet-400 animate-bounce"
                    style={{ animationDelay: `${i * 150}ms` }}
                  />
                ))}
              </div>
            ) : null}

            {messages.length === 1 ? (
              <div className="grid grid-cols-2 gap-1.5 pt-1">
                {QUICK_QUESTIONS.map((item) => (
                  <button
                    key={item.q}
                    type="button"
                    onClick={() => void ask(item.q, item.enhance)}
                    className="rounded-lg border border-slate-800 bg-slate-900/60 px-2 py-1.5 text-left text-[10px] text-slate-300 hover:border-violet-600 hover:text-violet-200 cursor-pointer"
                  >
                    {item.q}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <div className="border-t border-slate-800 px-3 py-2.5 flex items-center gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void ask(input, true);
              }}
              placeholder="Ask about the platform…"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-[11px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-violet-600"
            />
            <button
              type="button"
              onClick={() => void ask(input, true)}
              disabled={!input.trim() || typing}
              aria-label="Send"
              className="rounded-lg bg-violet-600 hover:bg-violet-500 text-slate-950 p-2 disabled:bg-slate-800 disabled:text-slate-500 cursor-pointer disabled:cursor-not-allowed"
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}