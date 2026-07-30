"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Ban,
  Check,
  CheckCircle2,
  Cpu,
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
import ThreatStream from "./components/ThreatStream";

interface AgentStep {
  agent: string;
  action: string;
  thought: string;
  status: string;
  detail: string;
}

interface NokiaTelemetry {
  number_verification_match: boolean;
  sim_swap_detected: boolean;
  sim_swap_age_hours?: number;
  location_verification_match: boolean;
  location_accuracy_meters: number;
  geofence_status: string;
  roaming_status: string;
  roaming_country?: string;
  reachability_status: string;
  qod_session_active: boolean;
  qod_profile?: string;
  evidence_strength?: string;
  confidence?: number;
  cross_border_risk?: boolean;
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
}

interface LiveEvent {
  id: string;
  type: string;
  message: string;
  stage?: string;
  detail?: string;
}

export default function AegisTelDashboard() {
  const [msisdn, setMsisdn] = useState("+99999123456");
  const [amount, setAmount] = useState("120000");
  const [lat, setLat] = useState("24.7136");
  const [lng, setLng] = useState("46.6753");
  const [geofenceRadius, setGeofenceRadius] = useState("2000");
  const [checkRoaming, setCheckRoaming] = useState(true);
  const [requestQoD, setRequestQoD] = useState(true);

  const [loading, setLoading] = useState(false);
  const [auditResult, setAuditResult] = useState<AuditResponse | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [ttsStatus, setTtsStatus] = useState<"idle" | "speaking" | "fallback" | "unavailable">("idle");
  const [requestStatus, setRequestStatus] = useState<"idle" | "requesting" | "ready" | "error">("idle");
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([]);

  const activeAudioRef = useRef<HTMLAudioElement | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

  useEffect(() => {
    return () => {
      if (activeAudioRef.current) {
        activeAudioRef.current.pause();
        activeAudioRef.current.currentTime = 0;
      }
    };
  }, []);

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

      if (!res.ok) throw new Error("TTS API error");

      const blob = await res.blob();
      const audioUrl = URL.createObjectURL(blob);
      const audio = new Audio(audioUrl);
      activeAudioRef.current = audio;

      audio.onended = () => {
        setIsSpeaking(false);
        setTtsStatus("idle");
        URL.revokeObjectURL(audioUrl);
      };
      await audio.play();
    } catch {
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

  const runAudit = async (targetPhone = msisdn, targetAmount = amount) => {
    setLoading(true);
    setAuditResult(null);
    setLiveEvents([]);
    setRequestStatus("requesting");

    const payload = {
      msisdn: targetPhone,
      transaction_type: "WIRE_TRANSFER",
      amount: parseFloat(targetAmount),
      current_location: {
        latitude: parseFloat(lat),
        longitude: parseFloat(lng),
      },
      request_qod_slice: requestQoD,
      metadata: {
        geofence_radius_meters: parseFloat(geofenceRadius),
        enforce_roaming_policy: checkRoaming,
      },
    };

    setLiveEvents([
      {
        id: `${Date.now()}-request`,
        type: "request",
        message: "Sending audit request to AegisTel backend",
        stage: "request",
      },
    ]);

    try {
      const res = await fetch(`${apiBase}/api/v1/audit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error("Audit request failed");

      const data: AuditResponse = await res.json();
      setAuditResult(data);
      setRequestStatus("ready");
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
    } catch {
      setRequestStatus("error");
      setLiveEvents((prev) => [
        ...prev,
        {
          id: `${Date.now()}-error`,
          type: "error",
          message: "Unable to reach the backend audit endpoint",
          stage: "error",
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

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-mono selection:bg-cyan-500 selection:text-slate-950">
      <nav className="border-b border-slate-800 bg-slate-900/50 backdrop-blur sticky top-0 z-50 px-6 py-3.5 flex justify-between items-center">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-cyan-950 border border-cyan-800 text-cyan-400">
            <Activity className="w-5 h-5 animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold tracking-wider text-slate-100">AEGISTEL</h1>
              <span className="text-[10px] bg-cyan-950 text-cyan-400 border border-cyan-800 px-1.5 py-0.5 rounded font-bold">
                NOKIA NaC CAMARA SWARM
              </span>
            </div>
            <p className="text-[11px] text-slate-400">Multi-Agent Telecom Fraud & Network Intelligence Platform</p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs">
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
            <span className="text-slate-400">APIs Integrated:</span>
            <span className="text-cyan-300 font-bold">6 CAMARA Signals</span>
          </div>
        </div>
      </nav>

      <div className="p-6 max-w-[1600px] mx-auto grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-4 space-y-6">
          <ThreatStream onSelectThreat={handleSelectThreat} />

          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4 shadow-lg">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Sliders className="w-4 h-4 text-cyan-400" /> CAMARA Audit Controls
              </h2>
              <span className="text-[10px] text-slate-500">Nokia NaC Sandbox</span>
            </div>

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
                <input
                  type="number"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-amber-300 font-mono focus:outline-none focus:border-amber-500"
                />
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
        </div>

        <div className="lg:col-span-8 space-y-6">
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
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase flex items-center gap-1">
                      <ShieldCheck className="w-3 h-3 text-cyan-400" /> Number Verification
                    </span>
                    <div className="text-xs font-bold">
                      {auditResult.telemetry.number_verification_match ? (
                        <span className="text-emerald-400 flex items-center gap-1"><Check className="w-3.5 h-3.5" /> VERIFIED MATCH</span>
                      ) : (
                        <span className="text-rose-400 flex items-center gap-1"><Ban className="w-3.5 h-3.5" /> SPOOF / UNVERIFIED</span>
                      )}
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase flex items-center gap-1">
                      <Smartphone className="w-3 h-3 text-rose-400" /> SIM Swap History
                    </span>
                    <div className="text-xs font-bold">
                      {auditResult.telemetry.sim_swap_detected ? (
                        <span className="text-rose-400">SIM SWAP DETECTED ({auditResult.telemetry.sim_swap_age_hours ?? 0}h ago)</span>
                      ) : (
                        <span className="text-emerald-400">CLEARED (No Swap)</span>
                      )}
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase flex items-center gap-1">
                      <MapPin className="w-3 h-3 text-amber-400" /> Location & Geofence
                    </span>
                    <div className="text-xs font-bold">
                      <span className={auditResult.telemetry.location_verification_match ? "text-emerald-400" : "text-rose-400"}>
                        {auditResult.telemetry.location_verification_match ? "INSIDE GEOFENCE" : "OUTSIDE GEOFENCE"}
                      </span>
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase flex items-center gap-1">
                      <Globe className="w-3 h-3 text-sky-400" /> Roaming Telemetry
                    </span>
                    <div className="text-xs font-bold">
                      <span className={auditResult.telemetry.roaming_status === "DOMESTIC" ? "text-emerald-400" : "text-amber-400"}>
                        {auditResult.telemetry.roaming_status} ({auditResult.telemetry.roaming_country})
                      </span>
                    </div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase flex items-center gap-1">
                      <Wifi className="w-3 h-3 text-purple-400" /> Reachability Status
                    </span>
                    <div className="text-xs font-bold text-slate-200">{auditResult.telemetry.reachability_status}</div>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase flex items-center gap-1">
                      <Radio className="w-3 h-3 text-indigo-400" /> QoD Network Slice
                    </span>
                    <div className="text-xs font-bold">
                      {auditResult.telemetry.qod_session_active ? (
                        <span className="text-cyan-400 animate-pulse">ACTIVE PROPRIETARY SLICE</span>
                      ) : (
                        <span className="text-slate-500">INACTIVE</span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="bg-slate-950/80 border border-slate-800 p-4 rounded-lg">
                    <div className="flex items-center gap-2 text-[10px] uppercase text-slate-500 mb-2">
                      <AlertTriangle className="w-3 h-3 text-amber-400" /> Evidence Strength
                    </div>
                    <div className="text-sm font-bold text-amber-300">{auditResult.telemetry.evidence_strength ?? "MEDIUM"}</div>
                  </div>
                  <div className="bg-slate-950/80 border border-slate-800 p-4 rounded-lg">
                    <div className="flex items-center gap-2 text-[10px] uppercase text-slate-500 mb-2">
                      <CheckCircle2 className="w-3 h-3 text-cyan-400" /> Confidence
                    </div>
                    <div className="text-sm font-bold text-cyan-300">{((auditResult.telemetry.confidence ?? 0) * 100).toFixed(0)}%</div>
                  </div>
                  <div className="bg-slate-950/80 border border-slate-800 p-4 rounded-lg">
                    <div className="flex items-center gap-2 text-[10px] uppercase text-slate-500 mb-2">
                      <Globe className="w-3 h-3 text-fuchsia-400" /> Cross-border Risk
                    </div>
                    <div className={`text-sm font-bold ${auditResult.telemetry.cross_border_risk ? "text-rose-400" : "text-emerald-400"}`}>
                      {auditResult.telemetry.cross_border_risk ? "DETECTED" : "CLEAR"}
                    </div>
                  </div>
                </div>

                <div className="bg-slate-950/80 border border-slate-800 p-4 rounded-lg space-y-2 text-xs">
                  <div className="flex justify-between items-center border-b border-slate-900 pb-2">
                    <span className="text-cyan-400 font-bold uppercase">Auditor Verdict & Action</span>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${auditResult.risk_score === "CRITICAL" || auditResult.risk_score === "HIGH" ? "bg-rose-950 text-rose-400 border border-rose-800" : "bg-emerald-950 text-emerald-400 border border-emerald-800"}`}>
                      STATUS: {auditResult.status} | RISK: {auditResult.risk_score}
                    </span>
                  </div>
                  <p className="text-slate-300 leading-relaxed">{auditResult.reasoning}</p>
                  <div className="pt-2 border-t border-slate-900 text-[11px] text-amber-300 font-semibold">
                    Recommended Action: {auditResult.recommended_action}
                  </div>
                </div>

                <button
                  onClick={() => void playVoiceAlert(`Audit complete for ${auditResult.msisdn}. Verdict: ${auditResult.status}. Risk level: ${auditResult.risk_score}. ${auditResult.reasoning}`)}
                  className="inline-flex items-center gap-2 rounded-lg border border-cyan-800 bg-cyan-950 px-3 py-2 text-[11px] font-bold text-cyan-300"
                >
                  <Volume2 className="w-3.5 h-3.5" />
                  {isSpeaking ? "PLAYING AUDIO BRIEFING" : "REPLAY AUDIO BRIEFING"}
                </button>
              </div>
            ) : (
              <div className="py-10 text-center text-slate-500 text-xs italic">
                Select an incident from Live Threat Feed or click "Run Multi-API Audit" to evaluate network signals.
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
                {liveEvents.map((event) => (
                  <div key={event.id} className="relative pl-6 space-y-1.5">
                    <div className="absolute -left-4.25 top-1 w-3 h-3 rounded-full bg-slate-950 border-2 border-cyan-400" />
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-cyan-400">{event.type.toUpperCase()}</span>
                      <span className="text-[10px] bg-slate-950 text-slate-400 px-2 py-0.5 rounded border border-slate-800">{event.stage ?? "event"}</span>
                    </div>
                    <div className="bg-slate-950 border border-slate-800 rounded p-2.5 text-[11px] space-y-1">
                      <p className="text-slate-300">{event.message}</p>
                      {event.detail && <p className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-900">{event.detail}</p>}
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
                <div className="text-[10px] uppercase text-slate-500 mb-2">Structured Evidence Trail</div>
                <div className="space-y-2">
                  {auditResult.agent_trace.map((step, idx) => (
                    <div key={`${step.agent}-${idx}`} className="bg-slate-950 border border-slate-800 rounded p-2.5 text-[11px]">
                      <div className="flex items-center justify-between">
                        <span className="text-cyan-400 font-bold">{step.agent}</span>
                        <span className="text-[10px] text-slate-500">{step.status}</span>
                      </div>
                      <p className="text-slate-300 mt-1">{step.thought}</p>
                      <p className="text-[10px] text-slate-500 mt-1 font-mono">{step.detail}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}