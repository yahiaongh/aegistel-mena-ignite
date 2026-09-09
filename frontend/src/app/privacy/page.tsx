import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy — AegisTel",
  description: "How AegisTel handles telecom data in this demo: purpose, minimization, retention, deletion, operator access, and the synthetic-data statement.",
};

const sections: { title: string; body: string }[] = [
  {
    title: "Purpose of telecom data",
    body:
      "AegisTel is a fraud-decision engine for payments. For each audited transaction it reads a minimal set of operator signals from the CAMARA network APIs (SIM-swap status, number-binding verification, device location verification, roaming status, reachability, congestion level, and, on confirmed risk, an optional QoD slice). These signals are used for exactly one purpose: deciding whether the SIM and device behind a payment really belong to the account holder, and defending MENA payments against account takeover (ATO) and SIM-swap fraud.",
  },
  {
    title: "Data minimization",
    body:
      "No message content, contact lists, call detail records, or payment credentials are collected. Only the signals listed above are read, and only for the MSISDN in the audit request, for the duration of that request. Synthetic-demo transactions do the same. Bounded planning means only the signals a transaction type actually needs are fetched.",
  },
  {
    title: "Retention",
    body:
      "Audit verdicts are recorded in the incident store (default: a local JSONL file, `data/local_memory.jsonl`, optionally mirrored to mem0/Qdrant when `AEGISTEL_LIVE_MEMORY=1` is set). The operator history panel reads this store. Feedback submissions are kept for product iteration.",
  },
  {
    title: "Deletion",
    body:
      "An operator with the admin token can call `POST /api/memory/clear-all` to wipe the incident store, or delete the JSONL file directly (the endpoint clears both local and remote stores). Feedback records can be removed by deleting `data/feedback.jsonl`. No per-request telecom payloads are persisted beyond the decision record above.",
  },
  {
    title: "Operator access",
    body:
      "History, memory-wipe, provider diagnostics, and feedback readback are operator-only endpoints protected by `AEGISTEL_ADMIN_KEY` (Bearer / `X-Admin-Token` / `?token=`). They fail closed (401 without a token, 503 when the key is unconfigured).",
  },
  {
    title: "Synthetic-demo-data statement",
    body:
      "The demo numbers `+99999991000`, `+99999991001`, `+99999991002`, `+99999991003`, and `+9999123456` are synthetic Nokia NaC sandbox-simulator subscribers. Their 'incident histories' are artifacts of prior demo runs, never real subscriber data, and they are excluded from verdict memory-weighting. Any live keys used during evaluation are non-prod credentials; no production telephony identities are involved in this submission.",
  },
];

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 px-6 py-10">
      <div className="max-w-2xl mx-auto space-y-6">
        <Link href="/" className="text-[11px] uppercase tracking-[0.2em] text-cyan-400 hover:text-cyan-300">
          ← Back to AegisTel dashboard
        </Link>
        <h1 className="text-2xl font-bold tracking-wide">AegisTel privacy notice</h1>
        <p className="text-sm text-slate-400">
          Demo submission for the GSMA MENA Ignite Hackathon 2026 (Theme 4 — Secure
          Fintech, Payments &amp; Anti-Fraud Innovation). This notice describes how
          telecom data is handled in this demonstration.
        </p>
        <div className="space-y-4">
          {sections.map((section) => (
            <section key={section.title} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
              <h2 className="text-sm font-bold text-cyan-300 mb-2">{section.title}</h2>
              <p className="text-[13px] leading-relaxed text-slate-300">{section.body}</p>
            </section>
          ))}
        </div>
        <p className="text-[11px] text-slate-500">
          Full technical details live in <code className="text-slate-400">PRIVACY.md</code> at the repository root.
        </p>
      </div>
    </main>
  );
}