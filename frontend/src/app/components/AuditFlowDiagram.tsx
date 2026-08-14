"use client";

import React from "react";
import {
  Activity,
  CheckCircle2,
  Cpu,
  Globe,
  Loader2,
  MapPin,
  RadioTower,
  ShieldCheck,
  Smartphone,
  Wifi,
  Zap,
} from "lucide-react";

export type FlowState = "pending" | "running" | "ok" | "flag" | "error";

export interface FlowTool {
  name: string;
  state: FlowState;
  source?: string;
  durationMs?: number;
}

interface AuditFlowDiagramProps {
  phase: "idle" | "running" | "done" | "error";
  tools: FlowTool[];
  specialist: FlowState;
  auditor: FlowState;
  llmModel?: string | null;
  verdict?: { status?: string; risk?: string } | null;
}

const TOOL_META: Record<string, { short: string; icon: React.ReactNode; label: string }> = {
  check_sim_swap: { short: "SIM", icon: <Smartphone className="w-3 h-3" />, label: "SIM SWAP" },
  verify_location: { short: "LOC", icon: <MapPin className="w-3 h-3" />, label: "GEOFENCE" },
  check_roaming_status: { short: "ROM", icon: <Globe className="w-3 h-3" />, label: "ROAMING" },
  check_device_reachability: { short: "RCH", icon: <Wifi className="w-3 h-3" />, label: "REACHABILITY" },
  verify_number: { short: "NV", icon: <ShieldCheck className="w-3 h-3" />, label: "NUMBER VRFY" },
  get_congestion_insights: { short: "CG", icon: <Activity className="w-3 h-3" />, label: "CONGESTION" },
  create_qod_session: { short: "QoD", icon: <Zap className="w-3 h-3" />, label: "QoD SLICE" },
};

const STATE_STYLES: Record<FlowState, { box: string; text: string; ring: string; dot: string }> = {
  pending: { box: "border-slate-800 bg-slate-950/60", text: "text-slate-500", ring: "", dot: "bg-slate-700" },
  running: { box: "border-cyan-600 bg-cyan-950/40", text: "text-cyan-300", ring: "ring-2 ring-cyan-500/40", dot: "bg-cyan-400 animate-pulse" },
  ok: { box: "border-emerald-700 bg-emerald-950/30", text: "text-emerald-300", ring: "", dot: "bg-emerald-400" },
  flag: { box: "border-amber-600 bg-amber-950/30", text: "text-amber-300", ring: "", dot: "bg-amber-400" },
  error: { box: "border-rose-700 bg-rose-950/30", text: "text-rose-300", ring: "", dot: "bg-rose-400" },
};

const STATE_LABEL: Record<FlowState, string> = {
  pending: "PENDING",
  running: "EXECUTING",
  ok: "HEALTHY",
  flag: "FLAGGED",
  error: "ERROR",
};

function Beam({ active }: { active: boolean }) {
  return (
    <div className={`h-0.5 flex-1 min-w-3 relative overflow-hidden rounded ${active ? "bg-slate-700" : "bg-slate-800"}`}>
      {active ? (
        <span className="absolute inset-y-0 left-0 w-6 bg-gradient-to-r from-transparent via-cyan-400 to-transparent animate-[flowbeam_0.9s_linear_infinite]" />
      ) : null}
    </div>
  );
}

function StageNode({ label, state, meta }: { label: string; state: FlowState; meta?: string }) {
  const s = STATE_STYLES[state];
  return (
    <div className={`flex flex-col items-center gap-1 px-1 py-2 rounded-lg border ${s.box} ${s.ring} min-w-14 sm:min-w-[72px]`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      <span className={`text-[9px] font-bold tracking-wider text-center ${s.text}`}>{label}</span>
      {meta ? <span className="text-[8px] text-slate-500 text-center leading-tight break-all max-w-full px-0.5">{meta}</span> : null}
    </div>
  );
}

function MobileStageRow({ label, state, meta }: { label: string; state: FlowState; meta?: string }) {
  const s = STATE_STYLES[state];
  return (
    <div className={`flex items-center justify-between gap-2 rounded-lg border px-3 py-2 ${s.box} ${s.ring}`}>
      <span className="flex items-center gap-2 min-w-0">
        <span className={`w-2 h-2 rounded-full shrink-0 ${s.dot}`} />
        <span className={`text-[10px] font-bold tracking-wider ${s.text}`}>{label}</span>
      </span>
      <span className="flex items-center gap-2 shrink-0">
        {meta ? <span className="text-[9px] text-slate-400 truncate max-w-28">{meta}</span> : null}
        <span className="text-[8px] font-bold tracking-wider text-slate-500">{STATE_LABEL[state]}</span>
      </span>
    </div>
  );
}

function VerticalConnector() {
  return (
    <div className="flex justify-center py-0.5">
      <span className="h-2 w-0.5 rounded bg-slate-700/80" />
    </div>
  );
}

export default function AuditFlowDiagram({ phase, tools, specialist, auditor, llmModel, verdict }: AuditFlowDiagramProps) {
  const running = phase === "running";
  const verdictState: FlowState = phase === "running" ? "running" : phase === "done" && verdict ? "ok" : "pending";
  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 space-y-3 font-mono">
      <div className="flex items-center justify-between">
        <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-400 flex items-center gap-2">
          <RadioTower className="w-3.5 h-3.5 text-cyan-400" /> Live Request Flow
        </div>
        <span className={`text-[9px] px-2 py-0.5 rounded border font-bold ${running ? "border-cyan-700 bg-cyan-950 text-cyan-300" : phase === "done" ? "border-emerald-800 bg-emerald-950 text-emerald-300" : phase === "error" ? "border-rose-800 bg-rose-950 text-rose-300" : "border-slate-700 text-slate-500"}`}>
          {running ? "AUDIT IN FLIGHT" : phase === "done" ? "COMPLETE" : phase === "error" ? "FAILED" : "STANDBY"}
        </span>
      </div>

      {/* Vertical flow — mobile / tablet */}
      <div className="lg:hidden space-y-1.5">
        <MobileStageRow label="TRANSACTION" state={running || phase === "done" ? "ok" : "pending"} />
        <VerticalConnector />

        <div className={`rounded-lg border p-2 ${running ? "border-cyan-700/60 bg-slate-950/80" : "border-slate-800 bg-slate-950/60"}`}>
          <div className="flex items-center gap-2 mb-1.5">
            <Cpu className="w-3 h-3 text-cyan-400 shrink-0" />
            <span className="text-[10px] font-bold tracking-wider text-slate-200">CAMARA TOOLS</span>
            <span className="text-[8px] text-slate-500">({tools.filter((t) => t.state !== "pending").length}/{tools.length})</span>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5">
            {tools.map((tool) => {
              const meta = TOOL_META[tool.name] ?? { short: tool.name.slice(0, 3).toUpperCase(), icon: <Cpu className="w-3 h-3" />, label: tool.name.toUpperCase() };
              const s = STATE_STYLES[tool.state];
              return (
                <div
                  key={tool.name}
                  title={`${tool.name}${tool.durationMs ? ` — ${tool.durationMs}ms` : ""}${tool.source ? ` — ${tool.source}` : ""}`}
                  className={`flex items-center gap-1.5 rounded border px-2 py-1.5 min-w-0 ${s.box} ${s.ring}`}
                >
                  <span className="shrink-0">{meta.icon}</span>
                  <span className="text-[8px] font-bold tracking-wide truncate">{meta.label}</span>
                </div>
              );
            })}
          </div>
        </div>
        <VerticalConnector />

        <MobileStageRow label="SPECIALISTS" state={specialist} meta={llmModel ?? "deterministic"} />
        <VerticalConnector />
        <MobileStageRow label="AUDITOR" state={auditor} meta={llmModel ?? "deterministic"} />
        <VerticalConnector />

        <MobileStageRow
          label="VERDICT"
          state={verdictState}
          meta={verdict ? `${verdict.status} / ${verdict.risk}` : undefined}
        />
      </div>

      {/* Horizontal flow — desktop */}
      <div className="hidden lg:flex items-stretch gap-1 overflow-x-auto pb-1">
        <StageNode label="TRANSACTION" state={running || phase === "done" ? "ok" : "pending"} meta="" />

        <Beam active={running} />

        <div className={`flex items-center gap-1.5 px-2 py-2 rounded-lg border ${running ? "border-cyan-700/60 bg-slate-950/80" : "border-slate-800 bg-slate-950/60"}`}>
          <Cpu className="w-3 h-3 text-cyan-400 shrink-0" />
          <div className="grid grid-cols-4 gap-1">
            {tools.map((tool) => {
              const meta = TOOL_META[tool.name] ?? { short: tool.name.slice(0, 3).toUpperCase(), icon: <Cpu className="w-3 h-3" />, label: tool.name.toUpperCase() };
              const s = STATE_STYLES[tool.state];
              return (
                <div key={tool.name} title={`${tool.name}${tool.durationMs ? ` — ${tool.durationMs}ms` : ""}${tool.source ? ` — ${tool.source}` : ""}`} className={`flex items-center gap-1 rounded border px-1.5 py-1 ${s.box} ${s.ring}`}>
                  {meta.icon}
                  <span className="hidden lg:inline text-[8px] font-bold tracking-wide">{meta.label}</span>
                  <span className="lg:hidden text-[8px] font-bold">{meta.short}</span>
                </div>
              );
            })}
          </div>
        </div>

        <Beam active={running} />

        <StageNode label="SPECIALISTS" state={specialist} meta={llmModel ?? "deterministic"} />

        <Beam active={running} />

        <StageNode label="AUDITOR" state={auditor} meta={llmModel ?? "deterministic"} />

        <Beam active={running} />

        <div className={`flex flex-col items-center gap-1 px-2 py-2 rounded-lg border min-w-16 sm:min-w-[88px] ${
          phase === "done" && verdict
            ? verdict.status === "APPROVED" ? "border-emerald-700 bg-emerald-950/30 text-emerald-300"
              : verdict.status === "STEP_UP_REQUIRED" ? "border-amber-600 bg-amber-950/30 text-amber-300"
              : "border-rose-700 bg-rose-950/30 text-rose-300"
            : "border-slate-800 bg-slate-950/60 text-slate-500"
        }`}>
          {phase === "running" ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin text-cyan-400" />
          ) : verdict ? (
            <CheckCircle2 className="w-3.5 h-3.5" />
          ) : (
            <span className="w-1.5 h-1.5 rounded-full bg-slate-700" />
          )}
          <span className="text-[9px] font-bold tracking-wider">VERDICT</span>
          {verdict ? (
            <span className="text-[8px] text-slate-400 leading-tight text-center">{verdict.status} / {verdict.risk}</span>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[8px] text-slate-200">
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-slate-700" /> pending</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" /> executing</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" /> healthy</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-amber-400" /> flagged</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-rose-400" /> risky</span>
      </div>
    </div>
  );
}