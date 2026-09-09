"use client";

import React, { useMemo, useState } from "react";
import {
  CheckCircle2,
  Inbox,
  KeyRound,
  Lock,
  MessageSquare,
  RefreshCw,
  Send,
  Star,
  TrendingUp,
  X,
} from "lucide-react";

export interface FeedbackWidgetProps {
  apiBase?: string | null;
  msisdn?: string;
  lastStatus?: string | null;
  lastRisk?: string | null;
  drillGrade?: string | null;
}

interface RatingsSummary {
  field: string;
  count: number;
  avg: number | null;
  distribution: Record<string, number>;
}

interface FeedbackAdminData {
  summary: {
    count: number;
    ratings_submitted: number;
    per_feature: Record<string, RatingsSummary>;
    moods: Record<string, number>;
    roles: Record<string, number>;
  };
  latest: Array<{
    id: string;
    created_at: string;
    ratings: Record<string, number>;
    mood?: string | null;
    role: string;
    comment: string;
    context: Record<string, string | number | boolean | null>;
  }>;
}

const FEATURES: Array<{ key: string; label: string; hint: string }> = [
  { key: "verdict", label: "Fraud verdict quality", hint: "Was the decision right?" },
  { key: "explainability", label: "Explains the WHY", hint: "Could you see the evidence?" },
  { key: "ui", label: "UI / experience", hint: "Look, feel, flow" },
  { key: "voice", label: "Voice briefing", hint: "The audio narration" },
  { key: "drill", label: "Red-team drill", hint: "Adversary simulation" },
  { key: "performance", label: "Speed / responsiveness", hint: "Deadlines, latency" },
];

const MOODS = ["😍", "😊", "😐", "😤"];
const ROLES = ["judge", "investor", "operator", "user", "other"];

function StarRow({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [hover, setHover] = useState(0);
  return (
    <div className="flex items-center gap-1" onMouseLeave={() => setHover(0)}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          aria-label={`${n} star${n > 1 ? "s" : ""}`}
          onMouseEnter={() => setHover(n)}
          onClick={() => onChange(n)}
          className="p-0.5 transition-transform hover:scale-110 cursor-pointer"
        >
          <Star
            className={`w-5 h-5 ${
              n <= (hover || value)
                ? hover !== 0 && n <= hover && n > value
                  ? "text-amber-300 fill-amber-300"
                  : "text-amber-400 fill-amber-400"
                : "text-slate-700"
            }`}
          />
        </button>
      ))}
      <span className="ml-2 text-[10px] text-slate-400 w-6">{value > 0 ? value : ""}</span>
    </div>
  );
}

export default function FeedbackWidget({ apiBase, msisdn, lastStatus, lastRisk, drillGrade }: FeedbackWidgetProps) {
  const base = apiBase ?? "";
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"rate" | "ops">("rate");

  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [mood, setMood] = useState<string | null>(null);
  const [role, setRole] = useState<string>("");
  const [comment, setComment] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [submittedId, setSubmittedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [token, setToken] = useState("");
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminData, setAdminData] = useState<FeedbackAdminData | null>(null);
  const [adminError, setAdminError] = useState<string | null>(null);

  const ratedCount = Object.values(ratings).filter((v) => v > 0).length;
  const canSubmit = ratedCount > 0 && !submitting;

  const starScale = useMemo<Record<number, string>>(() => ({ 1: "1 — rough", 2: "2", 3: "3 — fine", 4: "4", 5: "5 — flawless" }), []);

  const reset = () => {
    setRatings({});
    setMood(null);
    setRole("");
    setComment("");
    setSubmitting(false);
    setSubmittedId(null);
    setError(null);
  };

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    const context: Record<string, string | number | null> = {};
    if (msisdn) context.msisdn = msisdn;
    if (lastStatus) context.last_status = lastStatus;
    if (lastRisk) context.last_risk = lastRisk;
    if (drillGrade) context.drill_grade = drillGrade;
    if (typeof window !== "undefined") context.device = `${navigator.userAgent ?? ""}`.slice(0, 120);
    try {
      const res = await fetch(`${base}/api/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ratings,
          mood,
          role: role || "user",
          comment,
          context,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : `Submit failed (${res.status})`);
      }
      setSubmittedId(data.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the API");
    } finally {
      setSubmitting(false);
    }
  };

  const loadAdmin = async () => {
    if (!token.trim()) {
      setAdminError("Enter the ops passcode first.");
      return;
    }
    setAdminLoading(true);
    setAdminError(null);
    try {
      const res = await fetch(`${base}/api/feedback`, {
        headers: { Authorization: `Bearer ${token.trim()}` },
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : `Load failed (${res.status})`);
      }
      setAdminData(data);
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not load feedback");
      setAdminData(null);
    } finally {
      setAdminLoading(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-pressed={open}
        className="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full border border-cyan-800 bg-slate-900/95 backdrop-blur px-4 py-2.5 text-[11px] font-bold text-cyan-300 shadow-[0_8px_30px_rgba(2,8,23,0.6)] transition hover:border-cyan-600 hover:bg-cyan-950 cursor-pointer"
      >
        <MessageSquare className="w-4 h-4" />
        {open ? "CLOSE" : "FEEDBACK"}
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
      </button>

      {open ? (
        <div className="fixed inset-x-3 bottom-20 sm:inset-x-auto sm:right-5 sm:bottom-20 z-50 w-auto sm:w-[420px] max-w-[calc(100vw-1.5rem)] rounded-2xl border border-slate-700 bg-slate-950/95 backdrop-blur shadow-[0_20px_70px_rgba(2,8,23,0.8)] overflow-hidden">
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
            <div className="flex items-center gap-2">
              <MessageSquare className="w-4 h-4 text-cyan-400" />
              <span className="text-xs font-bold uppercase tracking-[0.2em] text-slate-200">
                {tab === "rate" ? "Judge the build" : "Ops readback"}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => {
                  setTab("rate");
                  reset();
                }}
                className={`rounded px-2 py-1 text-[10px] font-bold ${tab === "rate" ? "bg-cyan-950 text-cyan-300 border border-cyan-800" : "text-slate-500 hover:text-slate-300"}`}
              >
                RATE
              </button>
              <button
                type="button"
                onClick={() => setTab("ops")}
                className={`rounded px-2 py-1 text-[10px] font-bold flex items-center gap-1 ${tab === "ops" ? "bg-cyan-950 text-cyan-300 border border-cyan-800" : "text-slate-500 hover:text-slate-300"}`}
              >
                <Lock className="w-3 h-3" /> OPS
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close feedback"
                className="ml-1 rounded p-1 text-slate-500 hover:text-slate-200 hover:bg-slate-900 cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {tab === "rate" ? (
            <div className="px-4 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
              {submittedId ? (
                <div className="py-8 text-center space-y-3">
                  <CheckCircle2 className="w-10 h-10 text-emerald-400 mx-auto" />
                  <div className="text-sm font-bold text-slate-100">Logged.</div>
                  <div className="text-[11px] text-slate-400">
                    Referral <span className="font-mono text-cyan-300">{submittedId}</span> — every stroke helps the build.
                  </div>
                  <button
                    type="button"
                    onClick={reset}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-[11px] font-bold text-slate-300 hover:bg-slate-900 cursor-pointer"
                  >
                    SUBMIT ANOTHER
                  </button>
                </div>
              ) : (
                <>
                  <div className="space-y-3">
                    {FEATURES.map((feature) => (
                      <div key={feature.key} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2.5">
                        <div className="min-w-0">
                          <div className="text-[11px] font-bold text-slate-200">{feature.label}</div>
                          <div className="text-[10px] text-slate-500">{feature.hint}</div>
                        </div>
                        <StarRow value={ratings[feature.key] ?? 0} onChange={(v) => setRatings((prev) => ({ ...prev, [feature.key]: v }))} />
                      </div>
                    ))}
                  </div>

                  <div>
                    <div className="mb-2 text-[10px] uppercase tracking-[0.2em] text-slate-400">Overall vibe</div>
                    <div className="flex gap-2">
                      {MOODS.map((m) => (
                        <button
                          key={m}
                          type="button"
                          onClick={() => setMood(mood === m ? null : m)}
                          aria-pressed={mood === m}
                          className={`text-xl p-1.5 rounded-lg border transition cursor-pointer ${mood === m ? "border-cyan-500 bg-cyan-950/60" : "border-slate-800 bg-slate-900/50 hover:border-slate-600"}`}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <div className="mb-2 text-[10px] uppercase tracking-[0.2em] text-slate-400">You are a…</div>
                    <select
                      value={role}
                      onChange={(e) => setRole(e.target.value)}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-[11px] text-slate-200 outline-none focus:border-cyan-600"
                    >
                      <option value="">— select (optional) —</option>
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <div className="mb-2 text-[10px] uppercase tracking-[0.2em] text-slate-400">What would make it 5 stars?</div>
                    <textarea
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      rows={3}
                      maxLength={2000}
                      placeholder="Optional — the sharpest input wins"
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-[11px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-cyan-600 resize-none"
                    />
                  </div>

                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => void submit()}
                      disabled={!canSubmit}
                      className={`flex-1 inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-[11px] font-bold transition cursor-pointer ${canSubmit ? "bg-cyan-600 hover:bg-cyan-500 text-slate-950" : "bg-slate-800 text-slate-500 cursor-not-allowed"}`}
                    >
                      <Send className="w-3.5 h-3.5" />
                      {submitting ? "SENDING…" : ratedCount === 0 ? "RATE SOMETHING FIRST" : "SHIP IT"}
                    </button>
                    <span className="text-[9px] text-slate-600 whitespace-nowrap">{starScale[Math.max(...Object.values(ratings), 1)]}</span>
                  </div>
                  {error ? <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-[10px] text-rose-300">{error}</div> : null}
                </>
              )}
            </div>
          ) : (
            <div className="px-4 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
              <div className="flex gap-2">
                <input
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Ops passcode"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void loadAdmin();
                  }}
                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-[11px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-cyan-600"
                />
                <button
                  type="button"
                  onClick={() => void loadAdmin()}
                  disabled={adminLoading}
                  className="inline-flex items-center gap-2 rounded-lg border border-cyan-800 bg-cyan-950 px-3 py-2 text-[11px] font-bold text-cyan-300 hover:bg-cyan-900 cursor-pointer disabled:opacity-50"
                >
                  {adminLoading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <KeyRound className="w-3.5 h-3.5" />}
                  OPEN
                </button>
              </div>
              {adminError ? <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-[10px] text-rose-300">{adminError}</div> : null}

              {adminData ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                    <div className="flex items-center gap-2 text-[11px] font-bold text-slate-200">
                      <Inbox className="w-3.5 h-3.5 text-cyan-400" />
                      {adminData.summary.count} responses
                    </div>
                    <span className="text-[10px] text-slate-400">{adminData.summary.ratings_submitted} ratings logged</span>
                  </div>

                  <div className="space-y-2">
                    {Object.entries(adminData.summary.per_feature).map(([key, stat]) => (
                      <div key={key} className="space-y-1">
                        <div className="flex items-center justify-between text-[10px]">
                          <span className="font-bold text-slate-300 uppercase tracking-wider">{stat.field}</span>
                          <span className="text-slate-500">
                            {stat.count}× {stat.avg != null ? `avg ${stat.avg}` : "—"}
                          </span>
                        </div>
                        <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                          <div
                            className={`h-full rounded-full ${(stat.avg ?? 0) >= 4 ? "bg-emerald-400" : (stat.avg ?? 0) >= 3 ? "bg-amber-400" : "bg-rose-400"}`}
                            style={{ width: `${((stat.avg ?? 0) / 5) * 100}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="flex gap-4 flex-wrap">
                    <div>
                      <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-[0.2em] text-slate-400">
                        <TrendingUp className="w-3 h-3" /> Moods
                      </div>
                      <div className="flex gap-1.5 text-lg">
                        {Object.entries(adminData.summary.moods).length ? (
                          Object.entries(adminData.summary.moods).map(([memoji, n]) => (
                            <span key={memoji} title={`${n}`} className="text-base">
                              {memoji}
                              <span className="ml-0.5 align-middle text-[9px] text-slate-500">{n}</span>
                            </span>
                          ))
                        ) : (
                          <span className="text-[10px] text-slate-600">none yet</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-[10px] uppercase tracking-[0.2em] text-slate-400">Roles</div>
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(adminData.summary.roles).map(([r, n]) => (
                          <span key={r} className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[9px] text-slate-300">
                            {r}: {n}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>

                  <div>
                    <div className="mb-2 flex items-center gap-1 text-[10px] uppercase tracking-[0.2em] text-slate-400">
                      <Inbox className="w-3 h-3" /> Latest notes
                    </div>
                    <div className="space-y-2">
                      {adminData.latest.length ? (
                        adminData.latest.map((r) => (
                          <div key={r.id} className="rounded-lg border border-slate-800 bg-slate-900/70 p-3 space-y-1.5">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-[9px] text-slate-500">
                                {new Date(r.created_at).toLocaleString()} · {r.role}
                              </span>
                              <span className="text-[9px] font-mono text-cyan-400">{r.id}</span>
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {Object.entries(r.ratings).map(([k, v]) => (
                                <span key={k} className="rounded border border-cyan-900 bg-cyan-950/40 px-1.5 py-0.5 text-[9px] text-cyan-300">
                                  {k} ★{v}
                                </span>
                              ))}
                              {r.mood ? <span className="text-[9px] text-slate-400">{r.mood}</span> : null}
                            </div>
                            {r.comment ? <p className="text-[11px] text-slate-200 whitespace-pre-wrap break-words">{r.comment}</p> : null}
                            {r.context && Object.keys(r.context).length ? (
                              <div className="pt-1 border-t border-slate-900 text-[9px] text-slate-500 font-mono truncate">
                                {Object.entries(r.context)
                                  .slice(0, 5)
                                  .map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
                              </div>
                            ) : null}
                          </div>
                        ))
                      ) : (
                        <div className="rounded border border-dashed border-slate-800 py-4 text-center text-[10px] text-slate-600">
                          No feedback yet — ship it first.
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </>
  );
}