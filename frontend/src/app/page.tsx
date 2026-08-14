// frontend/src/app/page.tsx
"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  ChevronDown,
  ChevronUp,
  DollarSign,
  Globe,
  MapPin,
  Phone,
  Radio,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  Sliders,
  Smartphone,
  Terminal,
  Volume2,
  Wifi,
  Zap,
} from "lucide-react";
import {
  BarChart,
  Bar,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  PieChart,
  Pie,
  Cell,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
} from "recharts";
import ThreatStream from "./components/ThreatStream";
import AuditFlowDiagram, { type FlowState, type FlowTool } from "./components/AuditFlowDiagram";

interface ToolResult {
  name: string;
  success: boolean;
  source: string;
  duration_ms?: number | null;
  payload: Record<string, unknown>;
}

interface AgentStep {
  agent: string;
  action: string;
  thought: string;
  status: string;
  detail: string;
  model?: string;
  provider?: string;
}

interface NokiaTelemetry {
  number_verification_match?: boolean | null;
  number_verification_status?: string;
  max_congestion_level?: string | null;
  sim_swap_detected: boolean;
  last_sim_swap_date?: string;
  location_verification_match: boolean;
  location_accuracy_meters: number;
  geofence_status: "VERIFIED" | "NOT_VERIFIED" | "PARTIAL" | "UNKNOWN" | string;
  roaming_status: string;
  roaming_country?: string;
  reachability_status: string;
  qod_session_active: boolean;
  qod_profile?: string;
  qod_status?: string;
  evidence_strength?: string;
  confidence?: number;
  cross_border_risk?: boolean;
  tool_results?: ToolResult[];
}

interface AuditResponse {
  msisdn: string;
  amount: number;
  transaction_type: string;
  risk_score: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  status: "APPROVED" | "REJECTED" | "BLOCKED" | "STEP_UP_REQUIRED" | "MANUAL_REVIEW";
  telemetry: NokiaTelemetry;
  reasoning: string;
  recommended_action: string;
  agent_trace: AgentStep[];
  used_fallback?: boolean;
  raw_output?: string | null;
}

interface LiveEvent {
  id: string;
  type: string;
  message: string;
  stage?: string;
  detail?: string;
}

interface HistoryIncident {
  timestamp?: string;
  status?: string;
  risk_score?: string;
  amount?: number;
  roaming_status?: string;
}

interface HistoryResponse {
  msisdn: string;
  count: number;
  incidents: HistoryIncident[];
}

interface DrillPlay {
  id: string;
  name: string;
  archetype: string;
  intent: string;
  threat_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  verdict_status: string;
  defense_risk: string;
  outcome: "BLOCKED" | "ESCALATED" | "PARTIALLY_MISSED" | "MISSED" | "CLEARED" | "ERROR";
  detected_via: string[];
  used_fallback: boolean;
}

interface DrillBlindSpot {
  play_id: string;
  play_name: string;
  threat_level: string;
  outcome: string;
  note: string;
}

interface DrillReport {
  drill_id: string;
  generated_by_llm: boolean;
  playbook: string;
  readiness_score: number;
  grade: string;
  total_plays: number;
  outcomes: Record<string, number>;
  plays: DrillPlay[];
  blind_spots: DrillBlindSpot[];
  recommendations: string[];
}

const INITIAL_FLOW_TOOLS: FlowTool[] = [
  { name: "check_sim_swap", state: "pending" },
  { name: "verify_location", state: "pending" },
  { name: "check_roaming_status", state: "pending" },
  { name: "check_device_reachability", state: "pending" },
  { name: "verify_number", state: "pending" },
  { name: "get_congestion_insights", state: "pending" },
  { name: "create_qod_session", state: "pending" },
];

export default function AegisTelDashboard() {
  const [msisdn, setMsisdn] = useState("+99999991001");
  const [amount, setAmount] = useState("120000");
  const [lat, setLat] = useState("24.7136");
  const [lng, setLng] = useState("46.6753");
  const [geofenceRadius, setGeofenceRadius] = useState("2000");
  const [checkRoaming, setCheckRoaming] = useState(true);
  const [requestQoD, setRequestQoD] = useState(true);

  const [loading, setLoading] = useState(false);
  const [auditResult, setAuditResult] = useState<AuditResponse | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [, setTtsStatus] = useState<"idle" | "speaking" | "fallback" | "unavailable">("idle");
  const [requestStatus, setRequestStatus] = useState<"idle" | "requesting" | "ready" | "error">("idle");
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([]);
  const [activeSignalCount, setActiveSignalCount] = useState<number | null>(null);
  const [drillResult, setDrillResult] = useState<DrillReport | null>(null);
  const [drillLoading, setDrillLoading] = useState(false);
  const [drillError, setDrillError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [sessionStats, setSessionStats] = useState({ audits: 0, protectedAmount: 0 });
  const [sessionStatusCounts, setSessionStatusCounts] = useState<Record<string, number>>({
    APPROVED: 0,
    STEP_UP_REQUIRED: 0,
    MANUAL_REVIEW: 0,
    REJECTED: 0,
    BLOCKED: 0,
  });
  const [verdictVisible, setVerdictVisible] = useState(false);
  const [flowPhase, setFlowPhase] = useState<"idle" | "running" | "done" | "error">("idle");
  const [flowTools, setFlowTools] = useState<FlowTool[]>(INITIAL_FLOW_TOOLS);
  const [flowSpecialist, setFlowSpecialist] = useState<FlowState>("pending");
  const [flowAuditor, setFlowAuditor] = useState<FlowState>("pending");
  const [flowLlmModel, setFlowLlmModel] = useState<string | null>(null);
  const [flowVerdict, setFlowVerdict] = useState<{ status?: string; risk?: string } | null>(null);

  const activeAudioRef = useRef<HTMLAudioElement | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

  useEffect(() => {
    let cancelled = false;
    const fetchHealth = async () => {
      try {
        const res = await fetch(`${apiBase}/api/health`);
        if (cancelled) return;
        if (res.ok) {
          const data = await res.json();
          console.log("[Health] API health check response:", data);
          setActiveSignalCount(data.active_tool_count ?? null);
        } else {
          setActiveSignalCount(null);
        }
      } catch {
        if (!cancelled) setActiveSignalCount(null);
      }
    };

    void fetchHealth();
    const healthInterval = window.setInterval(() => void fetchHealth(), 20000);

    return () => {
      cancelled = true;
      window.clearInterval(healthInterval);
      if (activeAudioRef.current) {
        activeAudioRef.current.pause();
        activeAudioRef.current.currentTime = 0;
      }
    };
  }, [apiBase]);

  // Initialize session stats from localStorage to survive refreshes
  useEffect(() => {
    try {
      const raw = localStorage.getItem("aegistel_session_stats");
      const rawCounts = localStorage.getItem("aegistel_session_status_counts");
      // Defer state updates to avoid synchronous setState in effect
      setTimeout(() => {
        try {
          if (raw) setSessionStats(JSON.parse(raw));
          if (rawCounts) setSessionStatusCounts(JSON.parse(rawCounts));
        } catch {
          // ignore
        }
      }, 0);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("aegistel_session_stats", JSON.stringify(sessionStats));
      localStorage.setItem("aegistel_session_status_counts", JSON.stringify(sessionStatusCounts));
    } catch {
      // ignore
    }
  }, [sessionStats, sessionStatusCounts]);

  useEffect(() => {
    if (!auditResult) {
      // Defer to avoid synchronous setState in effect
      setTimeout(() => setVerdictVisible(false), 0);
      return;
    }
    const timer = window.setTimeout(() => setVerdictVisible(true), 220);
    return () => window.clearTimeout(timer);
  }, [auditResult]);

  useEffect(() => {
    if (!msisdn) return;
    const fetchHistory = async () => {
      try {
        const res = await fetch(`${apiBase}/api/v1/history/${encodeURIComponent(msisdn)}?limit=8`);
        if (!res.ok) throw new Error(`History fetch failed: ${res.status}`);
        const data: HistoryResponse = await res.json();
        setHistory(data);
      } catch {
        setHistory(null);
      }
    };

    void fetchHistory();
  }, [apiBase, msisdn, auditResult]);

  const playVoiceAlert = async (text: string) => {
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }

    setIsSpeaking(true);
    setTtsStatus("speaking");

    try {
      const formData = new FormData();
      formData.append("text", text);
      formData.append("voice", "ar-EG-ShakirNeural");

      const res = await fetch(`${apiBase}/api/audio/tts`, {
        method: "POST",
        body: formData,
      });

      console.log("[TTS] response status", res.status, "content-type", res.headers.get("content-type"));
      if (!res.ok) throw new Error(`TTS API error: ${res.status}`);

      const blob = await res.blob();
      console.log("[TTS] blob size", blob.size, "type", blob.type);
      const audioUrl = URL.createObjectURL(blob);
      const audio = new Audio(audioUrl);
      activeAudioRef.current = audio;

      audio.onended = () => {
        setIsSpeaking(false);
        setTtsStatus("idle");
        URL.revokeObjectURL(audioUrl);
      };
      await audio.play();
    } catch (error) {
      console.error("[TTS] playback failed", error);
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "en-US";
        utterance.rate = 0.95;
        utterance.onend = () => {
          setIsSpeaking(false);
          setTtsStatus("idle");
        };
        utterance.onerror = () => {
          setIsSpeaking(false);
          setTtsStatus("unavailable");
        };
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
        setTtsStatus("fallback");
      } else {
        setIsSpeaking(false);
        setTtsStatus("unavailable");
      }
    }
  };

  const applyAuditResult = (data: AuditResponse) => {
    setAuditResult(data);
    setRequestStatus("ready");
    setFlowPhase("done");
    setFlowVerdict({ status: data.status, risk: data.risk_score });
    setSessionStats((prev) => ({
      audits: prev.audits + 1,
      protectedAmount: prev.protectedAmount + (data.status !== "APPROVED" ? data.amount : 0),
    }));
    setSessionStatusCounts((prev) => ({
      ...prev,
      [data.status]: (prev[data.status] || 0) + 1,
    }));
    setLiveEvents((prev) => [
      ...prev,
      {
        id: `${Date.now()}-response`,
        type: "response",
        message: `Received full audit response for ${data.msisdn}`,
        stage: "completed",
        detail: data.reasoning,
      },
    ]);

    const briefText = `Audit complete for ${data.msisdn}. Verdict: ${data.status}. Risk level: ${data.risk_score}. ${data.reasoning}`;
    void playVoiceAlert(briefText);
  };

  const handleFlowProgress = (payload: Record<string, unknown>) => {
    const setToolState = (name: string, state: FlowState, extra?: Partial<FlowTool>) => {
      setFlowTools((prev) => prev.map((t) => (t.name === name ? { ...t, state, ...extra } : t)));
    };
    switch (payload.type) {
      case "tools:start":
        setFlowTools((prev) => prev.map((t) => ({ ...t, state: "running" as FlowState })));
        setLiveEvents((prev) => [
          ...prev,
          { id: `${Date.now()}-tools`, type: "tools", message: `Firing ${payload.count ?? 7} CAMARA tools in parallel`, stage: "telemetry" },
        ]);
        break;
      case "tool:done":
        setToolState(
          String(payload.tool ?? ""),
          payload.status === "ok" ? "ok" : "flag",
          { source: String(payload.source ?? ""), durationMs: Number(payload.duration_ms ?? 0) }
        );
        setLiveEvents((prev) => [
          ...prev,
          { id: `${Date.now()}-tool`, type: "tool", message: `${payload.tool} returned [${payload.source}] in ${payload.duration_ms}ms`, stage: "telemetry" },
        ]);
        break;
      case "memory:done":
        setLiveEvents((prev) => [
          ...prev,
          { id: `${Date.now()}-mem`, type: "memory", message: `Memory engine retrieved ${payload.incidents} prior incident(s)`, stage: "memory" },
        ]);
        break;
      case "synthesis:done":
        setLiveEvents((prev) => [
          ...prev,
          { id: `${Date.now()}-synth`, type: "synthesis", message: `Deterministic synthesis: ${payload.status} / ${payload.risk_score} (${payload.signal_count} signals)`, stage: "grounding" },
        ]);
        break;
      case "qod:start":
        setToolState("create_qod_session", "running");
        break;
      case "qod:done":
        setToolState("create_qod_session", "ok");
        break;
      case "llm:start":
        setFlowLlmModel(String(payload.specialist ?? "").split("/").pop() ?? null);
        setFlowSpecialist("running");
        setFlowAuditor("running");
        break;
      case "llm:done":
        setFlowSpecialist("ok");
        setFlowAuditor("ok");
        setFlowLlmModel(String(payload.specialist ?? "").split("/").pop() ?? null);
        setLiveEvents((prev) => [
          ...prev,
          { id: `${Date.now()}-llm`, type: "llm", message: `CrewAI completed on ${payload.specialist} (tier ${payload.tier})`, stage: "llm" },
        ]);
        break;
      case "llm:fallback":
        setFlowSpecialist("flag");
        setFlowAuditor("flag");
        setFlowLlmModel("deterministic");
        setLiveEvents((prev) => [
          ...prev,
          { id: `${Date.now()}-llmf`, type: "llm", message: `LLM layer skipped: ${payload.reason}`, stage: "fallback" },
        ]);
        break;
      case "crew:done":
        setFlowVerdict({ status: String(payload.status ?? ""), risk: String(payload.risk_score ?? "") });
        setLiveEvents((prev) => [
          ...prev,
          { id: `${Date.now()}-crew`, type: "crew", message: `Crew verdict: ${payload.status} / ${payload.risk_score}${payload.used_fallback ? " (deterministic fallback)" : ""}`, stage: "verdict" },
        ]);
        break;
    }
  };

  const runAudit = async (targetPhone = msisdn, targetAmount = amount, deterministicRetry = false) => {
    setLoading(true);
    setAuditResult(null);
    setRequestStatus("requesting");
    setFlowPhase("running");
    setFlowTools(INITIAL_FLOW_TOOLS);
    setFlowSpecialist("pending");
    setFlowAuditor("pending");
    setFlowLlmModel(null);
    setFlowVerdict(null);
    if (!deterministicRetry) setLiveEvents([]);

    const payload = {
      msisdn: targetPhone,
      amount: parseFloat(targetAmount),
      transaction_type: "WIRE_TRANSFER",
      current_location: {
        latitude: parseFloat(lat),
        longitude: parseFloat(lng),
      },
      request_qod_slice: requestQoD,
      metadata: {
        geofence_radius_meters: parseFloat(geofenceRadius),
        enforce_roaming_policy: checkRoaming,
        ...(deterministicRetry ? { _force_deterministic: true } : {}),
      },
    };

    if (deterministicRetry) {
      setLiveEvents((prev) => [
        ...prev,
        {
          id: `${Date.now()}-retry`,
          type: "request",
          message: "Stream dropped — rerunning on the deterministic engine (LLM providers rate-limited)",
          stage: "request",
        },
      ]);
    } else {
      setLiveEvents([
        {
          id: `${Date.now()}-request`,
          type: "request",
          message: "Opening live audit stream to AegisTel backend",
          stage: "request",
        },
      ]);
    }

    try {
      const res = await fetch(`${apiBase}/api/v1/audit/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok || !res.body) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ? `${body.error ?? "Request failed"}: ${body.detail}` : `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let streamError: string | null = null;

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          let eventName = "message";
          let dataLine = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
          }
          if (!dataLine) continue;
          if (eventName === "progress") {
            handleFlowProgress(JSON.parse(dataLine) as Record<string, unknown>);
          } else if (eventName === "result") {
            applyAuditResult(JSON.parse(dataLine) as AuditResponse);
            return;
          } else if (eventName === "error") {
            streamError = String((JSON.parse(dataLine) as { error?: string }).error ?? "Stream error");
          }
        }
      }

      if (streamError) throw new Error(streamError);
      throw new Error("Stream ended without a result");
    } catch (error) {
      if (!deterministicRetry) {
        // The LLM layer may have died mid-stream (provider rate limits) —
        // rerun on the deterministic engine so the intercept still lands a verdict.
        runAudit(targetPhone, targetAmount, true);
        return;
      }
      const message = error instanceof Error ? error.message : "Unknown error";
      setRequestStatus("error");
      setFlowPhase("error");
      setLiveEvents((prev) => [
        ...prev,
        {
          id: `${Date.now()}-error`,
          type: "error",
          message: "Audit request failed",
          stage: "error",
          detail: message,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectThreat = (threatMsisdn: string, threatAmount: number) => {
    setMsisdn(threatMsisdn);
    setAmount(threatAmount.toString());
    void runAudit(threatMsisdn, threatAmount.toString());
  };

  const runDrill = async () => {
    setDrillLoading(true);
    setDrillError(null);
    setLiveEvents((prev) => [
      ...prev,
      {
        id: `${Date.now()}-drill-start`,
        type: "drill",
        message: "Red team engaging: adversarial plays queued against the blue-team crew",
        stage: "drill",
      },
    ]);
    try {
      const res = await fetch(`${apiBase}/api/v1/drill/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ? `${body.error ?? "Request failed"}: ${body.detail}` : `HTTP ${res.status}`);
      }
      const data: DrillReport = await res.json();
      setDrillResult(data);
      setLiveEvents((prev) => [
        ...prev,
        {
          id: `${Date.now()}-drill-done`,
          type: "drill",
          message: `Drill complete: readiness ${data.readiness_score}% (${data.grade}) — ${data.blind_spots.length} blind spot(s) found`,
          stage: "completed",
          detail: data.blind_spots.length > 0 ? data.blind_spots[0].note : "No blind spots in this playbook",
        },
      ]);
      const drillBrief = `Adversarial drill complete. Defense readiness: ${data.readiness_score} percent, grade ${data.grade}. ${data.outcomes["BLOCKED"] ?? 0} plays blocked, ${data.outcomes["MISSED"] ?? 0} missed. ${data.blind_spots.length > 0 ? "The red team found a blind spot: " + data.blind_spots[0].play_name : "No blind spots found."}`;
      void playVoiceAlert(drillBrief);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      setDrillError(message);
      setLiveEvents((prev) => [
        ...prev,
        {
          id: `${Date.now()}-drill-error`,
          type: "error",
          message: "Adversarial drill failed",
          stage: "error",
          detail: message,
        },
      ]);
    } finally {
      setDrillLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-mono selection:bg-cyan-500 selection:text-slate-950 overflow-x-hidden">
      <nav className="border-b border-slate-800 bg-slate-900/50 backdrop-blur sticky top-0 z-50 px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex items-center gap-3 min-w-0">
          <div className="p-2 rounded-lg bg-cyan-950 border border-cyan-800 text-cyan-400 shrink-0">
            <Activity className="w-5 h-5 animate-pulse" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-base font-bold tracking-wider text-slate-100">AEGISTEL</h1>
              <span className="text-[10px] bg-cyan-950 text-cyan-400 border border-cyan-800 px-1.5 py-0.5 rounded font-bold">
                NOKIA NaC CAMARA SWARM
              </span>
            </div>
            <p className="hidden sm:block text-[11px] text-slate-400 truncate">Multi-Agent Telecom Fraud & Network Intelligence Platform</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs ml-auto">
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-md border ${requestStatus === "ready" ? "bg-emerald-950 border-emerald-800 text-emerald-300" : requestStatus === "requesting" ? "bg-cyan-950 border-cyan-800 text-cyan-300" : "bg-slate-900 border-slate-800 text-slate-400"}`}>
            <RadioTower className="w-3.5 h-3.5" />
            <span className="font-bold">API {requestStatus.toUpperCase()}</span>
          </div>
          {isSpeaking && (
            <div className="flex items-center gap-2 bg-rose-950 border border-rose-800 text-rose-400 px-3 py-1.5 rounded-md animate-pulse">
              <Volume2 className="w-4 h-4" />
              <span className="font-bold">AUDIO BRIEFING</span>
            </div>
          )}
          <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-md">
            <RadioTower className="w-3.5 h-3.5 text-cyan-400" />
            <span className="hidden sm:inline text-slate-400">APIs Integrated:</span>
            <span className="text-cyan-300 font-bold">{activeSignalCount ?? "—"} <span className="hidden sm:inline">CAMARA Signals</span></span>
          </div>
          <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-md">
            <ShieldCheck className="w-3.5 h-3.5 text-amber-400" />
            <span className="hidden sm:inline text-slate-400">Protected this session:</span>
            <span className="text-amber-300 font-bold">${sessionStats.protectedAmount.toLocaleString()}</span>
          </div>
        </div>
      </nav>

      <div className="p-4 sm:p-6 max-w-[1600px] mx-auto grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-6">
        <div className="lg:col-span-4 space-y-6">
          <ThreatStream onSelectThreat={handleSelectThreat} />

          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4 shadow-lg">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Sliders className="w-4 h-4 text-cyan-400" /> CAMARA Audit Controls
              </h2>
              <span className="text-[10px] text-slate-200">Nokia NaC Sandbox</span>
            </div>

            {/* <button
              type="button"
              onClick={async () => {
                try {
                  const res = await fetch(`${apiBase}/api/memory/clear-all`, { method: "POST" });
                  if (!res.ok) throw new Error(`Memory clear failed: ${res.status}`);
                  setLiveEvents((prev) => [
                    ...prev,
                    {
                      id: `${Date.now()}-memory-clear`,
                      type: "memory",
                      message: "Cleared local and Mem0 memory state",
                      stage: "memory",
                      detail: "The demo memory store was reset for the next audit run.",
                    },
                  ]);
                } catch (error) {
                  setLiveEvents((prev) => [
                    ...prev,
                    {
                      id: `${Date.now()}-memory-clear-error`,
                      type: "error",
                      message: "Unable to clear memory",
                      stage: "memory",
                      detail: error instanceof Error ? error.message : "Unknown error",
                    },
                  ]);
                }
              }}
              className="w-full bg-amber-600 hover:bg-amber-500 text-slate-950 font-bold py-2 rounded-lg text-xs flex items-center justify-center gap-2 transition cursor-pointer"
            >
              <ShieldCheck className="w-4 h-4" />
              CLEAR TEST MEMORY
            </button> */}

            <div className="space-y-3">
              <div>
                <label className="text-[11px] text-slate-400 mb-1 flex items-center gap-1.5">
                  <Phone className="w-3.5 h-3.5 text-cyan-400" /> Target Phone (MSISDN)
                </label>
                <input
                  type="text"
                  value={msisdn}
                  onChange={(e) => setMsisdn(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-cyan-300 font-mono focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div>
                <label className="text-[11px] text-slate-400 mb-1 flex items-center gap-1.5">
                  <DollarSign className="w-3.5 h-3.5 text-amber-400" /> Wire Transfer Amount ($)
                </label>
                <div className="relative">
                  <input
                    type="number"
                    value={amount}
                    min={0}
                    onChange={(e) => setAmount(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 pr-9 text-xs text-amber-300 font-mono focus:outline-none focus:border-amber-500"
                  />
                  <div className="absolute inset-y-0 right-0 flex flex-col border-l border-slate-800">
                    <button
                      type="button"
                      onClick={() => setAmount(String((parseFloat(amount || "0") || 0) + 1000))}
                      aria-label="Increase amount"
                      className="flex-1 w-7 flex items-center justify-center rounded-tr-lg bg-slate-900 text-amber-400/80 hover:text-amber-300 hover:bg-slate-800 border-b border-slate-800 transition cursor-pointer"
                    >
                      <ChevronUp className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setAmount(String(Math.max(0, (parseFloat(amount || "0") || 0) - 1000)))}
                      aria-label="Decrease amount"
                      className="flex-1 w-7 flex items-center justify-center rounded-br-lg bg-slate-900 text-amber-400/80 hover:text-amber-300 hover:bg-slate-800 transition cursor-pointer"
                    >
                      <ChevronDown className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-slate-400 mb-1 block">Device Lat</label>
                  <input
                    type="text"
                    value={lat}
                    onChange={(e) => setLat(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-xs text-slate-200"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-slate-400 mb-1 block">Device Lng</label>
                  <input
                    type="text"
                    value={lng}
                    onChange={(e) => setLng(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-xs text-slate-200"
                  />
                </div>
              </div>

              <div>
                <label className="text-[10px] text-slate-400 mb-1 block">Geofence Radius (Meters)</label>
                <input
                  type="text"
                  value={geofenceRadius}
                  onChange={(e) => setGeofenceRadius(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-xs text-slate-200"
                />
              </div>

              <div className="pt-2 border-t border-slate-800 space-y-2">
                <label className="flex items-center justify-between text-xs text-slate-400 cursor-pointer">
                  <span>Enforce Roaming Policy</span>
                  <input
                    type="checkbox"
                    checked={checkRoaming}
                    onChange={(e) => setCheckRoaming(e.target.checked)}
                    className="accent-cyan-500"
                  />
                </label>
                <label className="flex items-center justify-between text-xs text-slate-400 cursor-pointer">
                  <span>Auto-Provision QoD Slice on Risk</span>
                  <input
                    type="checkbox"
                    checked={requestQoD}
                    onChange={(e) => setRequestQoD(e.target.checked)}
                    className="accent-cyan-500"
                  />
                </label>
              </div>
            </div>

            <button
              onClick={() => void runAudit()}
              disabled={loading}
              className="w-full bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 text-slate-950 font-bold py-2.5 rounded-lg text-xs flex items-center justify-center gap-2 transition cursor-pointer"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>SWARM QUERYING NOKIA NaC...</span>
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4 fill-slate-950" />
                  <span>RUN MULTI-API AUDIT</span>
                </>
              )}
            </button>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3">
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3">
              <div>
                <div className="text-[10px] text-slate-400 uppercase">Session Verdict Distribution</div>
                <div className="text-xs text-slate-300 mb-3">Counts by audit outcome in this browser session.</div>
              </div>
              <div className="h-auto sm:h-40 w-full mt-2">
                {Object.values(sessionStatusCounts).reduce((a, b) => a + b, 0) > 0 ? (
                  <div className="grid grid-cols-1 sm:grid-cols-[1fr_0.95fr] gap-3 sm:h-full">
                    <div className="h-44 sm:h-full w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            dataKey="value"
                            data={[
                              { name: "APPROVED", value: sessionStatusCounts.APPROVED },
                              { name: "STEP_UP_REQUIRED", value: sessionStatusCounts.STEP_UP_REQUIRED },
                              { name: "MANUAL_REVIEW", value: sessionStatusCounts.MANUAL_REVIEW },
                              { name: "REJECTED", value: sessionStatusCounts.REJECTED },
                              { name: "BLOCKED", value: sessionStatusCounts.BLOCKED },
                            ]}
                            innerRadius={34}
                            outerRadius={56}
                            paddingAngle={2}
                          >
                            <Cell fill="#10b981" />
                            <Cell fill="#f59e0b" />
                            <Cell fill="#f97316" />
                            <Cell fill="#ef4444" />
                            <Cell fill="#7f1d1d" />
                          </Pie>
                          <Tooltip formatter={(value: unknown) => {
                            const v = Number(value ?? 0);
                            return `${v} audit${v === 1 ? '' : 's'}`;
                          }} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                    <div className="space-y-1 text-[11px] text-slate-300">
                      <div className="font-semibold text-slate-100">Session totals</div>
                      <div className="flex justify-between"> <span>Audits run</span> <span>{Object.values(sessionStatusCounts).reduce((a, b) => a + b, 0)}</span> </div>
                      <div className="flex justify-between"> <span>Approved</span> <span>{sessionStatusCounts.APPROVED}</span> </div>
                      <div className="flex justify-between"> <span>Step-up</span> <span>{sessionStatusCounts.STEP_UP_REQUIRED}</span> </div>
                      <div className="flex justify-between"> <span>Review</span> <span>{sessionStatusCounts.MANUAL_REVIEW}</span> </div>
                      <div className="flex justify-between"> <span>Rejected</span> <span>{sessionStatusCounts.REJECTED}</span> </div>
                      <div className="flex justify-between"> <span>Blocked</span> <span>{sessionStatusCounts.BLOCKED}</span> </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center text-[11px] text-slate-200">No session data yet; run an audit to populate the distribution.</div>
                )}
              </div>
              {Object.values(sessionStatusCounts).reduce((a, b) => a + b, 0) > 0 ? (
                <div className="mt-3 grid grid-cols-1 gap-2 text-[11px] text-slate-300">
                  <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-[#10b981]" /> APPROVED</div>
                  <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-[#f59e0b]" /> STEP_UP_REQUIRED</div>
                  <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-[#f97316]" /> MANUAL_REVIEW</div>
                  <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-[#ef4444]" /> REJECTED</div>
                  <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-[#7f1d1d]" /> BLOCKED</div>
                </div>
              ) : null}
            </div>

            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3">
              <div className="text-[10px] text-slate-400 uppercase">Per-Audit Signal Radar</div>
              <div className="text-xs text-slate-300 mb-2">Risk contribution from each CAMARA signal on this audit.</div>
              <div className="h-44 w-full">
                {auditResult ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <RadarChart
                      data={[
                        { subject: 'SIM Swap', A: auditResult.telemetry.sim_swap_detected ? 1 : 0, note: auditResult.telemetry.sim_swap_detected ? 'Flagged' : 'Clear' },
                        { subject: 'Location', A: auditResult.telemetry.geofence_status !== 'VERIFIED' ? 1 : 0, note: auditResult.telemetry.geofence_status },
                        { subject: 'Roaming', A: auditResult.telemetry.roaming_status === 'INTERNATIONAL_ROAMING' ? 1 : 0, note: auditResult.telemetry.roaming_status },
                        { subject: 'Reachability', A: (auditResult.telemetry.reachability_status || '').toUpperCase() === 'UNREACHABLE' ? 1 : 0, note: auditResult.telemetry.reachability_status },
                        { subject: 'Number Verify', A: (auditResult.telemetry.number_verification_status || '') !== 'VERIFIED' ? 1 : 0, note: auditResult.telemetry.number_verification_status || 'UNKNOWN' },
                        { subject: 'Congestion', A: (auditResult.telemetry.max_congestion_level || 'low').toLowerCase() === 'high' ? 1 : 0, note: auditResult.telemetry.max_congestion_level || 'LOW' },
                        { subject: 'QoD', A: auditResult.telemetry.qod_status ? 1 : 0, note: `${auditResult.telemetry.qod_status || 'NONE'}${auditResult.telemetry.qod_profile ? ' • ' + auditResult.telemetry.qod_profile : ''}` },
                      ]}
                    >
                      <PolarGrid />
                      <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                      <PolarRadiusAxis angle={30} domain={[0, 1]} tickCount={3} tick={{ fill: '#718096', fontSize: 10 }} />
                      <Radar name="Signal risk" dataKey="A" stroke="#06b6d4" fill="#06b6d4" fillOpacity={0.35} />
                      <Tooltip formatter={(value: unknown) => ((Number(value ?? 0)) === 1 ? 'Flagged' : 'Clear')} />
                    </RadarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full items-center justify-center text-[11px] text-slate-200">Run an audit to see which signals drove this verdict.</div>
                )}
              </div>
              {auditResult ? (
                <div className="mt-3 space-y-2 text-[11px] text-slate-400">
                  <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-cyan-400" />A value of 1 means that signal was flagged; 0 means it was clear.</div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-slate-300">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-2">
                      <div className="text-[10px] text-slate-200 uppercase">SIM Swap</div>
                      <div className="font-semibold text-slate-100">{auditResult.telemetry.sim_swap_detected ? 'Flagged' : 'Clean'}</div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-2">
                      <div className="text-[10px] text-slate-200 uppercase">QoD</div>
                      <div className="font-semibold text-slate-100">{auditResult.telemetry.qod_status ? (
                        <span className={auditResult.status === "APPROVED" ? "text-cyan-300" : "text-rose-400"}>
                          {`${auditResult.telemetry.qod_status}${auditResult.telemetry.qod_profile ? ' • ' + auditResult.telemetry.qod_profile : ''}`}
                        </span>
                      ) : (
                        <span className="text-slate-200">NONE</span>
                      )}</div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-2">
                      <div className="text-[10px] text-slate-200 uppercase">Number Verification</div>
                      <div className="font-semibold text-slate-100">
                        <span className={(auditResult.telemetry.number_verification_status || 'UNKNOWN') === 'VERIFIED' ? "text-emerald-400" : "text-rose-400"}>
                          {auditResult.telemetry.number_verification_status || 'UNKNOWN'}
                        </span>
                      </div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-2">
                      <div className="text-[10px] text-slate-200 uppercase">Cell Congestion</div>
                      <div className="font-semibold text-slate-100">
                        <span className={(auditResult.telemetry.max_congestion_level || 'low').toLowerCase() === 'high' ? "text-amber-400" : "text-emerald-400"}>
                          {auditResult.telemetry.max_congestion_level || 'LOW'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              ) : null}
            </div>

            <div className="bg-slate-900/80 border border-rose-950 rounded-lg p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[10px] text-rose-400 uppercase tracking-wider flex items-center gap-1.5">
                  <Zap className="w-3 h-3" /> Red Team — Adversarial Drill
                </div>
                <button
                  onClick={() => void runDrill()}
                  disabled={drillLoading}
                  className={`px-2.5 py-1 rounded text-[10px] font-bold uppercase border transition-colors ${
                    drillLoading
                      ? "border-rose-900 text-rose-500 cursor-not-allowed"
                      : "border-rose-700 bg-rose-950 text-rose-300 hover:bg-rose-900"
                  }`}
                >
                  {drillLoading ? "RED TEAM ENGAGED..." : "RUN ADVERSARIAL DRILL"}
                </button>
              </div>
              <p className="mt-2 text-[11px] text-slate-400 leading-relaxed">
                The same multi-agent engine that guards transactions now plays the attacker:
                a red-team playbook of fraud plays executes against the live crew, and the
                defense is graded on how many it actually caught.
              </p>

              {drillError ? (
                <div className="mt-3 rounded border border-rose-800 bg-rose-950/40 p-2.5 text-[10px] text-rose-300">
                  Drill request failed: {drillError}. Confirm the backend is running (uvicorn app.main:app --reload)
                  and NODE_ENV has NEXT_PUBLIC_API_BASE_URL pointing at it — this route is /api/v1/drill/run.
                </div>
              ) : null}

              {drillResult ? (
                <div className="mt-3 space-y-3">
                  <div>
                    <div className="flex items-baseline justify-between text-[11px] mb-1">
                      <span className="text-slate-400 uppercase">Defense Readiness</span>
                      <span className="font-bold text-rose-300">{drillResult.readiness_score}% <span className="text-slate-200">({drillResult.grade})</span></span>
                    </div>
                    <div className="h-2 rounded bg-slate-950 border border-slate-800 overflow-hidden">
                      <div
                        className={`h-full rounded transition-all duration-700 ${drillResult.readiness_score >= 80 ? "bg-emerald-500" : drillResult.readiness_score >= 60 ? "bg-amber-500" : "bg-rose-500"}`}
                        style={{ width: `${drillResult.readiness_score}%` }}
                      />
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5 text-[10px]">
                    {(Object.entries(drillResult.outcomes) as [string, number][]).map(([name, count]) => (
                      <span key={name} className={`px-2 py-0.5 rounded border font-bold ${
                        name === "BLOCKED" ? "border-rose-800 bg-rose-950 text-rose-300"
                        : name === "ESCALATED" ? "border-amber-800 bg-amber-950 text-amber-300"
                        : name === "CLEARED" ? "border-emerald-800 bg-emerald-950 text-emerald-300"
                        : name === "PARTIALLY_MISSED" ? "border-orange-800 bg-orange-950 text-orange-300"
                        : name === "MISSED" ? "border-red-800 bg-red-950 text-red-400"
                        : "border-slate-700 bg-slate-950 text-slate-400"
                      }`}>
                        {name} {count}
                      </span>
                    ))}
                  </div>

                  <div className="space-y-2">
                    {drillResult.plays.map((play) => (
                      <div key={play.id} className="border border-slate-800 bg-slate-950/70 rounded p-2.5">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-[11px] font-bold text-slate-200">{play.name}</span>
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                            play.threat_level === "CRITICAL" ? "border-rose-700 bg-rose-950 text-rose-300"
                            : play.threat_level === "HIGH" ? "border-orange-700 bg-orange-950 text-orange-300"
                            : play.threat_level === "MEDIUM" ? "border-amber-700 bg-amber-950 text-amber-300"
                            : "border-emerald-700 bg-emerald-950 text-emerald-300"
                          }`}>
                            THREAT {play.threat_level}
                          </span>
                        </div>
                        <div className="mt-1 text-[10px] text-slate-200 italic">{play.intent}</div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px]">
                          <span className="text-slate-400">Defense:</span>
                          <span className="font-bold text-cyan-300">{play.verdict_status} / {play.defense_risk}</span>
                          <span className={`ml-auto px-1.5 py-0.5 rounded font-bold border ${
                            play.outcome === "BLOCKED" ? "border-rose-800 bg-rose-950 text-rose-300"
                            : play.outcome === "ESCALATED" ? "border-amber-800 bg-amber-950 text-amber-300"
                            : play.outcome === "CLEARED" ? "border-emerald-800 bg-emerald-950 text-emerald-300"
                            : play.outcome === "PARTIALLY_MISSED" || play.outcome === "MISSED" ? "border-orange-800 bg-orange-950 text-orange-300"
                            : "border-slate-700 bg-slate-950 text-slate-400"
                          }`}>
                            {play.outcome.replace("_", " ")}
                          </span>
                        </div>
                        {play.detected_via.length > 0 ? (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {play.detected_via.map((signal) => (
                              <span key={signal} className="text-[9px] px-1.5 py-0.5 rounded bg-slate-950 border border-slate-800 text-slate-400">
                                {signal}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>

                  {drillResult.blind_spots.length > 0 ? (
                    <div className="border border-amber-900 bg-amber-950/20 rounded p-2.5">
                      <div className="text-[10px] font-bold uppercase text-amber-300 mb-1">
                        Blind Spot Discovered ({drillResult.blind_spots.length})
                      </div>
                      {drillResult.blind_spots.map((spot) => (
                        <div key={spot.play_id} className="text-[10px] text-slate-300">
                          <span className="font-bold text-amber-200">{spot.play_name}</span> — {spot.note}
                        </div>
                      ))}
                      {drillResult.recommendations.map((rec) => (
                        <div key={rec} className="text-[10px] text-slate-400 italic mt-1">→ {rec}</div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-[10px] text-emerald-400 text-center py-1">No blind spots in this playbook.</div>
                  )}

                  <div className="text-[9px] text-slate-600 border-t border-slate-900 pt-1.5 flex justify-between items-center gap-2">
                    <span className="truncate">{drillResult.playbook}</span>
                    <span className="shrink-0 ml-1">
                      <span className={`px-1.5 py-0.5 rounded border ${drillResult.generated_by_llm ? "text-violet-300 border-violet-800/60 bg-violet-950/40" : "text-slate-400 border-slate-800 bg-slate-900/60"}`}>
                        {drillResult.generated_by_llm ? "FRAUD GENIE" : "SAMPLED"} LINEUP
                      </span>
                    </span>
                  </div>
                </div>
              ) : drillLoading ? (
                <div className="py-4 flex items-center justify-center space-x-2 text-[11px] text-rose-300">
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Fraud Genie is writing attack plays... executing against the crew...</span>
                </div>
              ) : (
                <div className="mt-3 text-[11px] text-slate-200 italic border border-dashed border-slate-800 rounded p-3">
                  No drill run yet this session. Run one to grade the defense against the playbook.
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="lg:col-span-8 space-y-6">
          <AuditFlowDiagram
            phase={flowPhase}
            tools={flowTools}
            specialist={flowSpecialist}
            auditor={flowAuditor}
            llmModel={flowLlmModel}
            verdict={flowVerdict}
          />
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-6 shadow-lg space-y-5">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <span className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Cpu className="w-4 h-4 text-indigo-400" /> Carrier Telemetry & Risk Verdict
              </span>
              {auditResult && <span className="text-xs text-slate-400">MSISDN: {auditResult.msisdn}</span>}
            </div>

            {loading ? (
              <div className="py-12 flex flex-col items-center justify-center space-y-3">
                <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin" />
                <p className="text-xs text-slate-400 animate-pulse">Executing CAMARA network verification flow...</p>
              </div>
            ) : auditResult ? (
              <div className="space-y-5">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-200 uppercase flex items-center gap-1">
                      <Smartphone className="w-3 h-3 text-rose-400" /> SIM Swap History
                    </span>
                    <div className="text-xs font-bold">
                      {auditResult.telemetry.sim_swap_detected ? (
                        // format 2026-08-01T16:08:15.184759+00:00 to be human readable
                        <span className="text-rose-400">SIM SWAP DETECTED <br />{new Date(auditResult.telemetry.last_sim_swap_date!).toLocaleString() ?? 0}</span>
                      ) : (
                        <span className="text-emerald-400">CLEARED (No Swap)</span>
                      )}
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1 w-full">
                    <span className="text-[10px] text-slate-200 uppercase flex items-center gap-1">
                      <MapPin className="w-3 h-3 text-amber-400" /> Location & Geofence
                    </span>
                    <div className="text-xs font-bold">
                      <span className={
                        auditResult.telemetry.geofence_status === "VERIFIED"
                          ? "text-emerald-400"
                          : auditResult.telemetry.geofence_status === "PARTIAL"
                            ? "text-amber-400"
                            : auditResult.telemetry.geofence_status === "UNKNOWN"
                              ? "text-slate-400"
                              : "text-rose-400"
                      }>
                        {auditResult.telemetry.geofence_status === "VERIFIED"
                          ? "INSIDE GEOFENCE"
                          : auditResult.telemetry.geofence_status === "PARTIAL"
                            ? "PARTIAL MATCH"
                            : auditResult.telemetry.geofence_status === "UNKNOWN"
                              ? "UNKNOWN"
                              : "OUTSIDE GEOFENCE"}
                      </span>
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-200 uppercase flex items-center gap-1">
                      <Globe className="w-3 h-3 text-sky-400" /> Roaming Telemetry
                    </span>
                    <div className="text-xs font-bold">
                      <span className={auditResult.telemetry.roaming_status === "DOMESTIC" ? "text-emerald-400" : "text-amber-400"}>
                        {auditResult.telemetry.roaming_status}
                        {auditResult.telemetry.roaming_country ? ` (${auditResult.telemetry.roaming_country})` : null}
                      </span>
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-200 uppercase flex items-center gap-1">
                      <Wifi className="w-3 h-3 text-purple-400" /> Reachability Status
                    </span>
                    <div className="text-xs font-bold text-slate-200">{auditResult.telemetry.reachability_status}</div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-200 uppercase flex items-center gap-1">
                      <ShieldCheck className="w-3 h-3 text-emerald-400" /> Number Verification
                    </span>
                    <div className="text-xs font-bold">
                      {auditResult.telemetry.number_verification_status === "VERIFIED"
                        ? <span className="text-emerald-400">NUMBER VERIFIED</span>
                        : auditResult.telemetry.number_verification_status === "FAILED"
                          ? <span className="text-rose-400">VERIFICATION FAILED</span>
                          : <span className="text-slate-400">{auditResult.telemetry.number_verification_status ?? "UNKNOWN"}</span>}
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-200 uppercase flex items-center gap-1">
                      <Activity className="w-3 h-3 text-orange-400" /> Cell Congestion
                    </span>
                    <div className="text-xs font-bold">
                      <span className={(auditResult.telemetry.max_congestion_level ?? "").toUpperCase() === "HIGH" ? "text-rose-400" : (auditResult.telemetry.max_congestion_level ?? "").toUpperCase() === "MEDIUM" ? "text-amber-400" : "text-emerald-400"}>
                        {(auditResult.telemetry.max_congestion_level ?? "LOW").toUpperCase()} LOAD
                      </span>
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1 w-full">
                    <span className="text-[10px] text-slate-200 uppercase flex items-center gap-1">
                      <Radio className="w-3 h-3 text-indigo-400" /> QoD Slice Provisioning
                    </span>
                    <div className="text-xs font-bold">
                        {auditResult.telemetry.qod_session_active ? (
                          (() => {
                            const status = (auditResult.telemetry.qod_status || "").toUpperCase();
                            const requestedSet = new Set(["REQUESTED", "REQUESTED_CREATED"]);
                            const activeSet = new Set(["ACTIVE", "AVAILABLE"]);
                            if (requestedSet.has(status)) {
                              return <span className="text-cyan-300">QoD REQUESTED</span>;
                            }
                            if (activeSet.has(status)) {
                              return <span className="text-emerald-400">ACTIVE QoD</span>;
                            }
                            return <span className="text-slate-400">QoD REQUESTED</span>;
                          })()
                        ) : (
                          <span className="text-slate-200">INACTIVE</span>
                        )}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="bg-slate-950/80 border border-slate-800 p-4 rounded-lg">
                    <div className="flex items-center gap-2 text-[10px] uppercase text-slate-200 mb-2">
                      <AlertTriangle className="w-3 h-3 text-amber-400" /> Evidence Strength
                    </div>
                    <div className="text-sm font-bold text-amber-300">{auditResult.telemetry.evidence_strength ?? "MEDIUM"}</div>
                  </div>
                  <div className="bg-slate-950/80 border border-slate-800 p-4 rounded-lg">
                    <div className="flex items-center gap-2 text-[10px] uppercase text-slate-200 mb-2">
                      <CheckCircle2 className="w-3 h-3 text-cyan-400" /> Confidence
                    </div>
                    <div className="text-sm font-bold text-cyan-300">{((auditResult.telemetry.confidence ?? 0) * 100).toFixed(0)}%</div>
                  </div>
                  <div className="bg-slate-950/80 border border-slate-800 p-4 rounded-lg">
                    <div className="flex items-center gap-2 text-[10px] uppercase text-slate-200 mb-2">
                      <Globe className="w-3 h-3 text-fuchsia-400" /> Cross-border Risk
                    </div>
                    <div className={`text-sm font-bold ${auditResult.telemetry.cross_border_risk ? "text-rose-400" : "text-emerald-400"}`}>
                      {auditResult.telemetry.cross_border_risk ? "DETECTED" : "CLEAR"}
                    </div>
                  </div>
                </div>

                <div
                  className={`rounded-xl border p-4 space-y-2 text-xs shadow-[0_0_0_1px_rgba(255,255,255,0.03),0_12px_40px_rgba(2,8,23,0.35)] transition-all duration-500 ${verdictVisible ? "opacity-100 scale-100" : "opacity-0 scale-95"}`}
                  style={{
                    background: auditResult.risk_score === "CRITICAL" || auditResult.risk_score === "HIGH" || auditResult.status === "BLOCKED" ? "linear-gradient(135deg, rgba(127,29,29,0.28), rgba(15,23,42,0.95))" : auditResult.status === "STEP_UP_REQUIRED" ? "linear-gradient(135deg, rgba(120,53,15,0.24), rgba(15,23,42,0.95))" : "linear-gradient(135deg, rgba(6,78,59,0.16), rgba(15,23,42,0.95))",
                    borderColor: auditResult.risk_score === "CRITICAL" || auditResult.risk_score === "HIGH" || auditResult.status === "BLOCKED" ? "rgba(248,113,113,0.3)" : auditResult.status === "STEP_UP_REQUIRED" ? "rgba(245,158,11,0.28)" : "rgba(74,222,128,0.2)",
                  }}
                >
                  <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 border-b border-slate-900/70 pb-2">
                    <span className="text-cyan-400 font-bold uppercase">Auditor Verdict & Action</span>
                    <span className={`w-fit px-2 py-0.5 rounded text-[10px] font-bold ${auditResult.risk_score === "CRITICAL" || auditResult.risk_score === "HIGH" || auditResult.status === "BLOCKED" ? "bg-rose-950 text-rose-400 border border-rose-800" : auditResult.status === "STEP_UP_REQUIRED" ? "bg-amber-950 text-amber-300 border border-amber-800" : "bg-emerald-950 text-emerald-400 border border-emerald-800"}`}>
                      STATUS: {auditResult.status} | RISK: {auditResult.risk_score}
                    </span>
                  </div>
                  <p className="text-slate-300 leading-relaxed">{auditResult.reasoning}</p>
                  {auditResult.used_fallback ? (
                    <div className="pt-2 border-t border-slate-900/70 text-[11px] text-amber-300 font-semibold">
                      Deterministic fallback path was used for this evaluation.
                    </div>
                  ) : null}
                  <div className="pt-2 border-t border-slate-900/70 text-[11px] text-amber-300 font-semibold">
                    Recommended Action: {auditResult.recommended_action}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 shadow-[0_8px_24px_rgba(2,8,23,0.35)] space-y-3">
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                    <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-400 flex items-center gap-2">
                      <Terminal className="w-3.5 h-3.5 text-cyan-400 shrink-0" /> Evidence Explorer
                    </div>
                    <span className="text-[10px] text-slate-200">
                      {(() => {
                        const tools = auditResult.telemetry.tool_results ?? [];
                        const sdk = tools.filter((t) => t.source === "Nokia NaC SDK").length;
                        const rest = tools.filter((t) => t.source === "CAMARA REST").length;
                        return `${tools.length} tool calls • ${sdk} SDK • ${rest} REST • ${tools.length - sdk - rest} sandbox`;
                      })()}
                    </span>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {(auditResult.telemetry.tool_results ?? []).map((tool) => (
                      <details key={tool.name} className="rounded-lg border border-slate-800 bg-slate-950/70 group">
                        <summary className="cursor-pointer px-3 py-2 flex items-center justify-between gap-2 text-[11px]">
                          <span className="flex items-center gap-2 font-bold text-slate-200">
                            <span className={`w-1.5 h-1.5 rounded-full ${tool.success ? "bg-emerald-400" : "bg-rose-400"}`} />
                            {tool.name}
                          </span>
                          <span className="flex items-center gap-2">
                            <span className={`text-[9px] px-1.5 py-0.5 rounded border font-bold ${
                              tool.source === "Nokia NaC SDK" ? "text-cyan-300 border-cyan-800 bg-cyan-950"
                              : tool.source === "CAMARA REST" ? "text-violet-300 border-violet-800 bg-violet-950"
                              : "text-slate-400 border-slate-700 bg-slate-950"
                            }`}>
                              {tool.source === "Nokia NaC SDK" ? "LIVE SDK" : tool.source === "CAMARA REST" ? "REST" : "SANDBOX"}
                            </span>
                            {tool.duration_ms != null ? <span className="text-[9px] text-slate-200">{tool.duration_ms}ms</span> : null}
                          </span>
                        </summary>
                        <pre className="px-3 pb-3 text-[9px] text-slate-400 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap break-all border-t border-slate-900/70 pt-2">
                          {JSON.stringify(tool.payload, null, 2)}
                        </pre>
                      </details>
                    ))}
                  </div>

                  {auditResult.raw_output ? (
                    <details className="rounded-lg border border-violet-900 bg-violet-950/20">
                      <summary className="cursor-pointer px-3 py-2 text-[11px] font-bold text-violet-300 flex items-center justify-between">
                        <span className="flex items-center gap-2">LLM Raw Output (specialist + auditor reasoning)</span>
                        <span className="text-[9px] text-slate-200">{auditResult.used_fallback ? "deterministic" : "model-authored"}</span>
                      </summary>
                      <pre className="px-3 pb-3 text-[10px] text-slate-300 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-all border-t border-violet-900/70 pt-2">
                        {auditResult.raw_output}
                      </pre>
                    </details>
                  ) : null}
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 shadow-[0_8px_24px_rgba(2,8,23,0.35)]">
                  <button
                    type="button"
                    onClick={() => setHistoryOpen((prev) => !prev)}
                    className="flex w-full items-center justify-between rounded-lg border border-slate-800 bg-slate-900/70 px-3 py-2 text-left"
                  >
                    <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-cyan-400">Audit History for {msisdn}</span>
                    <span className="text-[10px] text-slate-400">{historyOpen ? "Collapse" : "Expand"}</span>
                  </button>
                  {historyOpen ? (
                    <div className="mt-4 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                        <div className="mb-3 flex items-center justify-between">
                          <div>
                            <div className="text-[10px] uppercase tracking-[0.2em] text-slate-200">Risk Trend</div>
                            <div className="text-xs font-semibold text-slate-300">Historical risk movement for this subscriber</div>
                          </div>
                          <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1 text-[10px] text-slate-400">{history?.count ?? 0} records</div>
                        </div>
                        {history && history.incidents.length > 0 ? (
                          <div className="h-40">
                            <ResponsiveContainer width="100%" height="100%">
                              <BarChart data={history.incidents.map((incident, index) => ({
                                label: index + 1,
                                risk: incident.risk_score === "CRITICAL" ? 4 : incident.risk_score === "HIGH" ? 3 : incident.risk_score === "MEDIUM" ? 2 : 1,
                                status: incident.status ?? "UNKNOWN",
                              }))}>
                                <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                                <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 10 }} />
                                <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} domain={[0, 4]} />
                                <Tooltip />
                                <Bar dataKey="risk" fill="#38bdf8" radius={[3, 3, 0, 0]} />
                              </BarChart>
                            </ResponsiveContainer>
                          </div>
                        ) : (
                          <div className="flex h-32 items-center justify-center rounded border border-dashed border-slate-800 bg-slate-900/60 text-center text-[11px] text-slate-200">
                            No prior audits for this number
                          </div>
                        )}
                      </div>
                      <div className="space-y-2">
                        {(history?.incidents ?? []).length > 0 ? history!.incidents.map((incident, index) => {
                          const severityClass = incident.risk_score === "CRITICAL" || incident.risk_score === "HIGH" ? "border-rose-800 bg-rose-950/30 text-rose-300" : incident.risk_score === "MEDIUM" ? "border-amber-800 bg-amber-950/20 text-amber-300" : "border-emerald-800 bg-emerald-950/20 text-emerald-300";
                          return (
                            <div key={`${incident.timestamp ?? index}`} className="rounded-lg border border-slate-800 bg-slate-950/70 p-3">
                              <div className="flex items-center justify-between gap-2">
                                <div className="text-[10px] text-slate-200">{incident.timestamp ? new Date(incident.timestamp).toLocaleString() : "Unknown time"}</div>
                                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${severityClass}`}>{incident.status ?? "UNKNOWN"}</span>
                              </div>
                              <div className="mt-2 flex items-center justify-between text-[11px] text-slate-300">
                                <span>{incident.roaming_status ?? "DOMESTIC"}</span>
                                <span className="font-semibold text-cyan-300">${Number(incident.amount ?? 0).toLocaleString()}</span>
                              </div>
                            </div>
                          );
                        }) : (
                          <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-3 text-[11px] text-slate-200">
                            Memory context is not yet available for this subscriber. The next audit run will populate it.
                          </div>
                        )}
                      </div>
                    </div>
                  ) : null}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => void playVoiceAlert(`Audit complete for ${auditResult.msisdn}. Verdict: ${auditResult.status}. Risk level: ${auditResult.risk_score}. ${auditResult.reasoning}`)}
                    className="inline-flex items-center gap-2 rounded-lg border border-cyan-800 bg-cyan-950 px-3 py-2 text-[11px] font-bold text-cyan-300"
                  >
                    <Volume2 className="w-3.5 h-3.5" />
                    {isSpeaking ? "PLAYING AUDIO BRIEFING" : "REPLAY AUDIO BRIEFING"}
                  </button>
                  <button
                    onClick={() => {
                      const audio = new Audio(`${apiBase}/api/audio/tts`);
                      void audio.play().catch((error) => console.error("[TTS] direct test button failed", error));
                    }}
                    className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-[11px] font-bold text-slate-300"
                  >
                    <Volume2 className="w-3.5 h-3.5" />
                    🔊 TEST SOUND
                  </button>
                </div>
              </div>
            ) : (
                <div className="py-10 text-center text-slate-200 text-xs italic">
                Select an incident from Live Threat Feed or click &quot;Run Multi-API Audit&quot; to evaluate network signals.
              </div>
            )}
          </div>

          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-6 shadow-lg space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Terminal className="w-4 h-4 text-cyan-400" /> Live Audit Trace
              </h2>
              <span className={`text-[10px] px-2 py-1 rounded border ${requestStatus === "ready" ? "border-emerald-800 text-emerald-300 bg-emerald-950" : requestStatus === "requesting" ? "border-cyan-800 text-cyan-300 bg-cyan-950" : "border-slate-700 text-slate-400 bg-slate-950"}`}>
                {requestStatus === "ready" ? "RESPONDED" : requestStatus === "requesting" ? "REQUESTING" : requestStatus === "error" ? "ERROR" : "STANDBY"}
              </span>
            </div>

            {liveEvents.length > 0 ? (
              <div className="relative pl-4 space-y-4 before:absolute before:left-2 before:top-3 before:bottom-3 before:w-0.5 before:bg-slate-800">
                {liveEvents.map((event, index) => (
                  <div key={`${event.id}-${index}`} className="relative pl-6 space-y-1.5">
                    <div className="absolute -left-4.25 top-1 w-3 h-3 rounded-full bg-slate-950 border-2 border-cyan-400" />
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-cyan-400">{event.type.toUpperCase()}</span>
                      <span className="text-[10px] bg-slate-950 text-slate-400 px-2 py-0.5 rounded border border-slate-800">{event.stage ?? "event"}</span>
                    </div>
                    <div className="bg-slate-950 border border-slate-800 rounded p-2.5 text-[11px] space-y-1">
                      <p className="text-slate-300">{event.message}</p>
                      {event.detail && <p className="text-[10px] text-slate-200 font-mono pt-1 border-t border-slate-900">{event.detail}</p>}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-8 text-center text-slate-600 text-xs italic border border-dashed border-slate-800 rounded-lg">
                No active execution trace.
              </div>
            )}

            {auditResult && auditResult.agent_trace.length > 0 && (
              <div className="border-t border-slate-800 pt-4">
                <div className="text-[10px] uppercase text-slate-200 mb-2">Structured Evidence Trail</div>
                <div className="space-y-2">
                  {auditResult.agent_trace.map((step, idx) => {
                    const isAuditor = step.agent.toLowerCase().includes("risk auditor");
                    const isSecurity = step.agent.toLowerCase().includes("security");
                    const accentClass = isAuditor ? "border-violet-800 bg-violet-950/40" : isSecurity ? "border-cyan-800 bg-cyan-950/20" : "border-slate-800 bg-slate-950";
                    return (
                      <div
                        key={`${step.agent}-${idx}`}
                        className={`border rounded p-2.5 text-[11px] transition-transform duration-300 hover:-translate-y-0.5 ${accentClass}`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className={isAuditor ? "text-violet-300 font-bold" : "text-cyan-400 font-bold"}>{step.agent}</span>
                          <div className="flex flex-wrap items-center gap-2">
                            {step.provider ? (
                              <span className="text-[10px] uppercase tracking-[0.2em] text-slate-400 bg-slate-950 border border-slate-800 rounded px-2 py-1">
                                {step.provider}
                              </span>
                            ) : null}
                            {step.model ? (
                              <span className="text-[10px] uppercase tracking-[0.2em] text-slate-400 bg-slate-950 border border-slate-800 rounded px-2 py-1">
                                {step.model}
                              </span>
                            ) : null}
                            <span className="text-[10px] text-slate-200">{step.status}</span>
                          </div>
                        </div>
                        <div className="mt-2 rounded border border-slate-800 bg-slate-950/70 p-3">
                          <div className="text-[10px] uppercase tracking-[0.2em] text-slate-400">🧠 Thought</div>
                          <p className="mt-2 text-sm font-medium leading-relaxed text-slate-100">{step.thought}</p>
                        </div>
                        <p className="text-[10px] text-slate-400 mt-2 font-mono">{step.detail}</p>
                        <p className="text-[10px] text-slate-200 mt-1 font-mono">{step.action}</p>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}